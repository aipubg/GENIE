"""Probe: can native Win32 enumeration match `tasklist` coverage?

Two native strategies are measured against tasklist as ground truth:

  A) NtQuerySystemInformation(SystemProcessInformation)
     -> returns ImageName + PID for EVERY process, no per-process open.
  B) EnumProcesses + OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)
     + QueryFullProcessImageNameW
     -> the combination the earlier rejected attempt lacked. Fails only for
        processes whose DACL denies even limited-info access.

Run repeatedly: process sets churn, so a single sample proves nothing.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SystemProcessInformation = 5

# ---------------------------------------------------------------------------
# Strategy A: NtQuerySystemInformation(SystemProcessInformation)
# ---------------------------------------------------------------------------
# x64 SYSTEM_PROCESS_INFORMATION field offsets, stable from Windows 7 to 11:
#   0   ULONG           NextEntryOffset
#   4   ULONG           NumberOfThreads
#   8   ULONGLONG       WorkingSetPrivateSize
#   16  ULONG           HardFaultCount
#   20  ULONG           NumberOfThreadsHighWatermark
#   24  ULONGLONG       CycleTime
#   32  LARGE_INTEGER   CreateTime
#   40  LARGE_INTEGER   UserTime
#   48  LARGE_INTEGER   KernelTime
#   56  UNICODE_STRING  ImageName      (16 bytes on x64)
#   72  LONG            BasePriority
#   80  HANDLE          UniqueProcessId
_OFF_IMAGE_NAME = 56
_OFF_PID = 80


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


def _basename(path: str) -> str:
    if not path:
        return ""
    return path.replace("/", "\\").rsplit("\\", 1)[-1]


def native_ntquery() -> dict[int, str]:
    """PID -> image basename, for every process the kernel knows about."""
    size = 1 << 20  # 1 MiB; grown on STATUS_INFO_LENGTH_MISMATCH
    for _ in range(8):
        buf = ctypes.create_string_buffer(size)
        need = wintypes.ULONG(0)
        status = ntdll.NtQuerySystemInformation(
            SystemProcessInformation, buf, size, ctypes.byref(need))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, need.value + (1 << 16))
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")
        break
    else:
        raise OSError("NtQuerySystemInformation: buffer never large enough")

    out: dict[int, str] = {}
    base = ctypes.addressof(buf)
    offset = 0
    while True:
        entry = base + offset
        name = ctypes.cast(entry + _OFF_IMAGE_NAME, ctypes.POINTER(UNICODE_STRING)).contents
        if name.Buffer and name.Length:
            raw = ctypes.string_at(name.Buffer, name.Length)
            out[int(ctypes.c_uint64.from_address(entry + _OFF_PID).value)] = \
                _basename(raw.decode("utf-16-le", "replace"))
        else:
            out[int(ctypes.c_uint64.from_address(entry + _OFF_PID).value)] = ""
        nxt = ctypes.c_uint32.from_address(entry).value
        if nxt == 0:
            break
        offset += nxt
    return out


# ---------------------------------------------------------------------------
# Strategy B: EnumProcesses + OpenProcess(QUERY_LIMITED) + QueryFullProcessImageNameW
# ---------------------------------------------------------------------------
def native_enumprocesses() -> dict[int, str]:
    # kernel32 exports these under K32* names; plain "EnumProcesses" is not an
    # export (it lives in Psapi.dll). Using the wrong symbol is what produced
    # "function not found" earlier.
    enum_procs = kernel32.K32EnumProcesses
    enum_procs.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD)]
    enum_procs.restype = wintypes.BOOL
    query_name = kernel32.QueryFullProcessImageNameW
    query_name.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                           wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query_name.restype = wintypes.BOOL

    arr = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD(0)
    if not enum_procs(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        raise OSError(f"K32EnumProcesses failed: {ctypes.get_last_error()}")
    count = needed.value // ctypes.sizeof(wintypes.DWORD)

    out: dict[int, str] = {}
    for i in range(count):
        pid = int(arr[i])
        if pid == 0:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            out[pid] = ""      # denied -> kept, unnamed
            continue
        try:
            cap = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(cap.value)
            if query_name(h, 0, path, ctypes.byref(cap)):
                out[pid] = _basename(path.value)
            else:
                out[pid] = ""
        finally:
            kernel32.CloseHandle(h)
    return out


def via_tasklist() -> dict[int, str]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, timeout=20,
                         creationflags=0x08000000)
    rows: dict[int, str] = {}
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            rows[int(parts[1])] = parts[0]
    return rows


def compare(label: str, native: dict[int, str], truth: dict[int, str]) -> None:
    n_pids, t_pids = set(native), set(truth)
    missing = t_pids - n_pids                 # truth has it, native dropped it
    unnamed = {p for p in n_pids & t_pids if not native[p]}
    named_ok = sum(1 for p in n_pids & t_pids
                   if native[p].lower() == truth[p].lower())
    print(f"  {label:<22} rows={len(native):>4}  tasklist={len(truth):>4}  "
          f"missing={len(missing):>3}  unnamed={len(unnamed):>3}  "
          f"name-match={named_ok:>4}")
    if missing:
        sample = sorted(missing)[:8]
        print(f"      missing pids: {sample} "
              f"-> {[truth[p] for p in sample]}")
    if unnamed:
        sample = sorted(unnamed)[:8]
        print(f"      unnamed pids: {sample} "
              f"-> {[truth[p] for p in sample]}")


def main() -> int:
    if not sys.platform.startswith("win"):
        print("windows only")
        return 1
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for r in range(1, rounds + 1):
        print(f"round {r}:")
        truth = via_tasklist()
        try:
            compare("A NtQuerySystemInform", native_ntquery(), truth)
        except Exception as exc:
            print(f"  A NtQuerySystemInform  ERROR: {exc}")
        try:
            compare("B EnumProcesses+QF", native_enumprocesses(), truth)
        except Exception as exc:
            print(f"  B EnumProcesses+QF     ERROR: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
