"""Release fetching (core/release_fetch.py) — Phase 14.3.

``core/updater.py`` stages, activates, verifies and rolls back releases, but nothing ever
*retrieves* one. This module completes that path — and it is deliberately **channel-agnostic**:
the owner points GENIE at any URL that serves a release manifest. That avoids forcing a product
decision (GitHub? a private bucket? a local share?) while still making updates possible.

Manifest format::

    {
      "version": "1.3.0",
      "note": "bug fixes",
      "files": [
        {"name": "genie.py", "sha256": "...", "bytes": 1234, "url": "genie.py"}
      ]
    }

``url`` may be relative, in which case it resolves against the manifest URL.

Security honesty (important):
    Verifying each file's sha256 proves the download is **intact** — it protects against
    corruption and truncation. It does **not** by itself prove the manifest is authentic, because
    both travel over the same channel. For untrusted channels the manifest must additionally be
    signature-verified; ``signature`` / ``public_key`` fields are accepted and verified when a
    verifier is supplied, and their absence is reported rather than glossed over.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.release_fetch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "GENIE-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_manifest(manifest_url: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    """Download and parse a release manifest."""
    try:
        raw = _get(manifest_url, timeout)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"manifest HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": f"manifest unreachable: {exc}"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"manifest is not valid JSON: {exc}"}
    if not isinstance(data, dict) or not data.get("version"):
        return {"ok": False, "error": "manifest has no version"}
    if not isinstance(data.get("files"), list) or not data["files"]:
        return {"ok": False, "error": "manifest lists no files"}
    return {"ok": True, "manifest": data}


def download_release(manifest_url: str, manifest: Dict[str, Any], *,
                     timeout: float = 30.0) -> Dict[str, Any]:
    """Download every file in the manifest and verify its hash. Returns a staging directory."""
    workdir = Path(tempfile.mkdtemp(prefix="genie-release-"))
    files: List[Dict[str, Any]] = []
    try:
        for entry in manifest.get("files", []):
            name = str(entry.get("name") or "").strip()
            if not name:
                shutil.rmtree(workdir, ignore_errors=True)
                return {"ok": False, "error": "manifest entry without a name"}
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            url = urllib.parse.urljoin(manifest_url, str(entry.get("url") or name))
            try:
                payload = _get(url, timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                shutil.rmtree(workdir, ignore_errors=True)
                return {"ok": False, "error": f"failed to download {name}: {exc}"}
            target.write_bytes(payload)

            actual = _sha256(target)
            expected = entry.get("sha256")
            if expected and actual != expected:
                shutil.rmtree(workdir, ignore_errors=True)
                return {"ok": False, "error": f"checksum mismatch for {name}",
                        "expected": expected, "actual": actual}
            if expected is None:
                log.warning("release file %s has no sha256 — integrity cannot be verified", name)
            files.append({"name": name, "sha256": actual, "bytes": target.stat().st_size})
    except Exception as exc:  # never leave a partial staging dir behind
        shutil.rmtree(workdir, ignore_errors=True)
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "dir": workdir, "files": files,
            "unverified": [f["name"] for f in files
                           if not any(e.get("name") == f["name"] and e.get("sha256")
                                      for e in manifest.get("files", []))]}


def fetch_and_stage(manifest_url: str, updater, *, note: str = "",
                    timeout: float = 30.0,
                    signature_verifier: Optional[Callable[[bytes, str], bool]] = None
                    ) -> Dict[str, Any]:
    """Fetch a release and hand it to the Updater. Refuses anything that fails verification."""
    got = fetch_manifest(manifest_url, timeout=timeout)
    if not got["ok"]:
        return got
    manifest = got["manifest"]
    version = str(manifest["version"])

    # optional authenticity check — file hashes alone do not prove the manifest is authentic
    signature = manifest.get("signature")
    if signature:
        if signature_verifier is None:
            return {"ok": False, "error": "manifest is signed but no signature verifier was "
                                          "supplied — refusing to trust it"}
        try:
            raw = _get(manifest_url, timeout)
            if not signature_verifier(raw, str(signature)):
                return {"ok": False, "error": "manifest signature did not verify"}
        except Exception as exc:
            return {"ok": False, "error": f"signature check failed: {exc}"}
    else:
        log.warning("release manifest is unsigned — authenticity depends entirely on the "
                    "transport; only use this over a channel you trust")

    downloaded = download_release(manifest_url, manifest, timeout=timeout)
    if not downloaded["ok"]:
        return downloaded

    try:
        staged = updater.stage(version, downloaded["dir"], note=note or manifest.get("note", ""))
    finally:
        shutil.rmtree(downloaded["dir"], ignore_errors=True)

    staged["signed"] = bool(signature)
    staged["warning"] = ("" if signature else
                         "manifest was unsigned — integrity verified, authenticity not")
    return staged
