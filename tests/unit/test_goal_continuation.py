"""Goal continuation & no-progress breaker (re-audit 14.5, donor: DeerFlow).

The safety requirement: **do not create an infinite self-loop.** The most important test here is
the one where the iteration never progresses and never finishes — the driver must still terminate.
"""
from __future__ import annotations

from agents.continuation import (CONTINUE, STOP_BUDGET, STOP_FAILED, STOP_GOAL_MET,
                                 STOP_NO_PROGRESS, STOP_OWNER_BLOCKER, ContinuationState,
                                 GoalContinuation)


def _state():
    return ContinuationState()


# ------------------------------------------------------------------- the gates
def test_continues_when_everything_is_fine():
    out = GoalContinuation().evaluate(_state(), goal_met=False, progress_made=True)
    assert out["decision"] == CONTINUE
    assert out["terminal"] is False


def test_a_met_goal_stops():
    out = GoalContinuation().evaluate(_state(), goal_met=True)
    assert out["decision"] == STOP_GOAL_MET
    assert out["terminal"] is True


def test_a_failed_run_stops():
    out = GoalContinuation().evaluate(_state(), goal_met=False, run_failed=True)
    assert out["decision"] == STOP_FAILED


def test_an_owner_only_blocker_stops_and_waits_for_the_owner():
    out = GoalContinuation().evaluate(_state(), goal_met=False, owner_blocker=True)
    assert out["decision"] == STOP_OWNER_BLOCKER
    assert "owner" in out["reason"]


def test_continuation_budget_is_enforced():
    state = _state()
    state.continuations_used = 5
    out = GoalContinuation(max_continuations=5).evaluate(state, goal_met=False,
                                                         progress_made=True)
    assert out["decision"] == STOP_BUDGET


# --------------------------------------------------------------- the breaker
def test_the_no_progress_breaker_trips():
    """Consecutive fruitless iterations must stop the mission."""
    gc = GoalContinuation(no_progress_limit=2)
    state = _state()
    gc.advance(state, progress_made=False)
    assert gc.evaluate(state, goal_met=False)["decision"] == CONTINUE, "one miss is tolerated"
    gc.advance(state, progress_made=False)
    out = gc.evaluate(state, goal_met=False)
    assert out["decision"] == STOP_NO_PROGRESS
    assert "breaker" in out["reason"]


def test_progress_resets_the_no_progress_counter():
    gc = GoalContinuation(no_progress_limit=2)
    state = _state()
    gc.advance(state, progress_made=False)
    gc.advance(state, progress_made=True)          # recovered
    assert state.consecutive_no_progress == 0
    assert gc.evaluate(state, goal_met=False)["decision"] == CONTINUE


# ------------------------------------------------------- no infinite self-loop
def test_a_never_progressing_iteration_still_terminates():
    """The critical safety test: no infinite self-loop."""
    gc = GoalContinuation(max_continuations=5, no_progress_limit=2)
    state = _state()

    def stuck(_state_):
        return {"goal_met": False, "progress_made": False}   # never finishes, never progresses

    out = gc.run_until_settled(state, stuck)
    assert out["terminal"] is True
    assert out["decision"] == STOP_NO_PROGRESS
    assert len(state.history) <= 5, "must not exceed the continuation budget"


def test_an_iteration_that_claims_nothing_terminates_on_budget():
    gc = GoalContinuation(max_continuations=3, no_progress_limit=99)
    state = _state()
    out = gc.run_until_settled(state, lambda s: {"progress_made": True})
    assert out["terminal"] is True
    assert out["decision"] == STOP_BUDGET
    assert len(state.history) == 3, "hard cap on iterations"


def test_the_driver_stops_as_soon_as_the_goal_is_met():
    gc = GoalContinuation(max_continuations=10)
    state = _state()
    calls = {"n": 0}

    def improving(_s):
        calls["n"] += 1
        return {"goal_met": calls["n"] >= 3, "progress_made": True}

    out = gc.run_until_settled(state, improving)
    assert out["decision"] == STOP_GOAL_MET
    assert calls["n"] == 3, "must not keep iterating after the goal is met"


def test_an_iteration_raising_does_not_hang_the_driver():
    gc = GoalContinuation(max_continuations=4)
    state = _state()
    calls = {"n": 0}

    def flaky(_s):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return {"goal_met": True, "progress_made": True}

    try:
        out = gc.run_until_settled(state, flaky)
        assert out["decision"] == STOP_GOAL_MET
    except RuntimeError:
        pass  # propagating is acceptable; hanging or looping forever would not be
    assert calls["n"] <= 4


# --------------------------------------------------------------------- status
def test_status_reports_remaining_budget():
    gc = GoalContinuation(max_continuations=4)
    state = _state()
    gc.advance(state, progress_made=True)
    status = gc.status(state)
    assert status["remaining"] == 3
    assert status["continuations_used"] == 1
    assert status["no_progress_limit"] == 2
