"""W7.3 — single instance, end to end.

Launching GENIE twice must NOT produce two applications. The second launch has
to hand off to the first (activating it) and exit, leaving exactly one
Genie.Desktop process and one backend.

This test exercises the real executables, not the classes: a mutex unit test
can pass while the shipped app still starts twice (for example if App.xaml.cs
checks the mutex after creating the window).

Non-destructive apart from starting and stopping our own app.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

EXE = (Path(__file__).resolve().parents[1]
       / "ui" / "windows" / "Genie.Desktop" / "bin" / "Release"
       / "net8.0-windows" / "win-x64" / "Genie.Desktop.exe")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def desktop_pids() -> list[int]:
    return [p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == "genie.desktop.exe"]


def pythonw_pids() -> list[int]:
    return [p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == "pythonw.exe"]


def main() -> int:
    if not EXE.exists():
        print(f"exe not found: {EXE}")
        return 2

    # Clean slate so counts mean something.
    for pid in desktop_pids():
        try:
            psutil.Process(pid).terminate()
        except Exception:
            pass
    time.sleep(2.0)

    print("1) first launch")
    first = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    time.sleep(10)
    pids_after_first = desktop_pids()
    check("first launch produced a running instance", len(pids_after_first) == 1,
          f"pids={pids_after_first}")

    backend_after_first = pythonw_pids()
    check("backend started with the app", len(backend_after_first) >= 1,
          f"pythonw={len(backend_after_first)}")

    print("2) second launch (must hand off, not duplicate)")
    before = set(desktop_pids())
    second = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    time.sleep(10)
    after = set(desktop_pids())

    check("still exactly one Genie.Desktop", len(after) == 1, f"pids={sorted(after)}")
    check("the original instance is the survivor",
          bool(before) and before == after,
          f"before={sorted(before)} after={sorted(after)}")

    # The second copy should exit on its own once it signals the first.
    try:
        rc = second.wait(timeout=15)
        check("second copy exited by itself", True, f"exit code {rc}")
    except subprocess.TimeoutExpired:
        second.kill()
        check("second copy exited by itself", False, "still running after 15s")

    print("3) cleanup")
    # Ask it to close itself rather than killing it. A kill cannot run OnExit,
    # so the backend it owns would be orphaned - and the next test would find
    # pythonw.exe processes holding the database with no window to close.
    subprocess.run([str(EXE), "--genie-shutdown"], cwd=str(EXE.parent),
                   timeout=25, capture_output=True)
    end = time.time() + 30
    while time.time() < end and (desktop_pids() or pythonw_pids()):
        time.sleep(1)
    check("instance closed", len(desktop_pids()) == 0, f"pids={desktop_pids()}")
    check("backend stopped with it", len(pythonw_pids()) == 0,
          f"pythonw={len(pythonw_pids())}")

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"RESULT: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
