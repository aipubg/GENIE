"""Prove the CORRECT build is running (Pass 3.5 correction).

Answers, deterministically:
  * which Genie.Desktop.dll is installed vs which one the source builds
  * whether the built UI actually contains the latest owner-facing UI
  * whether the packaged backend runtime is synced to source

Run:  PYTHONPATH="." python scripts/verify_build_match.py
Exit 0 = the source build matches the latest implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLED = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Programs/GENIE/Genie.Desktop.dll"
SRC_DLL = ROOT / "ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.dll"
MANIFEST = ROOT / "backend-dist/backend-runtime/RUNTIME_MANIFEST.json"
APP = ROOT / "backend-dist/backend-runtime/app"

# UI that MUST be present in the latest build.
REQUIRED_UI = [
    "Custom / OpenAI-compatible", "Mission specialists", "RingsHost",
    "Build identity",
    # Renamed in Pass 4 §23: the old "Test / diagnostics providers" heading
    # was confusing in normal Settings, so the check tracks the new wording.
    "Diagnostics endpoints (not used for routing)", "Delete",
]
# UI that MUST NOT be present (removed in Pass 1/2).
FORBIDDEN_UI = ["Enable / Disable", "Roles unavailable", "Everyday", "deep_work"]
# Backend modules that MUST be in the synced runtime.
REQUIRED_BACKEND = [
    "missions/runner.py", "missions/scheduler.py", "missions/control.py",
    "models/eligibility.py",
]

_pass = _fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


def contains(path: Path, needle: str) -> bool:
    try:
        return needle.encode("utf-8") in path.read_bytes()
    except Exception:
        return False


def main() -> int:
    print("build-match verification")
    print(f"\nsource-built UI : {SRC_DLL}")
    print(f"  sha256={sha(SRC_DLL)}")
    print(f"installed UI    : {INSTALLED}")
    print(f"  sha256={sha(INSTALLED)}")

    # The point of this check is that the owner must never be able to launch a
    # STALE installed build. Two ways to satisfy that:
    #   * the installed copy is byte-identical to the source build, or
    #   * there is no installed copy at all (rc26 was removed) - there is then
    #     nothing stale for the owner to launch by mistake.
    # An absent copy is therefore a PASS, not a failure.
    installed_absent = not INSTALLED.exists()
    same = SRC_DLL.exists() and INSTALLED.exists() and sha(SRC_DLL) == sha(INSTALLED)
    if installed_absent:
        check("no stale installed UI (absent)", True,
              "no installed copy - nothing stale to launch")
    else:
        check("installed UI == source-built UI", same,
              "same binary" if same else "DIFFERENT - the installed app is NOT this build")

    print("\n[latest UI markers in the source build]")
    for m in REQUIRED_UI:
        check(f"UI contains {m!r}", contains(SRC_DLL, m))
    print("\n[removed UI must be gone]")
    for m in FORBIDDEN_UI:
        check(f"UI does NOT contain {m!r}", not contains(SRC_DLL, m))

    print("\n[backend runtime sync]")
    check("runtime manifest exists", MANIFEST.exists())
    runtime_ok = True
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        check("manifest lists files", int(data.get("file_count") or 0) > 0,
              f"{data.get('file_count')} files @ {str(data.get('source_commit'))[:12]}")
        rt = data.get("runtime") or {}
        if rt.get("requirements_hash"):
            check("runtime requirements hash present", True, rt["requirements_hash"])
        for pkg, ver in (rt.get("packages") or {}).items():
            ok = not ver.startswith(("FAIL", "ERROR"))
            check(f"runtime package {pkg}", ok, ver)
            if not ok:
                runtime_ok = False
        for mod, imp_ok in (rt.get("critical_imports") or {}).items():
            check(f"runtime import {mod}", bool(imp_ok))
            if not imp_ok:
                runtime_ok = False
        check("STT model present", rt.get("stt_model_present") is True,
              rt.get("stt_model_dir", "?"))
        check("STT model loads", rt.get("stt_model_loads") is True)
    for rel in REQUIRED_BACKEND:
        check(f"runtime has {rel}", (APP / rel).exists())

    print(f"\nRESULT {_pass}/{_pass + _fail} passed")
    # Only a REAL stale installed copy demands action; an absent one is fine.
    if not same and not installed_absent:
        print("\nACTION: run scripts/sync_backend_runtime.py and launch the SOURCE-BUILT\n"
              f"        {SRC_DLL}\n        (not the installed copy / Start Menu shortcut).")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
