"""End-to-end proof that a full exit stops the backend, on the INSTALLED build.

scripts/verify_daemon_stop.js proves stopDaemon() in isolation with electron
stubbed. This proves it on the real thing:

    launch the installed GENIE
        -> wait for /health 200 (daemon up, daemon.pid recorded)
        -> ask it to shut itself down (--genie-shutdown)
        -> the frontend is gone, the backend is gone, /health is dead,
           and the recorded pid file is cleared

Launching needs ELECTRON_RUN_AS_NODE stripped, otherwise Electron starts as
plain Node and main.js dies at app.whenReady() - the app looks like it "launched
and quit".

Usage:
    python scripts/verify_full_exit_e2e.py [--exe PATH] [--timeout 90]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

HEALTH = "http://127.0.0.1:8787/health"
DEFAULT_EXE = r"C:\Users\ghostt\AppData\Local\Programs\GENIE\GENIE.exe"
USER_DATA = pathlib.Path(os.path.expandvars(r"%APPDATA%\genie-desktop"))


def health_ok(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def genie_processes() -> list:
    import psutil
    return [(p.info["pid"], p.info["name"]) for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() in ("genie.exe", "pythonw.exe")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    exe = pathlib.Path(args.exe)
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if detail else ""))

    if not exe.exists():
        print(f"SKIP: {exe} not installed")
        return 2

    pid_file = USER_DATA / "daemon.pid"
    if pid_file.exists():
        pid_file.unlink()

    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    # Point 6.7 — isolate the data directory so this acceptance run never writes
    # test state into the owner's production GENIE data.
    env["GENIE_DATA_DIR"] = str(
        pathlib.Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        / "genie-acceptance-data")
    print("launching", exe)
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + args.timeout
    up = False
    while time.time() < deadline:
        if health_ok():
            up = True
            break
        time.sleep(2)
    check("installed app starts and the backend answers /health", up)
    if not up:
        try:
            proc.kill()
        except Exception:
            pass
        return 1

    running = genie_processes()
    check("processes are up while running (frontend + daemon + hosts)",
          len(running) >= 2, f"{len(running)} processes")
    check("daemon pid was recorded", pid_file.exists(),
          pid_file.read_text().strip() if pid_file.exists() else "missing")

    recorded = pid_file.read_text().strip() if pid_file.exists() else None

    print("asking GENIE to shut itself down (--genie-shutdown)")
    subprocess.run([str(exe), "--genie-shutdown"], cwd=str(exe.parent), env=env,
                   capture_output=True, timeout=60)

    gone_deadline = time.time() + 60
    frontend_gone = backend_gone = False
    while time.time() < gone_deadline:
        frontend_gone = not any(n.lower() == "genie.exe" for _, n in genie_processes())
        backend_gone = not health_ok()
        if frontend_gone and backend_gone:
            break
        time.sleep(2)

    check("frontend exited", frontend_gone)
    check("backend stopped (/health no longer answers)", backend_gone)
    check("no GENIE processes remain at all", len(genie_processes()) == 0,
          genie_processes())
    check("recorded pid file cleared", not pid_file.exists(),
          f"was pid {recorded}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
