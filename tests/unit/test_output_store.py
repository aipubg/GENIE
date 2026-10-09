"""Oversized tool-output handling (re-audit 14.5; donors: DeerFlow + Strix).

The point is cost and context: a huge log must not be carried in model context, while the part that
matters (the failure at the END) must survive. Truncating to head alone hides errors.
"""
from __future__ import annotations

from agents.outputstore import ToolOutputStore


def test_a_small_output_is_passed_through_untouched():
    store = ToolOutputStore()
    out = store.maybe_elide("hello world")
    assert out["elided"] is False
    assert out["content"] == "hello world"
    assert out["ref"] == ""


def test_an_oversized_output_is_elided_and_spilled():
    store = ToolOutputStore(threshold=100)
    out = store.maybe_elide("x" * 5000)
    assert out["elided"] is True
    assert out["bytes"] == 5000
    assert out["shown_bytes"] < out["bytes"]
    assert out["ref"], "a reference is needed to retrieve the full output"
    assert "elided" in out["note"]


def test_the_full_output_is_retrievable_by_reference(tmp_path):
    store = ToolOutputStore(root=tmp_path / "out", threshold=100)
    payload = "BEGIN" + ("y" * 3000) + "END"
    out = store.maybe_elide(payload)
    assert store.fetch(out["ref"]) == payload, "nothing may be lost by eliding"


def test_both_head_and_tail_are_preserved():
    """Failures live at the end — a head-only truncation would hide them."""
    store = ToolOutputStore(threshold=100, head=50, tail=50)
    payload = "START-MARKER" + ("z" * 5000) + "TRACEBACK: assertion failed"
    out = store.maybe_elide(payload)
    assert "START-MARKER" in out["content"], "head (context) must survive"
    assert "TRACEBACK: assertion failed" in out["content"], "tail (failure) must survive"
    assert "omitted" in out["content"]


def test_omitted_byte_count_is_accurate(tmp_path):
    store = ToolOutputStore(root=tmp_path / "out", threshold=100, head=40, tail=40)
    payload = "a" * 1000
    out = store.maybe_elide(payload)
    assert out["bytes"] == 1000
    assert out["shown_bytes"] + out["omitted_bytes"] >= out["bytes"] - 200


def test_works_without_a_filesystem_root():
    """In-memory fallback, so this is usable in tests and minimal deployments."""
    store = ToolOutputStore(threshold=50)          # no root
    out = store.maybe_elide("q" * 900)
    assert out["elided"] is True
    assert store.fetch(out["ref"]) == "q" * 900


def test_references_are_distinct():
    store = ToolOutputStore(threshold=10)
    a = store.maybe_elide("a" * 100)["ref"]
    b = store.maybe_elide("b" * 100)["ref"]
    assert a != b


def test_status_reports_configuration(tmp_path):
    store = ToolOutputStore(root=tmp_path / "out", threshold=100)
    store.maybe_elide("w" * 500)
    status = store.status()
    assert status["stored"] >= 1
    assert status["threshold"] == 100
