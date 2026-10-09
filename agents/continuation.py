"""Goal continuation & no-progress breaker (agents/continuation.py) — re-audit 14.5.

Capability donor: **DeerFlow** (``/goal`` continuation with capped continuation count).

The owner's requirement: *"mission can continue automatically only when the goal is unfinished,
there is no owner-only blocker, progress is still being made, and continuation budget remains.
Add a no-progress breaker. Do NOT create an infinite self-loop."*

So the default is **stop**, and every automatic continuation must justify itself against four
independent gates. If any gate fails, the mission stops and says *why* — it never silently loops.

The no-progress breaker is the safety-critical part: an agent that keeps running without making
progress burns tokens forever and looks alive while achieving nothing. After
``no_progress_limit`` consecutive iterations with no progress, continuation is refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.continuation")

CONTINUE = "continue"
STOP_GOAL_MET = "stop:goal-met"
STOP_BUDGET = "stop:continuation-budget-exhausted"
STOP_NO_PROGRESS = "stop:no-progress"
STOP_OWNER_BLOCKER = "stop:owner-blocker-needed"
STOP_FAILED = "stop:run-failed"

#: gates that are legitimate reasons to stop, in the order they are checked
STOP_REASONS = {
    STOP_GOAL_MET: "the goal is complete — nothing left to continue",
    STOP_FAILED: "the last run failed and cannot be retried automatically",
    STOP_OWNER_BLOCKER: "an owner-only blocker needs the owner's decision",
    STOP_NO_PROGRESS: "no progress across consecutive iterations — breaker tripped",
    STOP_BUDGET: "continuation budget exhausted",
}


@dataclass
class ContinuationState:
    """Per-mission continuation bookkeeping."""

    continuations_used: int = 0
    consecutive_no_progress: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"continuations_used": self.continuations_used,
                "consecutive_no_progress": self.consecutive_no_progress,
                "history": list(self.history), "stopped_reason": self.stopped_reason}


class GoalContinuation:
    """Decides whether a mission may continue automatically."""

    def __init__(self, *, max_continuations: int = 5, no_progress_limit: int = 2):
        self.max_continuations = int(max_continuations)
        self.no_progress_limit = int(no_progress_limit)

    # -------------------------------------------------------------- evaluate
    def evaluate(self, state: ContinuationState, *, goal_met: bool,
                 run_failed: bool = False, owner_blocker: bool = False,
                 progress_made: bool = False) -> Dict[str, Any]:
        """Apply the four gates. Returns a decision that always explains itself."""
        # gate 1 — is there anything left to do?
        if goal_met:
            return self._decision(STOP_GOAL_MET, state, terminal=True)
        # gate 2 — did the run actually work?
        if run_failed:
            return self._decision(STOP_FAILED, state, terminal=True)
        # gate 3 — does this need a human?
        if owner_blocker:
            return self._decision(STOP_OWNER_BLOCKER, state, terminal=True)
        # gate 4a — the breaker: are we actually getting anywhere?
        if state.consecutive_no_progress >= self.no_progress_limit:
            return self._decision(STOP_NO_PROGRESS, state, terminal=True)
        # gate 4b — budget
        if state.continuations_used >= self.max_continuations:
            return self._decision(STOP_BUDGET, state, terminal=True)

        return {"decision": CONTINUE, "reason": "goal unfinished, no owner-only blocker, "
                                                "progress is being made, budget remains",
                "continuations_used": state.continuations_used,
                "remaining": self.max_continuations - state.continuations_used,
                "terminal": False}

    # --------------------------------------------------------------- advance
    def advance(self, state: ContinuationState, *, progress_made: bool) -> ContinuationState:
        """Record the outcome of one iteration."""
        if progress_made:
            state.consecutive_no_progress = 0
        else:
            state.consecutive_no_progress += 1
        state.continuations_used += 1
        state.history.append({"iteration": state.continuations_used,
                              "progress": bool(progress_made)})
        return state

    # ------------------------------------------------------------------ run
    def run_until_settled(self, state: ContinuationState,
                          iteration: "callable") -> Dict[str, Any]:
        """Drive iterations until a stop decision or the budget is spent.

        ``iteration(state)`` returns a dict with ``goal_met``, ``run_failed``, ``owner_blocker``
        and ``progress_made``. This can never loop forever: the budget gate is checked every pass.
        """
        while True:
            outcome = iteration(state) or {}
            goal_met = bool(outcome.get("goal_met"))
            run_failed = bool(outcome.get("run_failed"))
            owner_blocker = bool(outcome.get("owner_blocker"))
            progress = bool(outcome.get("progress_made"))

            self.advance(state, progress_made=progress)
            decision = self.evaluate(state, goal_met=goal_met, run_failed=run_failed,
                                     owner_blocker=owner_blocker, progress_made=progress)
            if decision["terminal"]:
                state.stopped_reason = decision["decision"]
                decision["iterations"] = len(state.history)
                decision["state"] = state.to_dict()
                return decision
            if state.continuations_used >= self.max_continuations:
                # belt and braces: never exceed the budget even if evaluate() is overridden
                state.stopped_reason = STOP_BUDGET
                return {"decision": STOP_BUDGET, "reason": STOP_REASONS[STOP_BUDGET],
                        "terminal": True, "iterations": len(state.history),
                        "state": state.to_dict()}

    # -------------------------------------------------------------- internal
    def _decision(self, decision: str, state: ContinuationState, *,
                  terminal: bool) -> Dict[str, Any]:
        state.stopped_reason = decision
        log.info("continuation decision: %s", decision)
        return {"decision": decision, "reason": STOP_REASONS.get(decision, decision),
                "terminal": terminal, "continuations_used": state.continuations_used,
                "consecutive_no_progress": state.consecutive_no_progress}

    def status(self, state: ContinuationState) -> Dict[str, Any]:
        return {"continuations_used": state.continuations_used,
                "max_continuations": self.max_continuations,
                "remaining": max(0, self.max_continuations - state.continuations_used),
                "consecutive_no_progress": state.consecutive_no_progress,
                "no_progress_limit": self.no_progress_limit,
                "stopped_reason": state.stopped_reason}
