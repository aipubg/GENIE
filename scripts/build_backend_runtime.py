"""Assemble the packaged GENIE backend runtime (embedded-Python fallback).

Produces:

    backend-dist/backend-runtime/
        python/      embeddable Python 3.12 + site-packages (deps pre-installed)
        app/         backend code + config + ui/web + ui/assets

Launched by the desktop shell as:

    python\\pythonw.exe app\\backend_entry.py

Why this exists
---------------
PyInstaller 6.22.2 segfaults non-deterministically on this dependency graph
(EXIT=139), even from a clean venv with only the true runtime deps. Rather than
fight the freezer, the backend ships as a self-contained Python runtime.

Python's embeddable distribution exists exactly for embedding Python inside
another application, so this is a supported approach — not a hack.

Important
---------
* The embeddable distribution has NO pip and restricted path handling via its
  ._pth file. All dependencies are therefore installed at BUILD time.
* End users never run pip.
* Nothing here uses the owner's installed Python.

Run:

    python scripts/build_backend_runtime.py

Packaging tooling only.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_build_root = os.environ.get("GENIE_BUILD_ROOT")
if not _build_root:
    _base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    _build_root = str(Path(_base) / "GENIE" / "build") if _base else str(Path.home() / ".local" / "share" / "GENIE" / "build")
BUILD = Path(_build_root) / "embed"
OUT = ROOT / "backend-dist" / "backend-runtime"

PY_VER = "3.12.6"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"

# Top-level packages shipped with the backend. `tests` is deliberately excluded.
PACKAGES = [
    "agents", "browser", "channels", "computer", "context", "core", "devices",
    "director", "evaluation", "experience", "forecast", "integrations", "knowledge",
    "memory", "missions", "models", "perception", "plugins", "proactive", "security",
    "skills", "specs", "teaching", "usermodel", "voice",
]
ENTRYPOINTS = ["backend_entry.py", "genie.py"]
DATA = [("config", "config"), ("ui/web", "ui/web"), ("ui/assets", "ui/assets")]


def download_embed() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    zip_path = BUILD / "python-embed.zip"
    if not zip_path.exists():
        print(f"downloading {EMBED_URL}")
        urllib.request.urlretrieve(EMBED_URL, zip_path)
    dest = BUILD / "python-embed"
    if not (dest / "python.exe").exists():
        dest.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
    return dest


def enable_site(dest: Path) -> None:
    """Embeddable Python ignores sys.path[0] and PYTHONPATH unless told not to."""
    for pth in dest.glob("python3*._pth"):
        lines = [l.strip() for l in pth.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        for needed in ("site-packages", "import site"):
            if needed not in lines:
                lines.append(needed)
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {pth.name}: site-packages enabled")


def enable_pywin32(dest: Path) -> None:
    """Make pywin32 importable inside the EMBEDDABLE runtime.

    Real defect found in production acceptance: pywinauto WAS bundled but
    `import win32api` failed with "No module named 'win32api'", so
    computer.uia.available() returned False and the primary Windows control
    path (UIA) was dead in the shipped runtime. Two things are required:

      1. the `win32`, `win32/lib` and `pythonwin` directories must be on
         sys.path (they are normally added by pywin32.pth, which the
         embeddable `._pth` does not process);
      2. `pythoncom312.dll` and `pywintypes312.dll` must be findable by the
         Windows loader. `._pth` entries affect module lookup only, NOT the
         DLL search path, so the DLLs are copied next to the interpreter.
    """
    for pth in dest.glob("python3*._pth"):
        lines = [l.rstrip() for l in pth.read_text(encoding="utf-8").splitlines()]
        for needed in ("site-packages\\win32", "site-packages\\win32\\lib",
                       "site-packages\\pythonwin", "site-packages\\pywin32_system32"):
            if needed not in lines:
                idx = lines.index("import site") if "import site" in lines else len(lines)
                lines.insert(idx, needed)
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {pth.name}: pywin32 paths enabled")

    system32 = dest / "site-packages" / "pywin32_system32"
    if system32.is_dir():
        copied = []
        for dll in system32.glob("*.dll"):
            shutil.copy2(dll, dest / dll.name)
            copied.append(dll.name)
        print(f"  pywin32 DLLs copied next to the interpreter: {', '.join(sorted(copied))}")


def install_deps(dest: Path) -> None:
    sp = dest / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    print("installing runtime deps into embeddable site-packages")
    cmd = [sys.executable, "-m", "pip", "install", "--quiet",
           "--disable-pip-version-check", "--no-compile", "--upgrade", "--target", str(sp),
           "-r", str(ROOT / "requirements-runtime.txt")]
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("dependency install failed")


def main() -> int:
    if platform.system() != "Windows":
        print("This packaging path is Windows-only (embeddable amd64 build).")
        return 2

    embed = download_embed()
    enable_site(embed)
    install_deps(embed)

    enable_pywin32(embed)
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "app").mkdir(parents=True, exist_ok=True)

    print("copying python runtime")
    shutil.copytree(embed, OUT / "python", dirs_exist_ok=True)

    print("copying backend code")
    for pkg in PACKAGES:
        src = ROOT / pkg
        if src.is_dir():
            shutil.copytree(src, OUT / "app" / pkg, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_test_*"))
    for entry in ENTRYPOINTS:
        if (ROOT / entry).exists():
            shutil.copy2(ROOT / entry, OUT / "app" / entry)

    print("copying read-only data")
    for src, dest in DATA:
        s = ROOT / src
        if s.exists():
            ignore = shutil.ignore_patterns("user.json") if src == "config" else None
            shutil.copytree(s, OUT / "app" / dest, dirs_exist_ok=True, ignore=ignore)
        else:
            print(f"  WARNING missing: {src}")

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\nOK -> {OUT}  ({size:,} bytes)")
    print("Launch: python\\pythonw.exe app\\backend_entry.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
