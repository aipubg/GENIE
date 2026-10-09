"""Phase 1 P1.14 — Preview EXE launch readiness verification.

Verifies the source-built Genie.Desktop.exe can find its backend runtime,
has all required dependencies, and the Preview shortcut script is valid.
Does NOT open a GUI window (headless environment).
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 64)
    print("Phase 1 P1.14 — Preview EXE launch readiness")
    print("=" * 64)

    exe_dir = REPO / "ui" / "windows" / "Genie.Desktop" / "bin" / "Release" / "net8.0-windows" / "win-x64"
    exe = exe_dir / "Genie.Desktop.exe"
    dll = exe_dir / "Genie.Desktop.dll"
    runtime_cfg = exe_dir / "Genie.Desktop.runtimeconfig.json"
    ico = REPO / "assets" / "Genie.ico"

    # --- 1. EXE artifact exists and is current ---
    check("EXE exists", exe.exists(), str(exe))
    check("DLL exists", dll.exists(), f"{dll.stat().st_size} bytes")
    check("runtimeconfig exists", runtime_cfg.exists(), str(runtime_cfg))
    check("icon exists", ico.exists(), f"{ico.stat().st_size} bytes")

    # EXE must be newer than any stale installed copy
    installed = Path(r"C:\Users\ghostt\AppData\Local\Programs\GENIE\Genie.Desktop.exe")
    if installed.exists():
        check("EXE newer than installed", dll.stat().st_mtime > installed.stat().st_mtime,
              f"source={dll.stat().st_mtime} installed={installed.stat().st_mtime}")
    else:
        check("no stale installed EXE (absent)", True, "nothing stale to launch")

    # --- 2. Required WPF dependencies present ---
    required_dlls = [
        "PresentationCore.dll", "PresentationFramework.dll", "WindowsBase.dll",
        "System.Xaml.dll", "CommunityToolkit.Mvvm.dll",
    ]
    for d in required_dlls:
        check(f"dependency {d}", (exe_dir / d).exists())

    # --- 3. Backend runtime discoverable (same logic as GenieBackend.FindRuntimeRoot) ---
    def find_runtime_root(base_dir: Path) -> Path | None:
        beside = [
            base_dir / "resources" / "backend-runtime",
            base_dir / "backend-runtime",
        ]
        for c in beside:
            if (c / "python" / "pythonw.exe").exists():
                return c
        d = base_dir
        for _ in range(8):
            if d is None:
                break
            candidate = d / "backend-dist" / "backend-runtime"
            if (candidate / "python" / "pythonw.exe").exists():
                return candidate
            d = d.parent
        return None

    rt = find_runtime_root(exe_dir)
    check("backend runtime discoverable", rt is not None, str(rt))
    if rt:
        check("runtime pythonw.exe exists", (rt / "python" / "pythonw.exe").exists())
        check("runtime python.exe exists", (rt / "python" / "python.exe").exists())
        check("runtime app/ exists", (rt / "app").is_dir())
        check("runtime manifest exists", (rt / "RUNTIME_MANIFEST.json").exists())

    # --- 4. Shortcut script valid ---
    vbs = REPO / "scripts" / "create_preview_shortcut.vbs"
    check("shortcut script exists", vbs.exists(), str(vbs))
    if vbs.exists():
        script = vbs.read_text(encoding="utf-8")
        check("shortcut targets source EXE", str(exe) in script or str(exe).replace("\\", "\\\\") in script)
        check("shortcut uses real icon", str(ico) in script or str(ico).replace("\\", "\\\\") in script)

    # --- 5. EXE PE header check (Windows GUI subsystem, not console) ---
    try:
        with open(exe, "rb") as f:
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset + 0x5C)
            subsystem = struct.unpack("<H", f.read(2))[0]
        # 2 = GUI, 3 = Console
        check("EXE is Windows GUI subsystem", subsystem == 2, f"subsystem={subsystem}")
    except Exception as exc:
        check("EXE is Windows GUI subsystem", False, str(exc))

    # --- 6. Try a silent launch probe (create process, immediately terminate) ---
    # We can't show a GUI, but we CAN verify the process starts and reaches
    # a point where it tries to load the backend (event log or proc existence).
    # In a headless sandbox this may fail for DISPLAY reasons; we record it
    # honestly without marking a FAIL.
    launched = False
    launch_detail = "not attempted"
    try:
        # Use STARTUPINFO to hide window; very short timeout
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(exe_dir),
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            proc.wait(timeout=3)
            out = proc.stdout.read(512).decode("utf-8", errors="ignore") if proc.stdout else ""
            err = proc.stderr.read(512).decode("utf-8", errors="ignore") if proc.stderr else ""
            launch_detail = f"exit={proc.returncode} out={out[:80]!r} err={err[:80]!r}"
            launched = (proc.returncode == 0)
        except subprocess.TimeoutExpired:
            # Still running after 3s = process started successfully
            proc.kill()
            launch_detail = "process still alive after 3s (started successfully)"
            launched = True
    except Exception as exc:
        launch_detail = str(exc)

    # We report this as informational, not a hard pass/fail, because headless
    # GUI launch may fail for environmental reasons unrelated to the build.
    print(f"  [INFO] silent launch probe: {launch_detail}")
    check("silent launch probe (informational)", launched, launch_detail)

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
