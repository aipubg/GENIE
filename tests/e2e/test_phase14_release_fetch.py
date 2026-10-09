"""Phase 14.3 — release fetching.

Served from a **real local HTTP server**, so these exercise actual network I/O rather than a
mocked response. The security-critical cases are the point: a tampered file must be refused, and a
signed manifest must never be trusted without a verifier.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.release_fetch import fetch_and_stage, fetch_manifest
from core.updater import Updater


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture()
def site(tmp_path):
    """A directory served over HTTP, plus a helper to rebuild it."""
    root = tmp_path / "site"
    root.mkdir()

    def build(files: dict, manifest_extra: dict | None = None, *, manifest_name: str = "manifest.json"):
        for dirpath in list(root.iterdir()):
            if dirpath.is_dir():
                import shutil
                shutil.rmtree(dirpath)
            else:
                dirpath.unlink()
        entries = []
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = content.encode("utf-8")
            target.write_bytes(payload)
            entries.append({"name": name, "sha256": _sha256_bytes(payload),
                            "bytes": len(payload), "url": name})
        manifest = {"version": "1.3.0", "note": "release", "files": entries}
        manifest.update(manifest_extra or {})
        (root / manifest_name).write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    handler = SimpleHTTPRequestHandler
    handler.log_message = lambda *a, **k: None  # keep output clean

    class _Quiet(handler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield build, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# ------------------------------------------------------------------ manifest
def test_manifest_is_fetched_and_parsed(site):
    build, base = site
    build({"genie.py": "print('v1.3')"})
    got = fetch_manifest(f"{base}/manifest.json")
    assert got["ok"] is True
    assert got["manifest"]["version"] == "1.3.0"
    assert len(got["manifest"]["files"]) == 1


def test_unreachable_manifest_is_reported_not_raised(site):
    _, base = site
    got = fetch_manifest("http://127.0.0.1:9/manifest.json")
    # Behind a proxy this can surface as "HTTP 502" rather than a refused connection; either way
    # it must be reported as a failure, never raised and never treated as success.
    assert got["ok"] is False, got
    assert got["error"], "an unreachable manifest must report why"


def test_missing_manifest_returns_an_http_error(site):
    build, base = site
    build({"a.txt": "x"})
    got = fetch_manifest(f"{base}/does-not-exist.json")
    assert got["ok"] is False and "HTTP" in got["error"]


def test_a_manifest_without_a_version_is_rejected(site):
    build, base = site
    build({"a.txt": "x"}, {"version": ""})
    got = fetch_manifest(f"{base}/manifest.json")
    assert got["ok"] is False and "no version" in got["error"]


def test_a_manifest_with_no_files_is_rejected(site):
    build, base = site
    build({"a.txt": "x"}, {"files": []})
    got = fetch_manifest(f"{base}/manifest.json")
    assert got["ok"] is False and "no files" in got["error"]


# ------------------------------------------------------------- fetch + stage
def test_a_release_is_downloaded_verified_and_staged(site, tmp_path):
    build, base = site
    build({"genie.py": "print('v1.3')", "core/mod.py": "X = 2"})
    updater = Updater(tmp_path / "releases")

    out = fetch_and_stage(f"{base}/manifest.json", updater)
    assert out.get("ok", True) is not False, out
    assert "1.3.0" in [r["version"] for r in updater.releases()]
    # the staged content really arrived
    assert (updater.releases_dir / "1.3.0" / "genie.py").read_text() == "print('v1.3')"
    # and it verifies, so it can be activated
    assert updater.verify("1.3.0")["ok"] is True
    assert updater.activate("1.3.0")["ok"] is True
    assert updater.active() == "1.3.0"


def test_a_tampered_file_is_refused_and_nothing_is_staged(site, tmp_path):
    build, base = site
    build({"genie.py": "good"})
    updater = Updater(tmp_path / "releases")
    # rebuild with a deliberately wrong hash in the manifest
    build({"genie.py": "good"}, {"files": [
        {"name": "genie.py", "sha256": "0" * 64, "bytes": 4, "url": "genie.py"}]})
    out = fetch_and_stage(f"{base}/manifest.json", updater)
    assert out["ok"] is False
    assert "checksum mismatch" in out["error"]
    assert updater.releases() == [], "a failed fetch must not leave a release behind"


def test_a_missing_file_is_reported(site, tmp_path):
    build, base = site
    build({"a.txt": "x"}, {"files": [
        {"name": "ghost.bin", "sha256": "0" * 64, "bytes": 3, "url": "ghost.bin"}]})
    updater = Updater(tmp_path / "releases")
    out = fetch_and_stage(f"{base}/manifest.json", updater)
    assert out["ok"] is False and "failed to download" in out["error"]


# ------------------------------------------------------------------ signing
def test_a_signed_manifest_without_a_verifier_is_refused(site, tmp_path):
    build, base = site
    build({"genie.py": "v"}, {"signature": "deadbeef"})
    updater = Updater(tmp_path / "releases")
    out = fetch_and_stage(f"{base}/manifest.json", updater)
    assert out["ok"] is False
    assert "no signature verifier" in out["error"], \
        "a signed manifest must never be trusted implicitly"
    assert updater.releases() == []


def test_a_bad_signature_is_refused(site, tmp_path):
    build, base = site
    build({"genie.py": "v"}, {"signature": "deadbeef"})
    updater = Updater(tmp_path / "releases")
    out = fetch_and_stage(f"{base}/manifest.json", updater,
                          signature_verifier=lambda raw, sig: False)
    assert out["ok"] is False and "did not verify" in out["error"]


def test_a_good_signature_is_accepted(site, tmp_path):
    build, base = site
    build({"genie.py": "v"}, {"signature": "abc123"})
    updater = Updater(tmp_path / "releases")
    out = fetch_and_stage(f"{base}/manifest.json", updater,
                          signature_verifier=lambda raw, sig: sig == "abc123")
    assert out.get("ok", True) is not False, out
    assert out.get("signed") is True
    assert updater.active() is None  # staged, not activated — activation is a separate decision


def test_an_unsigned_manifest_works_but_says_so(site, tmp_path):
    """Unsigned is allowed (channel may be trusted) but must be disclosed, not hidden."""
    build, base = site
    build({"genie.py": "v"})
    updater = Updater(tmp_path / "releases")
    out = fetch_and_stage(f"{base}/manifest.json", updater)
    assert out.get("ok", True) is not False, out
    assert out.get("signed") is False
    assert "unsigned" in out.get("warning", ""), \
        "the owner must be told authenticity was not proven"
