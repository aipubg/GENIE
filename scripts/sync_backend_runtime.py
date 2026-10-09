"""Sync authoritative backend source into the packaged runtime.

Why this exists
---------------
The native client launches the PACKAGED backend:

    backend-dist/backend-runtime/python/pythonw.exe app/backend_entry.py

not the source tree. So editing core/ or models/ had NO effect until someone
remembered to `cp` the changed files into backend-dist/backend-runtime/app/.
That is too fragile for a release candidate: a source change could silently
leave the shipped backend stale.

This script is the ONE deterministic path:

    source -> sync -> packaged runtime -> native client

What it does
------------
1. Copies the authoritative backend packages, entrypoints and read-only data
   (the same lists build_backend_runtime.py uses to build from scratch).
2. Excludes caches/junk (__pycache__, *.pyc, tests, .pytest_cache, .git).
3. Deletes stale files at the destination that no longer exist in source.
4. Writes a manifest: source commit + sha256 for every synced file.
5. VERIFIES: re-hashes the destination and compares against the manifest.
6. Fails loudly (non-zero exit) on any copy or verification error.

Run:
    python scripts/sync_backend_runtime.py

Add --check to verify only (no copy). Useful as a build/test assertion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend-dist" / "backend-runtime" / "app"
MANIFEST = ROOT / "backend-dist" / "backend-runtime" / "RUNTIME_MANIFEST.json"

# Mirrors build_backend_runtime.py so "build from scratch" and "sync" never
# disagree about what the packaged backend contains.
PACKAGES = [
    # `browser` was MISSING from this list, so the packaged runtime never shipped
    # the browser authority: computer/executor.py imports `browser` lazily and the
    # whole capability failed with "browser provider unavailable: No module named
    # 'browser'". Any website/browser action was therefore impossible in Preview.
    "agents", "browser", "channels", "computer", "context", "core", "devices",
    "director", "evaluation", "experience", "forecast", "integrations", "knowledge",
    "memory", "missions", "models", "perception", "plugins", "proactive", "security",
    "skills", "specs", "teaching", "usermodel", "voice",
]
ENTRYPOINTS = ["backend_entry.py", "genie.py"]
DATA = [("config", "config"), ("ui/web", "ui/web"), ("ui/assets", "ui/assets")]

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", "tests",
                "_test_crash", "_test_timeout"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def norm(rel: str) -> str:
    """Normalise a relative path to the OS separator.

    Critical: source rels are built with mixed separators (package rels come
    from relative_to() which uses os.sep, while data rels hardcode '/'). The
    stale-file comparison compares them against rels built the same way, so a
    mismatch here silently deletes valid packaged files.
    """
    return str(Path(rel))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def source_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                             capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _runtime_python() -> Path:
    """The exact python.exe used by the packaged runtime."""
    return ROOT / "backend-dist" / "backend-runtime" / "python" / "python.exe"


def _probe_runtime() -> dict:
    """Probe the packaged runtime for dependency + import + model readiness."""
    py = _runtime_python()
    result: dict = {"python_present": py.exists()}
    if not py.exists():
        return result

    # requirements hash
    req = ROOT / "requirements-runtime.txt"
    if req.exists():
        result["requirements_hash"] = sha256(req)[:16]

    # critical packages
    packages = {}
    for pkg in ("faster_whisper", "ctranslate2", "tokenizers", "av", "numpy",
                "sounddevice", "vosk", "google.genai", "google.protobuf", "PIL"):
        try:
            out = subprocess.run(
                [str(py), "-c",
                 f"import {pkg}; print(getattr({pkg}, '__version__', '?'))"],
                capture_output=True, text=True, timeout=15)
            packages[pkg] = out.stdout.strip() if out.returncode == 0 else f"FAIL:{out.stderr.strip()[:60]}"
        except Exception as exc:
            packages[pkg] = f"ERROR:{exc}"
    result["packages"] = packages

    # critical imports (same list, boolean)
    imports = {}
    for mod in ("faster_whisper", "ctranslate2", "tokenizers", "av"):
        try:
            out = subprocess.run(
                [str(py), "-c", f"import {mod}; print('ok')"],
                capture_output=True, text=True, timeout=15)
            imports[mod] = out.returncode == 0 and out.stdout.strip() == "ok"
        except Exception:
            imports[mod] = False
    result["critical_imports"] = imports

    # STT model readiness
    # Use the same canonical per-user store as voice.service and
    # scripts/provision_stt_model.py.  Do not hard-code an account name here:
    # the packaged runtime can be validated under a different Windows user.
    local_app_data = os.environ.get("LOCALAPPDATA")
    model_base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    model_dir = model_base / "GENIE" / "models" / "faster-whisper-small"
    result["stt_model_dir"] = str(model_dir)
    result["stt_model_present"] = model_dir.is_dir() and any(model_dir.iterdir())
    if result["stt_model_present"]:
        try:
            # Pass the path through an env var to avoid quoting hell in -c
            env = dict(os.environ)
            env["_GENIE_STT_MODEL_PATH"] = str(model_dir)
            out = subprocess.run(
                [str(py), "-c",
                 "import os; from faster_whisper import WhisperModel; "
                 "m = WhisperModel(os.environ['_GENIE_STT_MODEL_PATH'], device='cpu', compute_type='int8'); "
                 "print('load_ok')"],
                capture_output=True, text=True, timeout=60, env=env)
            result["stt_model_loads"] = out.returncode == 0 and "load_ok" in out.stdout
            if not result["stt_model_loads"]:
                result["stt_model_load_error"] = (out.stdout + out.stderr)[:120]
        except Exception as exc:
            result["stt_model_loads"] = False
            result["stt_model_load_error"] = str(exc)[:120]
    else:
        result["stt_model_loads"] = False

    return result


def iter_source_files() -> list[tuple[Path, str]]:
    """Yield (absolute source path, relative destination path under app/)."""
    items: list[tuple[Path, str]] = []

    for pkg in PACKAGES:
        src = ROOT / pkg
        if not src.is_dir():
            continue
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            if pkg == "config" and f.name == "user.json":
                continue  # mutable per-user config is never bundled
            rel = f.relative_to(ROOT)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if f.suffix in EXCLUDE_SUFFIXES:
                continue
            items.append((f, norm(str(rel))))

    for entry in ENTRYPOINTS:
        f = ROOT / entry
        if f.is_file():
            items.append((f, entry))

    for src_name, dest_name in DATA:
        src = ROOT / src_name
        if not src.exists():
            continue
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in f.relative_to(ROOT).parts):
                continue
            if f.suffix in EXCLUDE_SUFFIXES:
                continue
            rel = norm(str(Path(dest_name) / f.relative_to(src)))
            items.append((f, rel))

    return items


def do_sync(check_only: bool, *, no_prune: bool = False, skip_runtime_probe: bool = False) -> int:
    if not APP.parent.exists():
        print(f"FAIL: packaged runtime missing: {APP.parent}")
        print("Run scripts/build_backend_runtime.py first.")
        return 2

    items = iter_source_files()
    if not items:
        print("FAIL: no source files found — refusing to write an empty app/.")
        return 2

    print(f"source commit: {source_commit()}")
    print(f"syncing {len(items)} file(s) -> {APP}")

    # 1. Copy (unless --check).
    if not check_only:
        for src, rel in items:
            dest = APP / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dest)
            except Exception as exc:
                print(f"FAIL: copy error {src} -> {dest}: {exc}")
                return 1

        # 2. Remove stale destination files that source no longer provides.
        wanted = {norm(rel) for _, rel in items}
        for existing in (() if no_prune else APP.rglob("*")):
            if not existing.is_file():
                continue
            rel = norm(str(existing.relative_to(APP)))
            if rel.startswith("__pycache__") or rel.endswith(".pyc"):
                continue
            if rel not in wanted:
                try:
                    existing.unlink()
                    print(f"  removed stale: {rel}")
                except Exception as exc:
                    print(f"WARN: could not remove stale {rel}: {exc}")

    # 3. Build the manifest from the SOURCE truth + runtime probe.
    manifest = {
        "source_commit": source_commit(),
        "file_count": len(items),
        "files": {rel: sha256(src) for src, rel in items},
        "runtime": ({"probe_skipped": True, "reason": "Build-only synchronization; no model-load probe."}
                    if skip_runtime_probe else _probe_runtime()),
    }
    manifest["source_fingerprint"] = hashlib.sha256(
        json.dumps(manifest["files"], sort_keys=True).encode("utf-8")).hexdigest()

    # 4. VERIFY the destination matches the manifest.
    mismatches: list[str] = []
    for rel, want in manifest["files"].items():
        dest = APP / rel
        if not dest.is_file():
            mismatches.append(f"missing: {rel}")
            continue
        got = sha256(dest)
        if got != want:
            mismatches.append(f"hash mismatch: {rel} (source {want[:12]}… dest {got[:12]}…)")

    if mismatches:
        print(f"\nFAIL: {len(mismatches)} packaged-runtime drift issue(s):")
        for m in mismatches[:20]:
            print(f"  {m}")
        if len(mismatches) > 20:
            print(f"  … and {len(mismatches) - 20} more")
        return 1

    if not check_only:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nOK: packaged runtime matches source at commit "
          f"{manifest['source_commit'][:12]} ({manifest['file_count']} files).")
    if not check_only:
        print(f"manifest: {MANIFEST}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify only; do not copy")
    ap.add_argument("--no-prune", action="store_true",
                    help="preserve extra destination files; never delete during synchronization")
    ap.add_argument("--skip-runtime-probe", action="store_true",
                    help="skip dependency/model-load probes for a build-only pass")
    args = ap.parse_args()
    return do_sync(args.check, no_prune=args.no_prune, skip_runtime_probe=args.skip_runtime_probe)


if __name__ == "__main__":
    raise SystemExit(main())
