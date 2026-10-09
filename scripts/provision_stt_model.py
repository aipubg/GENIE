"""
Deterministic provisioning of the multilingual STT model.

    Systran/faster-whisper-small  (Faster-Whisper / CTranslate2 format)

Rules honoured:
  * download into  %LOCALAPPDATA%\\GENIE\\models\\.staging\\faster-whisper-small
  * never treat zero-byte files, HF pointer stubs or *.incomplete blobs as valid
  * validate by actually initialising WhisperModel (not just file existence)
  * promote to the active location ONLY after validation succeeds
  * on failure the existing valid model is left untouched

States: missing -> downloading -> validating -> ready | failed

    python scripts/provision_stt_model.py [--repo Systran/faster-whisper-small]
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

REQUIRED = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
PINNED_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
# Anything smaller than this is a pointer/stub, not a real asset.
MIN_MODEL_BYTES = 10 * 1024 * 1024


def model_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    root = base / "GENIE" / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _nonzero(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def validate_files(d: Path) -> tuple[bool, str]:
    for name in REQUIRED:
        p = d / name
        if not _nonzero(p):
            return False, f"{name} missing or zero-byte"
    if (d / "model.bin").stat().st_size < MIN_MODEL_BYTES:
        return False, "model.bin too small (partial download)"
    # Reject HF pointer stubs (they contain a tiny JSON with "oid"/"size").
    cfg = d / "config.json"
    try:
        head = cfg.read_text(encoding="utf-8", errors="ignore")[:200]
        if '"oid"' in head or head.strip().startswith("version https://"):
            return False, "config.json is an LFS pointer stub"
        json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"config.json unreadable: {exc}"
    return True, "files present and non-trivial"


def validate_load(d: Path) -> tuple[bool, str]:
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(str(d), device="cpu", compute_type="int8")
        # Touch the model to confirm it is really usable.
        _ = m.transcribe  # attribute check only (no audio needed here)
        return True, "WhisperModel initialised"
    except Exception as exc:
        return False, f"WhisperModel load failed: {type(exc).__name__}: {exc}"


def revision_at(d: Path) -> str:
    metadata = d / ".cache" / "huggingface" / "download" / "model.bin.metadata"
    try:
        return metadata.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def model_manifest(d: Path, repo: str, revision: str) -> dict:
    files = {}
    for name in REQUIRED:
        p = d / name
        digest = hashlib.sha256()
        with p.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files[name] = {"size": p.stat().st_size, "sha256": digest.hexdigest()}
    return {"repo": repo, "revision": revision, "files": files}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Systran/faster-whisper-small")
    ap.add_argument("--name", default="faster-whisper-small")
    ap.add_argument("--revision", default=PINNED_REVISION)
    args = ap.parse_args()

    root = model_root()
    active = root / args.name
    staging = root / ".staging" / args.name
    report: dict = {"repo": args.repo, "revision": args.revision,
                    "active": str(active), "staging": str(staging)}

    print(f"[state] checking active model at {active}")
    active_manifest_path = active / "GENIE_MODEL_MANIFEST.json"
    if all(_nonzero(active / n) for n in REQUIRED) and active_manifest_path.is_file():
        ok, why = validate_files(active)
        if ok:
            try:
                active_manifest = json.loads(active_manifest_path.read_text(encoding="utf-8"))
                expected = model_manifest(active, args.repo, args.revision)
                manifest_ok = active_manifest == expected
            except Exception:
                manifest_ok = False
            ok2, why2 = validate_load(active) if manifest_ok else (False, "pinned manifest/revision/checksum mismatch")
            if ok2:
                report["state"] = "ready"
                report["detail"] = "pinned files and checksums verified; packaged WhisperModel loadable"
                print(json.dumps(report, indent=2))
                return 0
            print(f"[state] active model failed load: {why2}")
        else:
            print(f"[state] active model invalid: {why}")
    else:
        print("[state] missing")

    # ---------------------------------------------------------- downloading
    report["state"] = "downloading"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        report["state"] = "failed"
        report["detail"] = f"huggingface_hub unavailable: {exc}"
        print(json.dumps(report, indent=2))
        return 1

    try:
        path = snapshot_download(
            repo_id=args.repo,
            revision=args.revision,
            local_dir=str(staging),
            allow_patterns=list(REQUIRED) + ["preprocessor_config.json"],
            max_workers=4,
        )
        report["downloaded_to"] = str(path)
        print(f"[state] downloaded -> {path}")
    except Exception as exc:
        report["state"] = "failed"
        report["detail"] = f"download failed: {type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2))
        return 1

    # ----------------------------------------------------------- validating
    report["state"] = "validating"
    actual_revision = revision_at(staging)
    if actual_revision != args.revision:
        report["state"] = "failed"
        report["detail"] = f"pinned revision mismatch: expected {args.revision}, found {actual_revision or 'unknown'}"
        print(json.dumps(report, indent=2))
        return 1
    ok, why = validate_files(staging)
    report["file_validation"] = {"ok": ok, "detail": why}
    print(f"[state] file validation: {ok} ({why})")
    if ok:
        sizes = {n: (staging / n).stat().st_size for n in REQUIRED}
        report["sizes"] = sizes
        print(f"[state] sizes: {sizes}")
        ok2, why2 = validate_load(staging)
        report["load_validation"] = {"ok": ok2, "detail": why2}
        print(f"[state] load validation: {ok2} ({why2})")
        if ok2:
            manifest = model_manifest(staging, args.repo, args.revision)
            (staging / "GENIE_MODEL_MANIFEST.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            # promote atomically-ish: swap in a fresh directory
            tmp_promote = root / (args.name + ".new")
            if tmp_promote.exists():
                shutil.rmtree(tmp_promote, ignore_errors=True)
            shutil.copytree(staging, tmp_promote)
            old = root / (args.name + ".old")
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
            if active.exists():
                active.rename(old)
            tmp_promote.rename(active)
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
            report["state"] = "ready"
            report["detail"] = "model validated and promoted"
            print(json.dumps(report, indent=2))
            return 0
        report["state"] = "failed"
        report["detail"] = why2
        print(json.dumps(report, indent=2))
        return 1

    report["state"] = "failed"
    report["detail"] = why
    print(json.dumps(report, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
