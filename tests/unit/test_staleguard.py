"""Read-before-write protection (re-audit 14.5, donor: DeerFlow).

Demonstrates the exact failure a lock does not prevent: agent A reads, agent B edits, agent A
writes its stale assumptions. The guard must refuse that write.
"""
from __future__ import annotations

from core.staleguard import StaleWriteGuard, version_of


def test_version_is_empty_for_a_missing_file(tmp_path):
    assert version_of(tmp_path / "nope.py") == ""


def test_version_is_a_content_hash(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1", encoding="utf-8")
    first = version_of(target)
    assert len(first) == 64
    target.write_text("x = 2", encoding="utf-8")
    assert version_of(target) != first, "content change must change the version"


def test_observe_records_what_a_writer_saw(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    guard = StaleWriteGuard()
    seen = guard.observe(target)
    assert guard.last_seen(target) == seen
    assert guard.is_stale(target, seen) is False


def test_a_write_based_on_the_current_version_succeeds(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    guard = StaleWriteGuard()
    version = guard.observe(target)
    out = guard.write(target, "v2", expected_version=version)
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "v2"


def test_a_stale_write_is_refused_and_the_file_is_untouched(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    guard = StaleWriteGuard()
    version = guard.observe(target)

    target.write_text("v2-by-someone-else", encoding="utf-8")  # concurrent edit

    out = guard.write(target, "my-edit", expected_version=version)
    assert out["ok"] is False
    assert out["stale"] is True
    assert "re-read" in out["detail"]
    assert target.read_text(encoding="utf-8") == "v2-by-someone-else", \
        "a refused write must not clobber the newer content"


def test_the_classic_two_agent_clobber_is_prevented(tmp_path):
    target = tmp_path / "shared.py"
    target.write_text("original", encoding="utf-8")

    guard_a, guard_b = StaleWriteGuard(), StaleWriteGuard()
    seen_by_a = guard_a.observe(target)          # A reads

    assert guard_b.write(target, "B was here",                  # B edits
                         expected_version=guard_b.observe(target))["ok"]

    assert guard_a.write(target, "A was here",                  # A writes stale
                         expected_version=seen_by_a)["ok"] is False
    assert target.read_text(encoding="utf-8") == "B was here"


def test_check_reports_expected_and_current(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    guard = StaleWriteGuard()
    old = guard.observe(target)
    target.write_text("v2", encoding="utf-8")
    result = guard.check(target, old)
    assert result["stale"] is True
    assert result["expected_version"] != result["current_version"]


def test_writing_a_new_file_with_empty_expected_version_works(tmp_path):
    target = tmp_path / "fresh.py"
    out = StaleWriteGuard().write(target, "new", expected_version="")
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "new"


def test_read_then_write_applies_a_transform(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("hello", encoding="utf-8")
    out = StaleWriteGuard().read_then_write(target, lambda text: text.upper())
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "HELLO"


def test_read_then_write_reports_transform_failure(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x", encoding="utf-8")

    def boom(text):
        raise RuntimeError("no")

    out = StaleWriteGuard().read_then_write(target, boom)
    assert out["ok"] is False
    assert "transform failed" in out.get("error", "")
    assert target.read_text(encoding="utf-8") == "x", "file must be unchanged"


def test_forget_clears_the_observed_version(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v", encoding="utf-8")
    guard = StaleWriteGuard()
    guard.observe(target)
    guard.forget(target)
    assert guard.last_seen(target) is None
