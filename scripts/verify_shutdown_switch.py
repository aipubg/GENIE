"""Verify `--genie-shutdown` actually closes a running GENIE.

The installer depends on this switch: it must ask a running instance to close
itself rather than force-killing it, because killing leaves the backend running
and files locked (the original "Failed to uninstall old application files").

This exercises the real executables:
  1. start GENIE, wait for the backend
  2. run `Genie.Desktop.exe --genie-shutdown`
  3. the running instance must exit on its own, with no kill
  4. every GENIE process must be gone and the backend stopped
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

EXE = (Path(__file__).resolve().parents[1] / "ui" / "windows" / "Genie.Desktop"
       / "bin" / "Release" / "net8.0-windows" / "win-x64" / "Genie.Desktop.exe")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def health_ok() -> bool:
    try:
        with _opener.open("http://127.0.0.1:8787/health", timeout=2):
            return True
    except Exception:
        return False


def genie_pids() -> set:
    return {p.pid for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() in ("genie.desktop.exe", "pythonw.exe")}


def main() -> int:
    if not EXE.exists():
        print(f"exe not found: {EXE}")
        return 2

    print("1) start GENIE")
    proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    end = time.time() + 60
    while time.time() < end and not health_ok():
        time.sleep(1)
    check("GENIE started and backend healthy", health_ok())
    if not health_ok():
        proc.kill()
        return 1

    before = len(genie_pids())
    check("GENIE processes present before the request", before >= 1, f"{before}")

    print("2) request a graceful shutdown")
    started = time.time()
    try:
        req = subprocess.run([str(EXE), "--genie-shutdown"], cwd=str(EXE.parent),
                             capture_output=True, timeout=20)
        rc, err = req.returncode, req.stderr.decode("utf-8", "replace")[:200]
    except subprocess.TimeoutExpired:
        rc, err = None, "timed out"
    elapsed = time.time() - started
    check("the requesting process exits quickly", rc == 0, f"rc={rc} in {elapsed:.1f}s {err}")

    print("3) the running instance closes itself")
    end = time.time() + 30
    while time.time() < end and genie_pids():
        time.sleep(1)
    left = genie_pids()
    check("every GENIE process is gone", not left, f"left={left}")
    check("backend stopped", not health_ok())

    # The launcher process should also have exited on its own; if it is somehow
    # still alive, close it so the test does not leak a process.
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
