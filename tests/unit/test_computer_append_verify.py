"""Regression: files.append must verify against the appended text, not the file.

Found during release-candidate hardening. `files.append` was registered to
_verify_file_written, which compares the file's ENTIRE content to params["text"].
For append that param is only the fragment being added, so verification failed
every time ("content differs from what was requested") even when the append had
succeeded — a false negative that made a working action look wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import verifier  # noqa: E402


def _ctx(path: Path, text: str, result=None):
    return verifier.VerifyContext(
        capability="files.append",
        params={"path": str(path), "text": text},
        result=result if result is not None else {"path": str(path)},
        before=None,
        after=None,
    )


def test_append_verifies_when_file_ends_with_the_text(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("original more", encoding="utf-8")
    out = verifier._verify_file_appended(_ctx(p, " more"))
    assert out.verified is True, out.detail
    assert "append" in out.detail.lower()


def test_append_fails_when_text_not_at_the_end(tmp_path):
    p = tmp_path / "b.txt"
    p.write_text("original", encoding="utf-8")
    out = verifier._verify_file_appended(_ctx(p, " more"))
    assert out.verified is False
    assert "does not end with" in out.detail


def test_append_fails_when_file_missing(tmp_path):
    p = tmp_path / "missing.txt"
    out = verifier._verify_file_appended(_ctx(p, "x"))
    assert out.verified is False
    assert "does not exist" in out.detail


def test_append_is_registered_to_the_append_verifier():
    mapping = verifier.VERIFIERS if hasattr(verifier, "VERIFIERS") else {}
    if not mapping:
        # find the registry dict by name
        for name in dir(verifier):
            obj = getattr(verifier, name)
            if isinstance(obj, dict) and "files.write" in obj:
                mapping = obj
                break
    assert mapping, "verifier registry not found"
    assert mapping.get("files.append") is verifier._verify_file_appended, \
        "files.append must use the append verifier, not the exact-content one"


def test_write_still_uses_exact_content_verification():
    mapping = {}
    for name in dir(verifier):
        obj = getattr(verifier, name)
        if isinstance(obj, dict) and "files.write" in obj:
            mapping = obj
            break
    assert mapping.get("files.write") is verifier._verify_file_written, \
        "files.write must still require exact content"


def test_realistic_append_sequence_verifies(tmp_path):
    """write then append -> the append must verify (this is what was broken)."""
    p = tmp_path / "c.txt"
    p.write_text("genie baseline", encoding="utf-8")
    p.write_text(p.read_text(encoding="utf-8") + " more", encoding="utf-8")
    out = verifier._verify_file_appended(_ctx(p, " more"))
    assert out.verified is True, out.detail
    assert p.read_text(encoding="utf-8") == "genie baseline more"
