"""Freeze the GENIE backend into a single executable for packaging.

Produces:

    backend-dist/GENIEBackend.exe          (Windows)
    backend-dist/genie-backend             (POSIX)

which the desktop shell launches as:

    GENIEBackend.exe

The entrypoint is backend_entry.py, NOT genie.py: the frozen binary must have
exactly one job (start the production daemon) and must not ship the developer
CLI surface.

Why this exists
---------------
A normal installed GENIE must not depend on the source checkout
(`E:\\G3\\GENIE`), on `python genie.py daemon`, on the owner's Python
installation, on a developer venv, or on WorkBuddy. The installer must carry
everything required to start the authoritative local runtime.

Build dependency (see requirements-build.txt):

    python -m pip install -r requirements-build.txt
    python scripts/build_backend.py

This is packaging tooling. It produces backend-dist/backend-runtime, the
embedded Python core that the Windows-native client (Genie.Desktop) owns.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend-dist"
ENTRY = ROOT / "backend_entry.py"
NAME = "GENIEBackend.exe" if platform.system() == "Windows" else "genie-backend"

# Modules the daemon loads dynamically (plugins / registries / optional
# services). PyInstaller cannot always infer these, so they are explicit.
# This is a targeted list, NOT "bundle the whole repository".
HIDDEN_IMPORTS = [
    "core",
    "core.config",
    "core.lifecycle",
    "core.db",
    "core.ipc",
    "core.ipc.server",
    "core.orchestrator",
    "core.events",
    "core.controlcenter",
    "core.backup",
    "core.hardening",
    "core.updater",
    "core.release_fetch",
    "core.logging_setup",
    "core.jsonrepair",
    "core.action_metrics",
    "core.staleguard",
    "models",
    "models.registry",
    "agents",
    "agents.providers",
    "agents.runtime",
    "agents.team",
    "missions",
    "memory",
    "skills",
    "devices",
    "devices.registry",
    "security",
    "voice",
    "voice.providers",
    "context",
    "context.builder",
]

# Heavy packages that are installed in the build interpreter but that the
# daemon never imports. PyInstaller walks the whole interpreter environment and
# its torch hook segfaults (observed EXIT=139), so they are excluded
# explicitly. Verified: no project module imports any of these.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "tensorboard",
    "IPython", "jupyter", "notebook", "pytest", "matplotlib",
    # The daemon is headless. Tk pulled in the Tcl/Tk hook and PyInstaller
    # segfaulted (EXIT=139) right after initialising it.
    "tkinter", "tk", "turtle", "Tkinter",
    # ROOT CAUSE of the segfault, found by bisecting leaf imports:
    # director.needle_runtime -> SEGFAULT, everything else -> builds fine.
    # It pulls in the optional `needle` package (cactus-needle), whose native
    # stack (onnxruntime/torch) crashes PyInstaller's analysis.
    #
    # Excluding it is legitimate, not a workaround: needle_runtime imports it
    # defensively inside try/except, package_installed() returns False when it
    # is absent, and the heuristic Director still serves. NEDLE2 is the local
    # Director and does not require this optional package.
    "needle", "cactus_needle", "onnxruntime", "onnx",
    # scipy is never imported anywhere in the project (verified by grep); it is
    # pulled in transitively and its hook chain crashed the next analysis run.
    "scipy",
]


def main() -> int:
    try:
        import PyInstaller
    except ModuleNotFoundError:
        print("PyInstaller is not installed.")
        print("Install it with:  python -m pip install -r requirements-build.txt")
        return 2

    if not ENTRY.exists():
        print(f"entrypoint missing: {ENTRY}")
        return 2

    print(f"PyInstaller {PyInstaller.__version__}")

    OUT.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        # NOT --clean: it bulk-deletes the work dir, which trips the host's
        # safe-delete guard (>50 files) and kills the build instantly with
        # exit 127 - which looks exactly like a crash. The work dir is reused
        # instead; PyInstaller overwrites in place.
        # --onedir, not --onefile: easier native DLL/data inclusion, plugin
        # inspection, no runtime self-extraction, and far less antivirus /
        # temp-path complexity. The NSIS installer ships the whole directory.
        "--onedir",
        "--name", "GENIEBackend",
        "--distpath", str(OUT),
        "--specpath", str(ROOT / "build"),
        "--workpath", str(ROOT / "build" / "backend"),
        "--collect-submodules", "core",
    ]
    # Read-only bundled data. config/ holds the provider templates (without it
    # the packaged registry comes up empty); ui/web + ui/assets are what the
    # daemon actually serves.
    #
    # Deliberately NOT all of ui/: ui/screenshots was ~14 MB and bundling it
    # bloated the payload. Electron is retired, so there is no node_modules
    # tree to exclude any more.
    for src, dest in (
        ("config", "config"),
        ("ui/web", "ui/web"),
        ("ui/assets", "ui/assets"),
    ):
        path = ROOT / src
        if path.exists():
            cmd += ["--add-data", f"{path}{os.pathsep}{dest}"]
        else:
            print(f"WARNING: bundled data missing: {path}")

    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))

    print("freezing backend:")
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("backend freeze FAILED")
        return result.returncode

    # --onedir layout: backend-dist/GENIEBackend/GENIEBackend.exe + _internal/
    bundle = OUT / "GENIEBackend"
    produced = bundle / NAME
    if produced.exists():
        digest = hashlib.sha256(produced.read_bytes()).hexdigest()
        total = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file())
        print(f"\nOK -> {produced}  ({produced.stat().st_size:,} bytes)")
        print(f"sha256 {digest}")
        print(f"bundle total {total:,} bytes")
        print("Ship the WHOLE directory via the NSIS installer "
              "(installer/genie_native.nsi -> backend-runtime)")
        return 0

    print(f"\nBuild finished but {produced} was not found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
