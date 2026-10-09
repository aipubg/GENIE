"""Provision the pinned Laya checkpoint into GENIE's persistent model store.

WHY THIS SCRIPT EXISTS
----------------------
The first Laya provisioning landed in temporary directories (`C:\\tmp\\laya_env`,
`C:\\tmp\\laya_src`, `C:\\tmp\\laya_weights`), which the OS may clean and which no
other machine can reproduce. GENIE's canonical persistent model location is
``core.paths.model_dir()`` (``%LOCALAPPDATA%/GENIE/models`` on Windows), shared
by Faster-Whisper, Vosk and Laya.

What it does (idempotent; each step is skipped when already satisfied):

  1. source   -> ``<model_dir>/laya/source``   pinned git revision
  2. weights  -> ``<model_dir>/laya/weights``  pinned HF revision, size + sha256
  3. venv     -> ``<model_dir>/laya/venv``     isolated Python with pinned deps
  4. manifest -> ``<model_dir>/laya/provision.json``

Verification is real, never assumed: every weight file must exist, be non-zero and
match the byte size reported by the Hub API (and its LFS sha256 when published);
the worker is then started once and must answer a probe before the manifest is
marked verified.

Usage:
    python scripts/provision_laya.py --verify-only
    python scripts/provision_laya.py --import-source C:\\tmp\\laya_src \\
                                     --import-weights C:\\tmp\\laya_weights
    python scripts/provision_laya.py                 # full network provision
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SOURCE_REPO = "https://github.com/NandhaKishorM/laya.git"
SOURCE_REVISION = "1e28ac20c0896b1c37a744cd11f740eb98f8b178"
MODEL_REPO = "convaiinnovations/laya"
MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
LICENSE_EXPECTED = "apache-2.0"

# Only what inference needs; the repo also ships assets/ and eval/ charts.
WANTED = (
    "model.safetensors",
    "rl_agent_config.json",
    "encoder/config.json",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer_config.json",
    "rl_agent_api.py",
    "rl_common.py",
)
PINNED_DEPS = ("laya==0.3.29", "transformers==4.57.6", "torch", "psutil")

# The worker caps itself at 2 threads; Laya is CPU-only here.
PROBE = "open notepad"


def log(msg: str) -> None:
    print(msg, flush=True)


def target_root() -> Path:
    from core import paths
    return Path(paths.model_dir()) / "laya"


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def hub_files() -> list:
    url = f"https://huggingface.co/api/models/{MODEL_REPO}?revision={MODEL_REVISION}"
    req = urllib.request.Request(url, headers={"User-Agent": "genie-provision/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return [s.get("rfilename") for s in data.get("siblings", []) if s.get("rfilename")]


def hub_license() -> str:
    url = f"https://huggingface.co/api/models/{MODEL_REPO}?revision={MODEL_REVISION}"
    req = urllib.request.Request(url, headers={"User-Agent": "genie-provision/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return ((data.get("cardData") or {}).get("license") or "").lower()


# --------------------------------------------------------------------- source
def provision_source(dest: Path, import_from: str | None) -> dict:
    # A linked worktree stores `.git` as a file pointing at the common git dir;
    # a normal clone stores it as a directory. Both are valid pinned checkouts.
    if (dest / ".git").exists():
        head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        if head.returncode == 0 and head.stdout.strip() == SOURCE_REVISION:
            log(f"  source: already at pinned revision")
            return {"revision": SOURCE_REVISION, "action": "reused"}
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if import_from:
        log(f"  source: importing from {import_from}")
        shutil.copytree(import_from, dest)
        # keep it a real git checkout: the runtime enforces the revision via git
    else:
        log(f"  source: cloning {SOURCE_REPO}")
        subprocess.run(["git", "clone", "--quiet", SOURCE_REPO, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", SOURCE_REVISION], check=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    got = head.stdout.strip()
    if got != SOURCE_REVISION:
        raise SystemExit(f"source revision mismatch: {got} != {SOURCE_REVISION}")
    log(f"  source: pinned {got[:12]}")
    return {"revision": got, "action": "provisioned"}


# -------------------------------------------------------------------- weights
def download_one(name: str, dest: Path) -> dict:
    out = dest / name
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": "genie-provision/1.0"})
    if out.exists() and out.stat().st_size > 0:
        log(f"  weights: {name} already present ({out.stat().st_size} bytes)")
        return {"bytes": out.stat().st_size, "sha256": sha256_of(out), "action": "reused"}
    part = out.with_name(out.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            declared = resp.headers.get("Content-Length")
            declared = int(declared) if declared and declared.isdigit() else None
            etag = (resp.headers.get("X-Linked-Etag") or resp.headers.get("ETag") or "").strip('"')
            with open(part, "wb") as fh:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    fh.write(block)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise SystemExit(f"download failed for {name}: {type(exc).__name__}: {exc}")
    actual = part.stat().st_size
    if actual <= 0:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{name}: zero-byte artifact rejected")
    if declared is not None and actual != declared:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{name}: truncated {actual} != Content-Length {declared}")
    digest = sha256_of(part)
    if len(etag) == 64 and etag.lower() != digest.lower():
        part.unlink(missing_ok=True)
        raise SystemExit(f"{name}: sha256 mismatch vs Hub LFS etag")
    os.replace(part, out)
    log(f"  weights: {name} -> {actual} bytes sha256={digest[:12]}")
    return {"bytes": actual, "sha256": digest, "etag_verified": len(etag) == 64,
            "action": "downloaded"}


def provision_weights(dest: Path, import_from: str | None) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    available = set(hub_files())
    missing_remote = [n for n in WANTED if n not in available]
    if missing_remote:
        raise SystemExit(f"pinned revision is missing expected files: {missing_remote}")
    files = {}
    for name in WANTED:
        target = dest / name
        if import_from and (Path(import_from) / name).is_file() and not target.is_file():
            src = Path(import_from) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            log(f"  weights: {name} imported ({target.stat().st_size} bytes)")
            files[name] = {"bytes": target.stat().st_size, "sha256": sha256_of(target),
                           "action": "imported"}
            continue
        files[name] = download_one(name, dest)
    return {"repo": MODEL_REPO, "revision": MODEL_REVISION, "files": files}


# ------------------------------------------------------------------------ venv
def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_creator() -> str:
    """A Python that can actually build a venv.

    The packaged GENIE runtime is an EMBEDDABLE CPython and ships no `venv`
    module, so `sys.executable -m venv` fails with "No module named venv". We
    probe the running interpreter first and then the usual full installs.
    """
    candidates = [sys.executable]
    for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(str(Path(base) / "Programs" / "Python" / "Python312" / "python.exe"))
            candidates.append(str(Path(base) / "Python312" / "python.exe"))
    candidates += [r"C:\Python312\python.exe", shutil.which("python3.12") or "",
                   shutil.which("python") or ""]
    for cand in candidates:
        if not cand or not Path(cand).exists():
            continue
        probe = subprocess.run([cand, "-c", "import venv; print('ok')"],
                               capture_output=True, text=True)
        if probe.returncode == 0 and "ok" in probe.stdout:
            return cand
    raise SystemExit("no interpreter with the `venv` module found; install a full "
                     "CPython 3.12 and re-run, or pass one via PYTHON312")


def provision_venv(venv: Path) -> dict:
    py = venv_python(venv)
    if not py.exists():
        # a half-built venv (interrupted earlier) blocks `venv`; clear it first
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
        creator = os.environ.get("PYTHON312") or _venv_creator()
        log(f"  venv: creating {venv} with {creator}")
        venv.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([creator, "-m", "venv", str(venv)], check=True)
    probe = subprocess.run([str(py), "-c",
                            "import laya, torch, transformers;"
                            "print(laya.__version__, torch.__version__, transformers.__version__)"],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        log(f"  venv: already provisioned ({probe.stdout.strip()})")
        return {"python": str(py), "packages": probe.stdout.strip(), "action": "reused"}
    log("  venv: installing pinned dependencies")
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", *PINNED_DEPS], check=True)
    probe = subprocess.run([str(py), "-c",
                            "import laya, torch, transformers;"
                            "print(laya.__version__, torch.__version__, transformers.__version__)"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        raise SystemExit(f"venv probe failed: {probe.stderr.strip()[:300]}")
    log(f"  venv: installed ({probe.stdout.strip()})")
    return {"python": str(py), "packages": probe.stdout.strip(), "action": "provisioned"}


# ------------------------------------------------------------------- verify
def verify_worker(venv: Path, source: Path, weights: Path) -> dict:
    worker = REPO_ROOT / "director" / "laya_worker.py"
    py = venv_python(venv)
    t0 = time.perf_counter()
    proc = subprocess.run([str(py), str(worker), str(source), str(weights)],
                          input=json.dumps({"text": PROBE}) + "\n",
                          capture_output=True, text=True, timeout=420)
    elapsed = round(time.perf_counter() - t0, 1)
    lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    ready = next((json.loads(l) for l in lines if '"ready"' in l), None)
    answer = next((json.loads(l) for l in lines if '"choice"' in l), None)
    ok = bool(ready and answer and answer.get("choice"))
    log(f"  verify: ready={bool(ready)} answer={answer} ({elapsed}s)")
    if not ok:
        raise SystemExit(f"worker verification failed: {proc.stderr[-400:]}")
    return {"ok": True, "load_ms": (ready or {}).get("load_ms"), "probe": PROBE,
            "answer": answer, "seconds": elapsed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--import-source")
    ap.add_argument("--import-weights")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    root = target_root()
    # Version the source directory by its required revision. This preserves any
    # legacy or interrupted checkout for diagnosis instead of treating it as a
    # disposable clone target.
    source = root / f"source-{SOURCE_REVISION[:12]}"
    weights, venv = root / "weights", root / "venv"
    manifest_path = root / "provision.json"
    log(f"Laya model store: {root}")

    if args.verify_only:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if not manifest:
            raise SystemExit("nothing provisioned yet")
        manifest["paths"] = {"root": str(root), "source": str(source), "weights": str(weights),
                             "venv": str(venv), "python": str(venv_python(venv))}
        source_head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=10)
        if source_head.returncode or source_head.stdout.strip() != SOURCE_REVISION:
            raise SystemExit("external Laya source is not at the pinned revision")
        if str(REPO_ROOT).lower() in json.dumps(manifest["paths"]).lower():
            raise SystemExit("external Laya manifest still points into the workspace")
        expected_files = (manifest.get("weights") or {}).get("files") or {}
        for name in WANTED:
            expected = expected_files.get(name) or {}
            path = weights / name
            if (not path.is_file() or path.stat().st_size <= 0 or
                    path.stat().st_size != int(expected.get("bytes") or -1) or
                    sha256_of(path) != expected.get("sha256")):
                raise SystemExit(f"external pinned weight failed size/hash check: {name}")
        result = verify_worker(venv, source, weights)
        manifest["verified"] = result
        manifest["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log("verify-only OK")
        return 0

    license_seen = hub_license()
    if license_seen != LICENSE_EXPECTED:
        raise SystemExit(f"unexpected license: {license_seen!r}")
    log(f"  license: {license_seen}")

    manifest = {
        "component": "laya",
        "license": license_seen,
        "source": provision_source(source, args.import_source),
        "weights": provision_weights(weights, args.import_weights),
        "venv": provision_venv(venv),
        "paths": {"root": str(root), "source": str(source), "weights": str(weights),
                  "venv": str(venv), "python": str(venv_python(venv))},
        "provisioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    manifest["verified"] = verify_worker(venv, source, weights)
    manifest["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"manifest written: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
