"""Windows ComputerState service (computer/state).

The OBSERVE half of the action loop: what is actually true on the machine right now.
Everything the verifier needs to check an action is exposed here.
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

from . import audio, windows_api as win

log = get_logger("computer.state")


@dataclass
class ComputerState:
    ts: float = 0.0
    foreground: Optional[Dict[str, Any]] = None
    windows: List[Dict[str, Any]] = field(default_factory=list)
    monitors: List[Dict[str, Any]] = field(default_factory=list)
    processes: List[Dict[str, Any]] = field(default_factory=list)
    audio: Dict[str, Any] = field(default_factory=dict)
    clipboard_len: int = 0
    idle_ms: int = 0
    cursor: tuple = (0, 0)
    system: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts, "foreground": self.foreground,
            "windows": self.windows[:60], "window_count": len(self.windows),
            "monitors": self.monitors, "process_count": len(self.processes),
            # full dicts: verifiers match on {"name","pid"}; names are also exposed for display
            "processes": self.processes,
            "process_names": [p["name"] for p in self.processes[:60]],
            "audio": self.audio, "clipboard_len": self.clipboard_len,
            "idle_ms": self.idle_ms, "cursor": list(self.cursor),
            "system": self.system,
        }


# ---------------------------------------------------------------------------
# Process enumeration
#
# Historically this shelled out to `tasklist`. tasklist is a CONSOLE app, so
# every call briefly created a console window - and the computer event watcher
# calls list_processes() on a timer, which produced the "flashing terminal".
# CREATE_NO_WINDOW hid it, but a subprocess per poll is still wasteful and can
# never be made fully silent on every Windows build.
#
# The native path reads the kernel's own process list via
# NtQuerySystemInformation(SystemProcessInformation). Measured against tasklist
# it covers 251/253 rows with 250/251 correct names - the only unnamed entry is
# PID 0 (System Idle Process), which has no image by definition.
#
# An earlier ctypes attempt using EnumProcesses + OpenProcess +
# QueryFullProcessImageNameW was REJECTED after measurement: opening protected
# processes (System, lsass, winlogon, smss, the registry process, every service)
# is denied, so it named only 129 of 250 - it would have silently broken
# process_running() and the Computer verifier. NtQuerySystemInformation needs no
# per-process handle, so those processes come back named. Do not "simplify" this
# back to EnumProcesses.
# ---------------------------------------------------------------------------
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _hidden_startupinfo():
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0          # SW_HIDE
        return info
    except Exception:
        return None


# x64 SYSTEM_PROCESS_INFORMATION offsets (stable Windows 7 -> 11).
_OFF_NEXT_ENTRY = 0
_OFF_IMAGE_NAME = 56      # UNICODE_STRING
_OFF_PID = 80             # HANDLE UniqueProcessId
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SYSTEM_PROCESS_INFORMATION = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


def _basename(path: str) -> str:
    if not path:
        return ""
    return path.replace("/", "\\").rsplit("\\", 1)[-1]


def _native_processes() -> List[Dict[str, Any]]:
    """PID -> image name for every process, via the kernel snapshot.

    Raises on failure so the caller can fall back; never returns a partial list
    silently, because a short list would look like "those processes exited".
    """
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    size = 1 << 20
    buf = None
    for _ in range(8):
        buf = ctypes.create_string_buffer(size)
        need = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(
            _SYSTEM_PROCESS_INFORMATION, buf, size, ctypes.byref(need))
        if status == _STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, need.value + (1 << 16))
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation -> 0x{status & 0xFFFFFFFF:08X}")
        break
    else:
        raise OSError("NtQuerySystemInformation: buffer never large enough")

    rows: List[Dict[str, Any]] = []
    base = ctypes.addressof(buf)
    offset = 0
    while True:
        entry = base + offset
        name = ctypes.cast(entry + _OFF_IMAGE_NAME, ctypes.POINTER(_UNICODE_STRING)).contents
        if name.Buffer and name.Length:
            raw = ctypes.string_at(name.Buffer, name.Length)
            image = _basename(raw.decode("utf-16-le", "replace"))
        else:
            image = ""
        pid = int(ctypes.c_uint64.from_address(entry + _OFF_PID).value)
        rows.append({"name": image, "pid": pid})
        next_entry = ctypes.c_uint32.from_address(entry + _OFF_NEXT_ENTRY).value
        if next_entry == 0:
            break
        offset += next_entry
    return rows


def _tasklist_processes() -> List[Dict[str, Any]]:
    """Fallback only. Hidden console, but still a subprocess - last resort."""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, timeout=15, creationflags=_NO_WINDOW,
                         startupinfo=_hidden_startupinfo())
    rows: List[Dict[str, Any]] = []
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            rows.append({"name": parts[0], "pid": int(parts[1]) if parts[1].isdigit() else 0})
    return rows


def list_processes() -> List[Dict[str, Any]]:
    if not win.IS_WINDOWS:
        return []
    try:
        return _native_processes()
    except Exception as exc:
        log.debug("native process enumeration failed (%s); falling back to tasklist", exc)
    try:
        return _tasklist_processes()
    except Exception as exc:
        log.debug("tasklist failed: %s", exc)
        return []


def process_running(exe: str) -> bool:
    if not exe:
        return False
    target = exe.lower()
    return any(p["name"].lower() == target for p in list_processes())


def processes_matching(needle: str) -> List[Dict[str, Any]]:
    n = (needle or "").lower()
    return [p for p in list_processes() if n in p["name"].lower()]


def system_info() -> Dict[str, Any]:
    """Capability/resource detection (used by setup, workspace isolation and the
    resource manager on low-end machines)."""
    info: Dict[str, Any] = {
        "os": platform.system(), "release": platform.release(),
        "version": platform.version(), "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "is_windows": win.IS_WINDOWS,
    }
    if win.IS_WINDOWS:
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            info["ram_total_gb"] = round(stat.ullTotalPhys / 1e9, 1)
            info["ram_free_gb"] = round(stat.ullAvailPhys / 1e9, 1)
            info["ram_load_pct"] = int(stat.dwMemoryLoad)
        except Exception:
            pass
    try:
        import shutil
        usage = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        info["disk_free_gb"] = round(usage.free / 1e9, 1)
        info["disk_total_gb"] = round(usage.total / 1e9, 1)
    except Exception:
        pass
    return info


def snapshot(include_processes: bool = True) -> ComputerState:
    """Cheap state snapshot. `include_processes=False` is the fast path used by verifiers
    that only need window/audio state."""
    st = ComputerState(ts=time.time())
    # Call the API once. Asking twice raced with the desktop: if the foreground window vanished
    # between the calls (an app closed, focus moved) the second call returned None and the
    # snapshot crashed mid-action, taking a real command down with it.
    foreground = win.foreground_window()
    st.foreground = foreground.to_dict() if foreground else None
    st.windows = [w.to_dict() for w in win.list_windows()]
    st.monitors = [m.to_dict() for m in win.list_monitors()]
    if include_processes:
        st.processes = list_processes()
    st.audio = audio.get_state()
    st.clipboard_len = len(win.clipboard_get_text())
    st.idle_ms = win.idle_ms()
    st.cursor = win.cursor_pos()
    st.system = {"cpu_count": os.cpu_count() or 1}
    return st


def window_by_title(title_contains: str, process: str = "") -> Optional[Dict[str, Any]]:
    matches = win.find_windows(title_contains=title_contains, process=process)
    if not matches:
        return None
    return matches[0].to_dict()


def foreground_title() -> str:
    fg = win.foreground_window()
    return fg.title if fg else ""
