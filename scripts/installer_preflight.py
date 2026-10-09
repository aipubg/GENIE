#!/usr/bin/env python3
"""GENIE installer preflight — find out WHO is blocking an install/upgrade.

Why this exists
---------------
The owner hit this while upgrading over a running GENIE:

    "GENIE cannot be closed. Please close it manually and click Retry."
    "Failed to uninstall old application files ... :2"

An NSIS dialog cannot tell you *which* process holds the files, so this script
answers the question directly:

  * every process whose image lives in the install directory (or that is
    GENIE.exe from anywhere), with PID, parent PID, executable path and the
    full command line;
  * whether that process has a window at all — a process with no window is one
    that survives closing the GENIE window and is the usual silent blocker;
  * which files inside the install directory are actually locked right now
    (opened exclusively, i.e. mapped/held by a running process).

Hard rules
----------
  * Read-only by default. It never kills anything unless you explicitly pass
    --force-kill --yes.
  * Prefer --request-shutdown, which asks GENIE to perform its own canonical
    graceful exit (GENIE.exe --genie-shutdown) and then waits for it.

Usage
-----
  python scripts/installer_preflight.py --install-dir "C:\\Path\\To\\GENIE"
  python scripts/installer_preflight.py --install-dir "..." --request-shutdown
  python scripts/installer_preflight.py --install-dir "..." --json report.json

Exit codes
----------
  0 = nothing is blocking
  2 = one or more processes/files are blocking
  3 = the preflight itself could not run
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

APP_EXE_NAMES = {"GENIE.exe", "genie.exe"}
# How long to wait, in seconds, for a graceful shutdown to complete.
DEFAULT_WAIT = 20

GENERIC_READ = 0x80000000
FILE_SHARE_NONE = 0x00000000
OPEN_EXISTING = 3
ERROR_SHARING_VIOLATION = 32
ERROR_ACCESS_DENIED = 5
ERROR_LOCK_VIOLATION = 33


# --------------------------------------------------------------------------- #
# process inventory
# --------------------------------------------------------------------------- #
def _powershell_json(script: str) -> Optional[List[Dict[str, Any]]]:
    """Run a PowerShell snippet and parse its ConvertTo-Json output."""
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='SilentlyContinue'; " + script,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    stdout = out.stdout or ""
    if out.returncode != 0 and not stdout.strip():
        return None
    text = stdout.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return None


def list_processes() -> List[Dict[str, Any]]:
    """Every WIN32 process with the fields we need, via PowerShell CIM."""
    script = (
        "@(Get-CimInstance Win32_Process | ForEach-Object { "
        "$p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ "
        "pid = $_.ProcessId; parent = $_.ParentProcessId; name = $_.Name; "
        "exe = $_.ExecutablePath; cmd = $_.CommandLine; "
        "hasWindow = [bool]($p -and $p.MainWindowHandle -ne 0); "
        "title = $(if ($p) { $p.MainWindowTitle } else { '' }) "
        "} }) | ConvertTo-Json -Depth 3"
    )
    rows = _powershell_json(script)
    if rows is None:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "pid": r.get("pid"),
            "parent_pid": r.get("parent"),
            "name": r.get("name") or "",
            "exe": r.get("exe") or "",
            "cmdline": r.get("cmd") or "",
            "has_window": bool(r.get("hasWindow")),
            "window_title": r.get("title") or "",
        })
    return out


def relevant_processes(procs: List[Dict[str, Any]], install_dir: str) -> List[Dict[str, Any]]:
    """Processes that can hold files in `install_dir`, or any GENIE.exe."""
    target = os.path.normcase(os.path.abspath(install_dir)) if install_dir else ""
    hits = []
    for p in procs:
        exe = (p.get("exe") or "")
        if p.get("name") in APP_EXE_NAMES:
            hits.append(p)
            continue
        if target and exe:
            try:
                if os.path.normcase(os.path.abspath(exe)).startswith(target):
                    hits.append(p)
            except (OSError, ValueError):
                continue
    return hits


# --------------------------------------------------------------------------- #
# file locks
# --------------------------------------------------------------------------- #
def _is_locked(path: str) -> bool:
    """True when the file cannot be opened exclusively (someone holds it).

    Uses CreateFileW with dwShareMode=0. A mapped executable / DLL is shared by
    the running image, so an exclusive open fails with a sharing violation —
    which is exactly the condition that makes NSIS fail to replace files.
    """
    if not os.path.isfile(path):
        return False
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        ctypes.c_wchar_p(path),
        ctypes.c_uint(GENERIC_READ),
        ctypes.c_uint(FILE_SHARE_NONE),
        None,
        ctypes.c_uint(OPEN_EXISTING),
        ctypes.c_uint(0),
        None,
    )
    if handle is None:
        return True
    err = ctypes.windll.kernel32.GetLastError()
    if handle == ctypes.c_void_p(-1).value or handle == 0:
        return err in (ERROR_SHARING_VIOLATION, ERROR_ACCESS_DENIED, ERROR_LOCK_VIOLATION)
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    return False


def locked_files(install_dir: str, limit: int = 200) -> List[str]:
    """Best-effort probe for files under `install_dir` that cannot be opened.

    HONESTY NOTE: this probe is known to under-report. Windows maps a running
    executable as an image section, and on some systems (including the one this
    was verified on) CreateFileW succeeds even against a process that is
    definitely running. So an empty result here does NOT prove nothing is
    locked — it only proves the probe found nothing. The blocking verdict is
    therefore based on the process inventory, which is verified, not on this.
    """
    if not install_dir or not os.path.isdir(install_dir):
        return []
    interesting = (".exe", ".dll", ".node", ".pak", ".bin", ".dat")
    checked = 0
    locked: List[str] = []
    for root, _dirs, files in os.walk(install_dir):
        for name in files:
            if not name.lower().endswith(interesting):
                continue
            if checked >= limit:
                return locked
            checked += 1
            path = os.path.join(root, name)
            try:
                if _is_locked(path):
                    locked.append(path)
            except (OSError, ValueError):
                continue
    return locked


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def find_app_exe(install_dir: str) -> Optional[str]:
    """The GENIE.exe inside the install directory, if present."""
    if not install_dir:
        return None
    for name in ("GENIE.exe", "genie.exe"):
        candidate = os.path.join(install_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def request_shutdown(exe: str, wait: int = DEFAULT_WAIT) -> bool:
    """Ask the running GENIE to close itself, then wait for it to disappear.

    This is a request, not a kill: GENIE.exe --genie-shutdown is relayed through
    the single-instance lock to the running instance, which performs its own
    canonical full exit.
    """
    try:
        subprocess.Popen([exe, "--genie-shutdown"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False

    deadline = time.time() + max(1, wait)
    while time.time() < deadline:
        time.sleep(1.0)
        still = [p for p in list_processes()
                 if (p.get("exe") or "").lower() == exe.lower()
                 or p.get("name") in APP_EXE_NAMES]
        if not still:
            return True
    return False


def force_kill(pids: List[int]) -> List[int]:
    """Last resort. Explicitly requested only."""
    killed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                           capture_output=True, timeout=30)
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def render(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("GENIE installer preflight")
    lines.append("=" * 62)
    lines.append(f"install dir : {report['install_dir'] or '(not given)'}")
    lines.append(f"app exe     : {report['app_exe'] or '(not found)'}")
    lines.append(f"generated   : {report['generated']}")
    lines.append("")

    procs = report["processes"]
    lines.append(f"GENIE processes: {len(procs)}")
    if procs:
        lines.append("")
        for p in procs:
            survives = "YES (no window)" if not p["has_window"] else "no"
            lines.append(f"  PID {p['pid']}  parent {p['parent_pid']}  {p['name']}")
            lines.append(f"      exe    : {p['exe'] or '(unknown)'}")
            cmd = (p["cmdline"] or "").strip()
            lines.append(f"      cmd    : {cmd[:160] if cmd else '(unknown)'}")
            lines.append(f"      window : {p['window_title'] or '(none)'}")
            lines.append(f"      survives window close: {survives}")
    else:
        lines.append("  none — no GENIE process is running.")
    lines.append("")

    locks = report["locked_files"]
    lines.append(f"Locked files (best-effort probe): {len(locks)}")
    lines.append("  This probe can UNDER-REPORT: Windows may still allow an open")
    lines.append("  against a running image. An empty list is not proof of no locks.")
    for f in locks[:25]:
        lines.append(f"  {f}")
    if len(locks) > 25:
        lines.append(f"  ... and {len(locks) - 25} more")
    lines.append("")

    if report["blocked"]:
        lines.append("RESULT: BLOCKED — an install/upgrade would fail right now.")
        pids = ", ".join(str(p["pid"]) for p in procs)
        if pids:
            lines.append(f"        Blocking PID(s): {pids}")
        lines.append("        Fix: re-run with --request-shutdown (graceful), or close")
        lines.append("        GENIE from the tray icon -> 'Exit GENIE'.")
    else:
        lines.append("RESULT: CLEAR — nothing is holding the install directory.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GENIE installer preflight")
    ap.add_argument("--install-dir", default="",
                    help="Directory GENIE is installed into (or will be installed into).")
    ap.add_argument("--json", dest="json_out", default="",
                    help="Write the machine-readable report to this path.")
    ap.add_argument("--request-shutdown", action="store_true",
                    help="Ask a running GENIE to exit gracefully, then re-check.")
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT,
                    help=f"Seconds to wait for graceful shutdown (default {DEFAULT_WAIT}).")
    ap.add_argument("--force-kill", action="store_true",
                    help="Kill blocking processes. Requires --yes. NOT the default.")
    ap.add_argument("--yes", action="store_true",
                    help="Confirm a destructive action (--force-kill).")
    args = ap.parse_args(argv)

    if sys.platform != "win32":
        print("installer_preflight is Windows-only (it reads WIN32 process data).")
        return 3

    install_dir = os.path.abspath(args.install_dir) if args.install_dir else ""
    if install_dir and not os.path.isdir(install_dir):
        print(f"install dir does not exist yet: {install_dir}")
        print("Nothing can be holding files there; treating as CLEAR.")
        install_dir = ""

    procs = relevant_processes(list_processes(), install_dir)
    app_exe = find_app_exe(install_dir)

    shutdown_ok = None
    if args.request_shutdown:
        if not app_exe:
            print("Cannot request shutdown: no GENIE.exe found in the install dir.")
        else:
            shutdown_ok = request_shutdown(app_exe, args.wait)
            print(f"graceful shutdown request: {'succeeded' if shutdown_ok else 'timed out'}")
            procs = relevant_processes(list_processes(), install_dir)

    killed: List[int] = []
    if args.force_kill:
        if not args.yes:
            print("Refusing to kill: --force-kill requires --yes. "
                  "GENIE never force-kills a running session by default.")
            return 3
        killed = force_kill([p["pid"] for p in procs if p.get("pid")])
        print(f"force-killed PID(s): {killed or 'none'}")
        procs = relevant_processes(list_processes(), install_dir)

    locks = locked_files(install_dir) if install_dir else []
    # Verdict is driven by the process inventory, which is verified. The lock
    # probe is advisory only (see locked_files docstring).
    blocked = bool(procs)

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "install_dir": install_dir,
        "app_exe": app_exe,
        "processes": procs,
        "locked_files": locks,
        "locked_files_note": "best-effort; may under-report. Verdict uses processes.",
        "blocked": blocked,
        "shutdown_requested": bool(args.request_shutdown),
        "shutdown_ok": shutdown_ok,
        "force_killed": killed,
    }

    print(render(report))

    if args.json_out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".", exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2)
            print(f"\nJSON report written to {args.json_out}")
        except OSError as exc:
            print(f"could not write JSON report: {exc}", file=sys.stderr)

    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
