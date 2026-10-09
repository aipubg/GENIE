"""Launch the native client detached from the calling shell.

A backgrounded child of a bash command does not reliably outlive that command:
GENIE was observed to die a couple of minutes after `./Genie.Desktop.exe &`.
Detaching with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP puts it in its own
process group so the harness shell can exit without taking the app with it.

    python scripts/launch_native_detached.py [--wait-health] [--seconds N]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

EXE = (Path(__file__).resolve().parents[1] / "ui" / "windows" / "Genie.Desktop"
       / "bin" / "Release" / "net8.0-windows" / "win-x64" / "Genie.Desktop.exe")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(timeout: float = 2.0) -> bool:
    try:
        with _opener.open("http://127.0.0.1:8787/health", timeout=timeout):
            return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-health", action="store_true")
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()

    if not EXE.exists():
        print(f"exe not found: {EXE}")
        return 2

    proc = subprocess.Popen(
        [str(EXE)], cwd=str(EXE.parent), close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    print(f"launched pid={proc.pid}")

    if args.wait_health:
        end = time.time() + args.seconds
        while time.time() < end:
            if health_ok():
                print("backend healthy")
                return 0
            time.sleep(1)
        print("backend did not become healthy in time")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
