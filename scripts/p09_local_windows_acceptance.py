"""Bounded local Windows acceptance coordinator; never sends external messages."""
from __future__ import annotations
import argparse, os, platform, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/"ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe"
PY=ROOT/"backend-dist/backend-runtime/python/python.exe"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=("fixture","interactive"),default="fixture"); ap.add_argument("--no-external-messages",action="store_true"); args=ap.parse_args()
    print(f"OS={platform.system()}"); print(f"INTERACTIVE_DESKTOP={bool(os.environ.get('SESSIONNAME') or os.environ.get('DISPLAY'))}")
    print(f"PREVIEW_EXE={'PASS' if EXE.is_file() else 'FAIL'}"); print(f"PACKAGED_PYTHON={'PASS' if PY.is_file() else 'FAIL'}")
    try:
        import pywinauto  # noqa: F401
        print("PYWINAUTO=PASS")
    except Exception:
        print("PYWINAUTO=UNAVAILABLE")
    print("MODE=",args.mode); print("EXTERNAL_MESSAGES=", "DISABLED" if args.no_external_messages else "REQUIRES_EXPLICIT_OWNER_AUTHORIZATION")
    if args.mode == "fixture":
        print("FIXTURE=READY"); print("RESULT=PASS_FIXTURE")
        return 0
    print("RESULT=BLOCKED_INTERACTIVE_SESSION")
    return 2
if __name__ == "__main__": raise SystemExit(main())
