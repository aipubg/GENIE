"""Repeatable smoke test for the native Windows client (PHASE W2+).

Proves, against a real launch:

    1. the native client starts
    2. it auto-starts the embedded backend (health answers 200)
    3. it spawns NO console process (no conhost/cmd child) - the §18/§41 gate
    4. a graceful window close stops everything it owns
    5. after close: no GENIE processes remain and the backend is down

Usage:
    python scripts/verify_native_client.py [--exe PATH] [--wait 40]

Requires psutil in the interpreter used.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import pathlib
import subprocess
import sys
import time
import urllib.request

HEALTH = "http://127.0.0.1:8787/health"
WM_CLOSE = 0x0010


def health_ok(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def genie_pids() -> set:
    import psutil
    return {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() in ("genie.desktop.exe", "pythonw.exe")}


def close_windows_of(pid: int) -> int:
    """Post WM_CLOSE to the process's visible windows - a real graceful close."""
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    found = []

    def cb(h, _l):
        proc = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(proc))
        if proc.value == pid and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    for h in found:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return len(found)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--wait", type=float, default=40.0)
    args = ap.parse_args()

    exe = pathlib.Path(args.exe)
    if not exe.exists():
        print(f"SKIP: {exe} not found")
        return 2

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        deadline = time.time() + args.wait
        up = False
        while time.time() < deadline:
            if health_ok():
                up = True
                break
            time.sleep(1)
        check("native client starts and backend answers /health", up)
        if not up:
            return 1

        check("backend processes are running", len(genie_pids()) >= 2,
              f"{len(genie_pids())} processes")

        # No console process may be parented by GENIE or its backend.
        import psutil
        owned = genie_pids()
        consoles = [p.info for p in psutil.process_iter(["pid", "ppid", "name"])
                    if (p.info["name"] or "").lower() == "conhost.exe"
                    and p.info["ppid"] in owned]
        check("no console process spawned by GENIE", len(consoles) == 0, consoles)

        closed = close_windows_of(proc.pid)
        check("a window was open to close", closed > 0, f"{closed} window(s)")

        gone_deadline = time.time() + 30
        while time.time() < gone_deadline:
            if not genie_pids() and not health_ok():
                break
            time.sleep(1)

        check("no GENIE processes remain after close", len(genie_pids()) == 0,
              genie_pids())
        check("backend stopped after close", not health_ok())
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    passed = sum(1 for _, ok in results if ok)
    print(f"\nRESULT {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
