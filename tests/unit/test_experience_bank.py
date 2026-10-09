"""P5 Youtu-Agent gap: trajectory summarisation + semantic group advantage.

Deterministic. Lessons are derived from recorded outcomes only — with too few
rollouts the bank reports insufficient evidence instead of inventing one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db import Database                          # noqa: E402
from experience.bank import MIN_GROUP, ExperienceBank  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def bank(db):
    return ExperienceBank(db)


def _steps(caps, ok=True):
    return [{"capability": c, "ok": ok} for c in caps]


# ------------------------------------------------------------- summarisation
def test_summary_is_factual(bank):
    s = bank.summarise(_steps(["search", "extract"], True), success=True,
                       failures=["timeout"])
    assert "2 step(s)" in s
    assert "search -> extract" in s
    assert "outcome: success" in s
    assert "timeout" in s


def test_summary_handles_no_steps(bank):
    assert bank.summarise([], success=False) == "no steps recorded"


# ---------------------------------------------------------------- recording
def test_record_persists_trajectory(bank):
    out = bank.record(task_type="web.extract", steps=_steps(["fetch"]),
                      success=True, tools=["scrapling"])
    assert out["success"] is True
    assert out["step_count"] == 1
    trajs = bank.trajectories("web.extract")
    assert len(trajs) == 1
    assert trajs[0]["tools"] == ["scrapling"]


def test_trajectories_are_scoped_by_task(bank):
    bank.record(task_type="a", steps=_steps(["x"]), success=True)
    bank.record(task_type="b", steps=_steps(["y"]), success=True)
    assert len(bank.trajectories("a")) == 1
    assert len(bank.trajectories("b")) == 1


# ------------------------------------------------------------ group advantage
def test_insufficient_rollouts_reports_no_lesson(bank):
    for _ in range(MIN_GROUP - 1):
        bank.record(task_type="t", steps=_steps(["x"]), success=True)
    adv = bank.group_advantage("t")
    assert adv["ok"] is False
    assert "insufficient evidence" in adv["reason"]
    assert adv["lessons"] == []


def test_only_successes_cannot_produce_advantage(bank):
    for _ in range(4):
        bank.record(task_type="t", steps=_steps(["x"]), success=True)
    adv = bank.group_advantage("t")
    assert adv["ok"] is False
    assert "failure" in adv["reason"]


def test_tool_used_only_by_winners_is_an_advantage(bank):
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["fetch"]), success=True,
                    tools=["cache"])
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["fetch"]), success=False,
                    tools=["retry"], failures=["timeout"])
    adv = bank.group_advantage("t")
    assert adv["ok"] is True
    kinds = {l["kind"] for l in adv["lessons"]}
    assert "tool_advantage" in kinds, adv["lessons"]
    assert any("cache" in l["text"] for l in adv["lessons"])


def test_tool_used_only_by_losers_is_a_risk(bank):
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["safe"])
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["unsafe"], failures=["boom"])
    adv = bank.group_advantage("t")
    assert any(l["kind"] == "tool_risk" and "unsafe" in l["text"]
               for l in adv["lessons"]), adv["lessons"]


def test_recurring_failure_mode_is_surfaced(bank):
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["a"])
    for _ in range(3):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["b"], failures=["rate limited"])
    adv = bank.group_advantage("t")
    assert any(l["kind"] == "failure_mode" and "rate limited" in l["text"]
               for l in adv["lessons"]), adv["lessons"]


def test_best_strategy_is_ranked(bank):
    bank.record(task_type="t", steps=_steps(["x"]), success=True, strategy="direct")
    bank.record(task_type="t", steps=_steps(["x"]), success=True, strategy="direct")
    bank.record(task_type="t", steps=_steps(["x"]), success=False, strategy="crawl")
    adv = bank.group_advantage("t")
    assert any(l["kind"] == "strategy_advantage" and "direct" in l["text"]
               for l in adv["lessons"]), adv["lessons"]


def test_lessons_are_sorted_by_support(bank):
    for _ in range(3):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["z"])
    for _ in range(3):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["q"], failures=["err"])
    adv = bank.group_advantage("t")
    supports = [l["support"] for l in adv["lessons"]]
    assert supports == sorted(supports, reverse=True)


# ---------------------------------------------------------------- the bank
def test_refresh_then_retrieve(bank):
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["good"])
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["bad"], failures=["nope"])
    lessons = bank.refresh("t")
    assert lessons, "refresh should derive lessons"
    stored = bank.retrieve("t")
    assert len(stored) == len(lessons)
    assert stored[0]["task_type"] == "t"


def test_refresh_without_evidence_stores_nothing(bank):
    bank.record(task_type="t", steps=_steps(["x"]), success=True)
    assert bank.refresh("t") == []
    assert bank.retrieve("t") == []


def test_refresh_replaces_stale_lessons(bank, db):
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["v1"])
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["v2"], failures=["e"])
    first = bank.refresh("t")
    # add evidence that changes the picture
    for _ in range(3):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["v3"])
    second = bank.refresh("t")
    assert len(bank.retrieve("t")) == len(second)
    assert bank.retrieve("t") != []


def test_retrieve_respects_min_confidence(bank):
    """A weakly-supported lesson is filtered out by a confidence threshold.

    'g' appears in only 1 of 4 wins (confidence 0.25); the others are strongly
    supported, so raising the threshold must drop exactly that one.
    """
    bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["g"])
    for _ in range(3):
        bank.record(task_type="t", steps=_steps(["x"]), success=True, tools=["other"])
    for _ in range(2):
        bank.record(task_type="t", steps=_steps(["x"]), success=False,
                    tools=["b"], failures=["e"])
    bank.refresh("t")

    everything = bank.retrieve("t", min_confidence=0.0)
    assert any("g" in l["text"] for l in everything), everything
    assert any(l["confidence"] < 0.5 for l in everything), \
        "expected at least one weakly-supported lesson"

    strong = bank.retrieve("t", min_confidence=0.5)
    assert not any("'g'" in l["text"] for l in strong), \
        f"weakly-supported lesson survived a 0.5 threshold: {strong}"
    assert len(strong) < len(everything)


def test_bank_is_per_task(bank):
    for _ in range(2):
        bank.record(task_type="one", steps=_steps(["x"]), success=True, tools=["a"])
        bank.record(task_type="one", steps=_steps(["x"]), success=False,
                    tools=["b"], failures=["e"])
    bank.refresh("one")
    assert bank.retrieve("one")
    assert bank.retrieve("two") == []
