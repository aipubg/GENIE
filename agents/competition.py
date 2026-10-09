"""Candidate competition for one task (roadmap §16-§18).

Several agents attempt the SAME task independently; an INDEPENDENT verifier
scores the attempts; the best survives and the rest are discarded. Every
attempt — winner or loser — is written to the experience bank, so failures
teach as much as successes.

The rules this module exists to enforce, because they are the ones a naive
"just run them all and take the first answer" implementation breaks:

1. **The verifier is not a contestant.** A runner may never verify its own
   output; `verifier is runner` is rejected at construction. Scoring is blind:
   the verifier receives the task and the output only, never the agent id, so
   it cannot favour a candidate by identity.

2. **All candidates failing is a result, not a problem to paper over.** The
   outcome is `needs_revision` and every failure reason is returned. The engine
   never promotes a loser because something has to be returned.

3. **Exactly one commit stage.** Side effects (publish, push, write outside the
   workspace) run ONCE, after a winner exists. Contestants must not perform
   them during the attempt — otherwise two agents both push and the "winner"
   is whoever happened to be last.

4. **Cancellation stops everybody.** The cancel event is checked before each
   attempt and between attempts; a partially finished competition returns what
   it has with outcome `cancelled`, rather than completing one more round.

5. **Cost is a first-class decision.** Two candidates by default; three or four
   only when the remaining budget can absorb them.

This engine composes the existing team/runner/budget/experience machinery. It
does not replace it and does not add a second execution stack.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.contracts import AgentDefinition
from core.logging_setup import get_logger
from agents.contracts import TaskNode

log = get_logger("agents.competition")

#: runner(agent, task, context) -> output dict
Runner = Callable[[AgentDefinition, TaskNode, Dict[str, Any]], Dict[str, Any]]
#: verifier(task, output) -> {"ok": bool, "score": float, "notes": str}
Verifier = Callable[[TaskNode, Dict[str, Any]], Dict[str, Any]]
#: commit(task, winning_output) -> anything; called at most once
CommitFn = Callable[[TaskNode, Dict[str, Any]], Dict[str, Any]]

DEFAULT_CANDIDATES = 2
MAX_CANDIDATES = 4

OUTCOME_WINNER = "winner"
OUTCOME_NEEDS_REVISION = "needs_revision"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_NO_CANDIDATES = "no_candidates"

# Fraction of the task budget that must remain before we spend on a third or
# fourth attempt. Below 60% the answer is "we cannot afford to be thorough",
# and the honest response is to run two and say so.
_THIRD_AT = 0.60
_FOURTH_AT = 0.85


@dataclass
class CandidateResult:
    """One contestant's attempt and how the verifier judged it."""

    candidate_id: str
    agent_id: str = ""
    attempted: bool = False
    ok: bool = False                 # produced an output without raising
    accepted: bool = False           # verifier says the criteria are met
    score: float = 0.0
    notes: str = ""
    error: str = ""
    output: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"candidate_id": self.candidate_id, "agent_id": self.agent_id,
                "attempted": self.attempted, "ok": self.ok, "accepted": self.accepted,
                "score": round(self.score, 6), "notes": self.notes,
                "error": self.error, "elapsed_ms": self.elapsed_ms}


@dataclass
class CompetitionResult:
    """The outcome of one task's competition."""

    task_id: str = ""
    outcome: str = OUTCOME_NO_CANDIDATES
    winner: Optional[CandidateResult] = None
    candidates: List[CandidateResult] = field(default_factory=list)
    requested: int = 0
    ran: int = 0
    elapsed_ms: int = 0
    committed: bool = False
    commit_result: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def has_winner(self) -> bool:
        return self.winner is not None

    def failure_reasons(self) -> List[str]:
        """Why each rejected candidate failed — what a revision must address."""
        reasons = []
        for c in self.candidates:
            if c.accepted:
                continue
            bits = [b for b in (c.error, c.notes) if b]
            reasons.append(f"{c.candidate_id}: " + ("; ".join(bits) or "rejected by verifier"))
        return reasons

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "outcome": self.outcome,
                "has_winner": self.has_winner,
                "winner": self.winner.to_dict() if self.winner else None,
                "candidates": [c.to_dict() for c in self.candidates],
                "requested": self.requested, "ran": self.ran,
                "elapsed_ms": self.elapsed_ms, "committed": self.committed,
                "commit_result": self.commit_result,
                "failure_reasons": self.failure_reasons(), "note": self.note}


class CompetitionEngine:
    """Run N independent attempts at one task and keep the verified best."""

    def __init__(self, *, runner: Optional[Runner] = None,
                 verifier: Optional[Verifier] = None,
                 experience=None, audit=None, db=None,
                 default_candidates: int = DEFAULT_CANDIDATES,
                 max_candidates: int = MAX_CANDIDATES) -> None:
        if runner is not None and verifier is not None and runner is verifier:
            # Rule 1: a contestant must never grade its own work.
            raise ValueError("verifier must be independent of the runner")
        self.runner = runner
        self.verifier = verifier
        self.experience = experience
        self.audit = audit
        self.db = db
        self.default_candidates = max(1, int(default_candidates))
        self.max_candidates = max(self.default_candidates, int(max_candidates))
        self._lock = threading.RLock()

    # ------------------------------------------------------------- costing
    @staticmethod
    def budget_ratio(task: TaskNode) -> Optional[float]:
        """Fraction of the task budget still unspent, or None if unknown.

        Only finite dimensions count. A task whose budget has no finite
        dimension is NOT treated as fully funded: an absent limit is absence of
        information, not permission to spend. It falls back to the default
        candidate count. Being generous with someone else's wallet requires
        evidence — a real, finite allowance that is still mostly unspent.
        """
        budget = getattr(task, "budget", None)
        if budget is None:
            return None
        pairs = (("tokens", budget.tokens, budget.tokens_used),
                 ("cost_usd", budget.cost_usd, budget.cost_used),
                 ("model_calls", budget.model_calls, budget.model_calls_used))
        ratios = [max(0.0, (allowance - used) / allowance)
                  for _, allowance, used in pairs if allowance]
        if not ratios:
            return None
        return min(ratios)

    def candidate_count(self, task: TaskNode, *, requested: Optional[int] = None) -> int:
        """How many attempts this task can justify. Two unless the budget affords more."""
        if requested is not None:
            return max(1, min(int(requested), self.max_candidates))
        ratio = self.budget_ratio(task)
        if ratio is None:
            return self.default_candidates
        if ratio >= _FOURTH_AT:
            return min(4, self.max_candidates)
        if ratio >= _THIRD_AT:
            return min(3, self.max_candidates)
        return self.default_candidates

    # -------------------------------------------------------------- competing
    def compete(self, task: TaskNode, candidates: List[AgentDefinition], *,
                criteria: str = "", requested: Optional[int] = None,
                cancel_event: Optional[threading.Event] = None,
                context: Optional[Dict[str, Any]] = None) -> CompetitionResult:
        """Run the attempts and return the verified best, or `needs_revision`."""
        started = time.time()
        pool = [c for c in (candidates or []) if c is not None]
        count = self.candidate_count(task, requested=requested)
        chosen = pool[:count]
        result = CompetitionResult(task_id=getattr(task, "task_id", ""),
                                   requested=count, candidates=[])

        if not chosen:
            result.outcome = OUTCOME_NO_CANDIDATES
            result.note = "no candidates supplied"
            result.elapsed_ms = int((time.time() - started) * 1000)
            return result

        if cancel_event is not None and cancel_event.is_set():
            result.outcome = OUTCOME_CANCELLED
            result.note = "cancelled before any attempt started"
            result.elapsed_ms = int((time.time() - started) * 1000)
            return result

        for index, agent in enumerate(chosen):
            if cancel_event is not None and cancel_event.is_set():
                result.outcome = OUTCOME_CANCELLED
                result.note = f"cancelled after {index} attempt(s)"
                break
            result.candidates.append(self._attempt(task, agent, index, criteria,
                                                   context or {}))

        result.ran = len(result.candidates)
        if result.outcome != OUTCOME_CANCELLED:
            accepted = [c for c in result.candidates if c.accepted]
            if accepted:
                best = max(accepted, key=lambda c: (c.score, -c.elapsed_ms))
                result.winner = best
                result.outcome = OUTCOME_WINNER
                result.note = (f"{len(accepted)} of {result.ran} attempt(s) met the "
                               f"criteria; best score {best.score:.3f}")
            else:
                # Rule 2: never promote a loser just to have something to return.
                result.outcome = OUTCOME_NEEDS_REVISION
                result.note = (f"all {result.ran} attempt(s) failed verification; "
                               f"the task needs revision, not a guess")
        result.elapsed_ms = int((time.time() - started) * 1000)
        self._audit(task, result)
        return result

    def compete_and_commit(self, task: TaskNode, candidates: List[AgentDefinition], *,
                           commit: Optional[CommitFn] = None, criteria: str = "",
                           requested: Optional[int] = None,
                           cancel_event: Optional[threading.Event] = None,
                           context: Optional[Dict[str, Any]] = None) -> CompetitionResult:
        """Compete, then apply side effects ONCE for the winner (rule 3)."""
        result = self.compete(task, candidates, criteria=criteria, requested=requested,
                              cancel_event=cancel_event, context=context)
        if not result.has_winner:
            result.note = (result.note + " — nothing was committed").strip()
            return result
        if commit is None:
            return result
        try:
            outcome = commit(task, result.winner.output) or {}
            result.committed = True
            result.commit_result = outcome if isinstance(outcome, dict) else {"result": outcome}
        except Exception as exc:  # noqa: BLE001 - a failed commit must not look like success
            result.committed = False
            result.commit_result = {"error": str(exc)}
            result.note = f"winner chosen but the commit stage failed: {exc}"
            log.warning("commit stage failed for %s: %s", result.task_id, exc)
        return result

    # ---------------------------------------------------------------- attempt
    def _attempt(self, task: TaskNode, agent: AgentDefinition, index: int,
                 criteria: str, context: Dict[str, Any]) -> CandidateResult:
        candidate_id = f"cand{index + 1}"
        out = CandidateResult(candidate_id=candidate_id,
                              agent_id=getattr(agent, "agent_id", "") or "")
        began = time.time()
        if self.runner is None:
            out.error = "no runner configured"
            return self._finish(out, task, began)
        try:
            produced = self.runner(agent, task, dict(context)) or {}
            out.attempted = True
            out.ok = True
            out.output = produced if isinstance(produced, dict) else {"result": produced}
        except Exception as exc:  # noqa: BLE001 - one contestant dying must not kill the round
            out.attempted = True
            out.ok = False
            out.error = str(exc)[:400]
            return self._finish(out, task, began)
        self._verify(out, task, criteria)
        return self._finish(out, task, began)

    def _verify(self, out: CandidateResult, task: TaskNode, criteria: str) -> None:
        if self.verifier is None:
            # Without an independent judge there is no competition, only noise.
            # Say so rather than pretending the first answer won on merit.
            out.accepted = False
            out.notes = "no verifier configured; nothing can be accepted"
            return
        try:
            # Rule 1 (blind): task + output only. The agent id never crosses.
            judgement = self.verifier(task, out.output) or {}
        except Exception as exc:  # noqa: BLE001
            out.accepted = False
            out.notes = f"verifier failed: {str(exc)[:200]}"
            return
        if isinstance(judgement, bool):
            out.accepted = judgement
            out.score = 1.0 if judgement else 0.0
            return
        if not isinstance(judgement, dict):
            out.accepted = False
            out.notes = "verifier returned an unusable result"
            return
        out.accepted = bool(judgement.get("ok", False))
        try:
            out.score = float(judgement.get("score", 0.0))
        except (TypeError, ValueError):
            out.score = 0.0
        out.notes = str(judgement.get("notes", ""))[:400]
        if criteria and not out.notes:
            out.notes = criteria

    def _finish(self, out: CandidateResult, task: TaskNode, began: float) -> CandidateResult:
        out.elapsed_ms = int((time.time() - began) * 1000)
        self._record(out, task)
        return out

    # ------------------------------------------------------------- experience
    def _record(self, out: CandidateResult, task: TaskNode) -> None:
        """Every attempt is experience — a loss teaches as much as a win."""
        if self.experience is None:
            return
        try:
            self.experience.record(
                task_type=str(getattr(task, "capability", "") or "task"),
                role=str(getattr(task, "required_role", "") or "worker"),
                strategy=f"competition:{out.candidate_id}",
                provider=str(getattr(task, "provider_used", "") or ""),
                tools=[],
                success=bool(out.accepted),
                failures=[out.error or out.notes] if not out.accepted else [],
                latency_ms=out.elapsed_ms,
                cost_usd=0.0,
                mission_id=str(getattr(task, "mission_id", "") or ""))
        except Exception as exc:  # noqa: BLE001 - experience is never mission-critical
            log.debug("experience record failed: %s", exc)

    def _audit(self, task: TaskNode, result: CompetitionResult) -> None:
        if self.audit is None:
            return
        try:
            self.audit.record(who="system", action="agent.competition",
                              why=f"{result.outcome}: {result.note}"[:180],
                              mission_id=str(getattr(task, "mission_id", "") or ""),
                              result=f"{result.ran} attempt(s)")
        except Exception as exc:  # noqa: BLE001
            log.debug("competition audit failed: %s", exc)
