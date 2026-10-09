"""Context compaction (re-audit 14.5, donor: DeerFlow).

The invariant that matters: **mission truth is never compacted away.** A summary dropping the
goal or a hard constraint is a bug, not a trade-off.
"""
from __future__ import annotations

from agents.contextcompaction import (ARTIFACT, COMPRESSIBLE, PROTECTED, RECEIPT, RECENT,
                                      ContextCompactor)


def _compactor(**kw):
    c = ContextCompactor(**kw)
    c.add("mission.goal", "finish implementation and make all tests pass", kind=PROTECTED)
    c.add("mission.id", "mis_123", kind=PROTECTED)
    c.add("constraint.no_destructive", "never delete user files", kind=PROTECTED)
    return c


def test_protected_mission_truth_survives_compaction():
    c = _compactor(keep_recent=2)
    for n in range(10):
        c.add(f"turn-{n}", "x" * 400, kind=COMPRESSIBLE)

    report = c.compact()

    assert report.protected_preserved is True
    surviving = {i.key: i.content for i in c.items()}
    assert surviving["mission.goal"] == "finish implementation and make all tests pass"
    assert surviving["mission.id"] == "mis_123"
    assert surviving["constraint.no_destructive"] == "never delete user files"


def test_protected_truth_survives_even_a_brutal_token_budget():
    """Space pressure must never shed mission truth."""
    c = _compactor(keep_recent=50)
    for n in range(40):
        c.add(f"turn-{n}", "y" * 4000, kind=COMPRESSIBLE)

    report = c.compact(budget=50)

    assert report.protected_preserved is True
    keys = {i.key for i in c.items()}
    assert {"mission.goal", "mission.id", "constraint.no_destructive"} <= keys


def test_older_compressible_history_is_dropped():
    c = _compactor(keep_recent=2)
    for n in range(6):
        c.add(f"turn-{n}", "z" * 200, kind=COMPRESSIBLE)

    report = c.compact()
    assert len(report.dropped) >= 3
    assert "turn-0" in report.dropped, "oldest goes first"
    assert "turn-5" not in report.dropped, "most recent is kept"


def test_compaction_actually_reduces_tokens():
    c = _compactor(keep_recent=2)
    for n in range(20):
        c.add(f"turn-{n}", "q" * 1000, kind=COMPRESSIBLE)
    report = c.compact()
    assert report.tokens_after < report.tokens_before


def test_receipts_and_artifacts_are_kept_as_references():
    c = _compactor(keep_recent=1)
    c.add("receipt-1", "", kind=RECEIPT, ref="r1-abc")
    c.add("artifact-1", "", kind=ARTIFACT, ref="artifact://build.log")
    c.add("old-turn", "w" * 500, kind=COMPRESSIBLE)
    c.add("new-turn", "w" * 500, kind=COMPRESSIBLE)

    c.compact()
    keys = {i.key for i in c.items()}
    assert "receipt-1" in keys
    assert "artifact-1" in keys


def test_a_summary_is_produced_for_dropped_history():
    c = _compactor(keep_recent=1)
    c.add("turn-old", "we tried approach A and it failed", kind=COMPRESSIBLE)
    c.add("turn-new", "now trying approach B", kind=COMPRESSIBLE)
    report = c.compact()
    assert "compacted history" in report.summary
    assert "turn-old" in report.summary


def test_the_most_recent_items_are_kept_over_older_ones():
    """Most-recently-added wins; the oldest recent item is the first to go."""
    c = ContextCompactor(keep_recent=2)
    c.add("oldest", "a", kind=RECENT)
    c.add("middle", "b", kind=RECENT)
    c.add("newest", "c", kind=RECENT)
    c.compact()
    keys = {i.key for i in c.items()}
    assert "newest" in keys and "middle" in keys
    assert "oldest" not in keys, "the oldest recent item drops first"


def test_status_reports_kind_counts():
    c = _compactor()
    c.add("r", "", kind=RECEIPT)
    c.add("t", "hello", kind=COMPRESSIBLE)
    status = c.status()
    assert status["by_kind"][PROTECTED] == 3
    assert status["by_kind"][RECEIPT] == 1
    assert status["items"] == 5


def test_compacting_twice_is_stable_for_protected_truth():
    c = _compactor(keep_recent=1)
    c.add("t1", "a" * 200, kind=COMPRESSIBLE)
    c.add("t2", "b" * 200, kind=COMPRESSIBLE)
    c.compact()
    c.compact()
    assert c.protected(), "protected items must survive repeated compaction"
    assert len(c.protected()) == 3
