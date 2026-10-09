"""Browser mode resolution and owner-browser detection (browser/mode.py).

Phase 1 corrective pass — two authorized browser modes (owner-clarified):

  MODE_OWNER_EXISTING
      Drive the owner's ALREADY-RUNNING, signed-in browser. GENIE attaches over
      CDP when that browser exposes a remote-debugging port. GENIE NEVER
      force-closes, relaunches, copies credentials from, or switches the profile
      of the owner's browser. If the running browser has no debug port,
      attachment is impossible and GENIE reports the exact owner action.

  MODE_GENIE_OWNED
      Drive a SEPARATE, GENIE-launched browser/profile. Used when the owner
      explicitly asks GENIE to work on its own, or as the honest fallback after
      the owner declines to enable attachment. This profile is logged out until
      the owner signs in themselves.

Browser identity safety (corrective pass, 2026-09-22)
-----------------------------------------------------
Attaching to the wrong browser would be a correctness AND security defect. So
before any `owner_existing` attachment GENIE must CONFIRM, from reliable Windows
process information, that the CDP endpoint actually belongs to the owner's
browser:

  * The listening debug port's OWNING PROCESS is identified via
    `Get-NetTCPConnection` (PowerShell CIM) — not guessed from reachability.
  * The owning process's executable, PID, `--remote-debugging-port` and
    `--user-data-dir` are all checked before classifying it as the owner's Brave.
  * A GENIE-owned background profile (marker: user-data-dir basename
    `browser-profile`) is NEVER mistaken for the owner's browser.
  * An arbitrary reachable debug port is NEVER treated as the owner's browser.
    If identity cannot be confirmed, GENIE stops and reports
    `needs_owner_action` WITHOUT navigating or modifying that browser.
  * Identity is NEVER inferred from a website's login state, the browser's
    User-Agent, or the presence of GENIE fixture tabs.

This module is PURE detection + decision. It launches nothing and closes
nothing. The process lister, the CDP reachability probe and the port->PID
resolver are all injectable so the logic is fully testable without a real browser.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.logging_setup import get_logger
from .cdp import browser_ready

log = get_logger("browser.mode")

MODE_OWNER_EXISTING = "owner_existing"
MODE_GENIE_OWNED = "genie_owned"

# Browser key -> process image names to look for.
_PROC_NAMES = {
    "brave": {"brave.exe"},
    "chrome": {"chrome.exe"},
    "edge": {"msedge.exe"},
    "chromium": {"chrome.exe", "chromium.exe"},
}

# Sites whose value depends on the owner's signed-in session. When the owner
# names a browser for one of these, the intended mode is their EXISTING browser.
_AUTH_SITES = {"chatgpt", "openai", "gmail"}

# GENIE-owned background browsing profile marker. A browser launched by GENIE
# itself uses a user-data-dir whose final component is this name. It must never
# be mistaken for the owner's signed-in browser.
_GENIE_PROFILE_MARKER = "browser-profile"

# Phrases that mean "your own / separate GENIE browser" (explicit Mode 2).
_OWN_BROWSER_RE = re.compile(
    r"\b(your own browser|apna browser|genie'?s browser|genie browser|"
    r"separate browser|alag browser|own profile|apna profile|separate profile|"
    r"your profile|on your own|independently|apne aap)\b", re.I)

# Phrases that mean the owner's EXISTING browser (explicit Mode 1).
_MY_BROWSER_RE = re.compile(
    r"\b(my brave|mera brave|apna brave|my chrome|mera chrome|my browser|"
    r"mera browser|mere browser|hamara browser|hamare browser|humare browser|our browser|apna browser|existing brave|existing browser|"
    r"mere brave|my signed[- ]?in brave)\b", re.I)

# Standard debug ports GENIE checks for, in priority order, when composing an
# honest "unidentifiable endpoint" message. These are READ-ONLY reachability
# probes used only to phrase the message — GENIE never navigates or attaches.
_STD_DEBUG_PORTS = (9222, 9229, 9333)


def _first_arg(cmdline: str, flag: str) -> Optional[str]:
    """Return the value after `flag` from a command line.

    The value may be quoted (``--user-data-dir="C:\\...\\User Data"``) or bare
    (``--remote-debugging-port=9222``). Handles both, so a path containing a
    space is not truncated at the first space.
    """
    if not cmdline:
        return None
    m = re.search(re.escape(flag) + r"=?\s*(\"?)(.*?)\1(?=\s|$)", cmdline, re.I)
    return m.group(2) if m else None


def _first_token(cmdline: str) -> str:
    if not cmdline:
        return ""
    s = cmdline.strip()
    if s.startswith('"') and '"' in s[1:]:
        return s[1:].split('"', 1)[0]
    return s.split()[0] if s.split() else ""


def _running_browser_key(cmdline: str) -> Optional[str]:
    low = (cmdline or "").lower()
    for key, names in _PROC_NAMES.items():
        if any(n in low for n in names):
            return key
    return None


def _looks_genie_owned(cmdline: str) -> bool:
    """True when the process's user-data-dir is GENIE's own background profile.

    Used to keep GENIE-owned background browsing strictly separate from the
    owner's signed-in browser (requirement: never substitute a GENIE-owned
    profile for the owner's, and never disturb the owner's active browser).
    """
    ud = _first_arg(cmdline or "", "--user-data-dir") or ""
    if not ud:
        return False
    return bool(re.fullmatch(re.escape(_GENIE_PROFILE_MARKER) + r"(?:-\d+)?", Path(ud).name.lower()))


def _create_flags() -> int:
    # 0x08000000 CREATE_NO_WINDOW, 0x00000008 DETACHED_PROCESS — keep the spawned
    # PowerShell/wmic helper windowless on Windows.
    return 0x00000008 | 0x08000000


# --------------------------------------------------------- reliable listers

def _list_via_powershell_cim() -> Optional[List[Dict[str, str]]]:
    """Enumerate Chromium-family browser processes via PowerShell CIM.

    `Get-CimInstance Win32_Process` is the reliable way to obtain, for every
    browser process, its `ExecutablePath`, `ProcessId` and `CommandLine`. The
    obsolete `wmic` executable is NOT used here.

    Returns a list (possibly empty) on success, or None when PowerShell is
    unavailable / enumeration failed — in which case the caller falls through to
    the wmic fallback. Never kills or relaunches anything.
    """
    ps_script = (
        "$items = @(Get-CimInstance Win32_Process -Filter "
        "\"Name='brave.exe' OR Name='chrome.exe' OR Name='msedge.exe' OR "
        "Name='chromium.exe'\");"
        "$objs = $items | ForEach-Object { [pscustomobject]@{"
        "pid=[string]$_.ProcessId; exe=$_.ExecutablePath; cmdline=$_.CommandLine} };"
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "ConvertTo-Json -InputObject @($objs) -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=30, creationflags=_create_flags())
    except (OSError, ValueError) as exc:
        log.debug("powershell CIM listing unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("powershell CIM listing failed: %s", exc)
        return None
    if proc.returncode != 0:
        log.debug("powershell CIM listing exited %s", proc.returncode)
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("powershell CIM JSON parse failed: %s", exc)
        return None
    if isinstance(data, dict):
        data = [data]
    out: List[Dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        out.append({
            "pid": str(row.get("pid") or "").strip(),
            "exe": (row.get("exe") or "") or "",
            "cmdline": (row.get("cmdline") or "") or "",
        })
    return out


def _list_via_wmic() -> Optional[List[Dict[str, str]]]:
    """Deprecated fallback lister. `wmic` is removed/unavailable on many systems.

    Returns [{pid, exe, cmdline}] with `exe` left empty (it is derived later from
    the command-line's first token). Returns None when wmic is unavailable.
    """
    try:
        proc = subprocess.run(
            ["wmic", "path", "win32_process",
             "where", "name='brave.exe' or name='chrome.exe' or name='msedge.exe' "
                      "or name='chromium.exe'",
             "get", "CommandLine,ProcessId", "/format:list"],
            capture_output=True, text=True, timeout=20, creationflags=_create_flags())
        data = proc.stdout or ""
    except (OSError, ValueError) as exc:
        log.debug("wmic listing unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("wmic listing failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    out: List[Dict[str, str]] = []
    pid = ""
    cmd = ""
    for line in data.splitlines():
        line = line.strip()
        if not line:
            if pid or cmd:
                out.append({"pid": pid, "exe": "", "cmdline": cmd})
            pid, cmd = "", ""
            continue
        if line.lower().startswith("processid="):
            pid = line.split("=", 1)[1].strip()
        elif line.lower().startswith("commandline="):
            cmd = line.split("=", 1)[1].strip()
    if pid or cmd:
        out.append({"pid": pid, "exe": "", "cmdline": cmd})
    return out


def _default_process_lister() -> List[Dict[str, str]]:
    """Return running Chromium-family browser processes as
    [{pid, exe, cmdline}]. Windows-only; reads process info ONLY — never kills or
    relaunches anything. Uses PowerShell CIM, falling back to the deprecated `wmic`
    executable. Returns [] elsewhere / on any failure.
    """
    if sys.platform != "win32":
        return []
    procs = _list_via_powershell_cim()
    if procs is not None:
        return procs
    procs = _list_via_wmic()
    if procs is not None:
        return procs
    return []


def _default_port_owner(port: int) -> Optional[int]:
    """Return the PID that OWNS a LISTENING localhost TCP port, via PowerShell
    `Get-NetTCPConnection`. This is the authoritative way to confirm which process
    a CDP endpoint actually belongs to: the executable name alone is not enough
    (an unrelated Chrome could be holding port 9222).

    Returns None when not Windows, when the port is not listening, or on failure.
    """
    if sys.platform != "win32":
        return None
    ps_script = (
        "$c = Get-NetTCPConnection -LocalPort %d -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 "
        "-ExpandProperty OwningProcess; if ($c) { [string]$c }" % int(port)
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15, creationflags=_create_flags())
        raw = (proc.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    def _w(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:  # noqa: BLE001
            return None
    return _w


def _port_reachable(port: int) -> bool:
    try:
        browser_ready(int(port), timeout_s=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def detect_owner_browser(name: str, *,
                         lister: Optional[Callable[[], List[Dict[str, str]]]] = None,
                         reach_probe: Optional[Callable[[int], bool]] = None,
                         port_owner: Optional[Callable[[int], Optional[int]]] = None) -> Dict[str, Any]:
    """Inspect the owner's running browser named `name` and decide whether GENIE
    may attach over CDP — WITHOUT ever navigating or modifying that browser.

    Identity is verified from reliable process information: the listening debug
    port's owning PID must match the detected browser process, and a GENIE-owned
    background profile is never mistaken for the owner's. If identity cannot be
    confirmed, the result reports `needs_owner_action` (via `attachable=False` +
    `owner_action`) and GENIE attaches to NOTHING.

    Returns one of:
      {"found": False}
      {"found": True, "running": True, "attachable": False,
       "exe", "pid", "user_data_dir", "debug_port": None,
       "reason": "...", "owner_action": "<exact instruction>"}
      {"found": True, "running": True, "attachable": False, "genie_owned_conflict": True,
       "exe", "pid", "user_data_dir", "debug_port", "owner_action": "..."}
      {"found": True, "running": False, "attachable": False, "unidentifiable": True,
       "debug_port", "owner_action": "..."}
      {"found": True, "running": True, "attachable": True,
       "exe", "pid", "user_data_dir", "debug_port": <int>}

    `lister`, `reach_probe` and `port_owner` are injectable for deterministic
    tests. In real runtime they default to PowerShell-CIM enumeration, a live CDP
    reachability probe, and the PowerShell `Get-NetTCPConnection` port->PID resolver.
    The port-ownership cross-check runs only when `port_owner` is supplied (it is,
    in real runtime) so un-injected unit tests stay deterministic.
    """
    lister = lister or _default_process_lister
    probe = _safe(reach_probe or _port_reachable)
    resolve_owner = _safe(port_owner) if port_owner is not None else None
    key = (name or "").strip().lower()
    if not key:
        return {"found": False}

    best: Dict[str, Any] = {}
    genie_conflict: Dict[str, Any] = {}
    port_conflict: Dict[str, Any] = {}

    for proc in lister() or []:
        cmdline = proc.get("cmdline", "")
        bk = _running_browser_key(cmdline)
        if bk != key:
            continue
        ud = _first_arg(cmdline, "--user-data-dir")
        port = _first_arg(cmdline, "--remote-debugging-port")
        port_i = int(port) if port and port.isdigit() else None
        pid_i = int(proc.get("pid") or 0)
        exe = proc.get("exe") or _first_token(cmdline)
        info: Dict[str, Any] = {
            "found": True, "running": True, "attachable": False,
            "exe": exe, "pid": pid_i,
            "user_data_dir": ud, "debug_port": port_i,
        }

        # A GENIE-owned background profile is NOT the owner's signed-in browser.
        if _looks_genie_owned(cmdline):
            info["genie_owned_conflict"] = True
            info["reason"] = ("this Brave instance uses GENIE's own background profile "
                              f"({_GENIE_PROFILE_MARKER}), not your signed-in profile")
            if not genie_conflict:
                genie_conflict = info
            continue

        if port_i is None:
            info["reason"] = "running without a remote-debugging port"
            if not best:
                best = info
            continue

        # A debug port is declared. Confirm it is actually reachable AND owned by
        # THIS process (never assume a reachable port belongs to the owner).
        if probe(port_i):
            owning_pid = resolve_owner(port_i) if resolve_owner is not None else None
            if resolve_owner is None or owning_pid == pid_i:
                info["attachable"] = True
                return info
            info["reason"] = (f"debug port {port_i} is reachable but owned by a different "
                              f"process (pid {owning_pid}), not this {key} (pid {pid_i})")
            if not port_conflict:
                port_conflict = info
        else:
            info["reason"] = f"declares --remote-debugging-port={port_i} but the port is not reachable"
            if not best:
                best = info

    if best:
        best["owner_action"] = (
            f"Your {key} is running but without a reachable remote-debugging port, so GENIE "
            f"cannot attach to your signed-in session over CDP without relaunching it (which "
            f"it is not allowed to do). To let GENIE use your signed-in {key}, close {key} and "
            f"relaunch it once from a shortcut whose target ends with "
            f"' --remote-debugging-port=9222', then ask again. Alternatively say 'use your own "
            f"browser' and GENIE will open a separate {key} profile (logged out — you sign in "
            f"there yourself).")
        return best

    if genie_conflict:
        genie_conflict["owner_action"] = (
            f"A Brave process is running, but it is GENIE's own background profile "
            f"({_GENIE_PROFILE_MARKER}), not your signed-in {key}. GENIE will not treat its own "
            f"profile as your browser. To let GENIE use your signed-in {key}, close {key} and "
            f"relaunch it once from a shortcut ending in ' --remote-debugging-port=9222', or say "
            f"'use your own browser' for a separate logged-out {key} profile.")
        return genie_conflict

    if port_conflict:
        port_conflict["owner_action"] = (
            f"A {key} process declares a debug port, but that port is owned by a DIFFERENT "
            f"process, so GENIE cannot safely confirm it is your browser. GENIE will not attach "
            f"to or modify that browser. If you want GENIE to use your signed-in {key}, close the "
            f"conflicting browser/program and relaunch {key} from a shortcut ending in "
            f"' --remote-debugging-port=9222', then ask again. Or say 'use your own browser'.")
        return port_conflict

    # No matching owner browser process was found. Probe the standard debug ports
    # READ-ONLY, purely to compose an honest message. GENIE never navigates or
    # attaches to an arbitrary reachable endpoint it cannot identify.
    for cand in _STD_DEBUG_PORTS:
        if probe(cand):
            return {"found": True, "running": False, "attachable": False,
                    "unidentifiable": True, "debug_port": cand,
                    "owner_action": (
                        f"A Chromium debugging port ({cand}) is reachable, but GENIE cannot "
                        f"confirm it belongs to your signed-in {key} (no matching {key} process "
                        f"was found, or the port is owned by another process). GENIE will NOT "
                        f"attach to or modify that browser. To let GENIE use your signed-in {key}, "
                        f"close {key} and relaunch it from a shortcut whose target ends with "
                        f"' --remote-debugging-port={cand}', then ask again. Or say 'use your own "
                        f"browser' for a separate logged-out {key} profile.")}
    return {"found": False}


def decide_browser_mode(text: str, browser: str, service: str) -> str:
    """Decide the authorized mode for a web request.

    A named browser means the owner's session unless a separate profile was
    explicitly requested. Site names must not silently change profile identity.
    """
    t = (text or "").lower()
    if _OWN_BROWSER_RE.search(t):
        return MODE_GENIE_OWNED
    if _MY_BROWSER_RE.search(t):
        return MODE_OWNER_EXISTING
    if not browser:
        return MODE_GENIE_OWNED
    return MODE_OWNER_EXISTING
