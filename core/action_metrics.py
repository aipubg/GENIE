"""Wrong-action rate instrumentation (core/action_metrics.py) — Phase 14 exit gate.

The Phase 14 exit gate requires the "wrong-action rate within target", but until now nothing
measured it — a gate you cannot measure is a gate you cannot pass, only assert.

What counts as a **wrong action** (deliberately narrow and defensible):

* ``failed`` — GENIE attempted something and it errored out;
* ``unverified`` — GENIE reported success but verification disagreed (the dangerous one: it
  *looks* fine to the owner while being wrong).

What does **not** count:

* ``denied`` / ``confirmation required`` — the permission system correctly refused. That is the
  safety layer *working*, not a wrong action. Counting refusals as failures would discourage the
  agent from ever asking, which is the opposite of what we want.
* ``blocked`` (safe mode) — correctly refused by policy.

Those are tracked separately as ``refused`` so they stay visible without poisoning the rate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.action_metrics")

#: default target from the spec-facing gate: at most 2% of executed actions may be wrong
DEFAULT_TARGET = 0.02


@dataclass
class ActionOutcome:
    capability: str
    ok: bool
    verified: bool
    kind: str = "execution"      # execution | refused | blocked
    detail: str = ""
    ts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"capability": self.capability, "ok": self.ok, "verified": self.verified,
                "kind": self.kind, "detail": self.detail[:200], "ts": self.ts}


def classify(ok: bool, verified: bool, detail: str = "") -> str:
    """Map an execution result onto an outcome kind."""
    text = (detail or "").lower()
    if "safe mode" in text:
        return "blocked"
    if "denied" in text or "confirmation required" in text or "outside" in text:
        return "refused"
    if not ok:
        return "failed"
    if ok and not verified:
        return "unverified"
    return "success"


class ActionMeter:
    """Counts action outcomes and reports whether we are inside the target."""

    def __init__(self, db=None, target: float = DEFAULT_TARGET):
        self.db = db
        self.target = float(target)
        self._outcomes: List[ActionOutcome] = []

    # --------------------------------------------------------------- record
    def record(self, *, capability: str, ok: bool, verified: bool = False,
               detail: str = "") -> Dict[str, Any]:
        kind = classify(ok, verified, detail)
        outcome = ActionOutcome(capability=capability, ok=bool(ok), verified=bool(verified),
                                kind=kind, detail=detail, ts=int(time.time() * 1000))
        self._outcomes.append(outcome)
        self._persist(outcome)
        return outcome.to_dict()

    def _persist(self, outcome: ActionOutcome) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT INTO agent_action_outcomes(ts, capability, ok, verified, kind, detail)"
                " VALUES(?,?,?,?,?,?)",
                (outcome.ts, outcome.capability, int(outcome.ok), int(outcome.verified),
                 outcome.kind, outcome.detail[:200]))
        except Exception as exc:
            log.debug("action metric persist failed: %s", exc)

    # --------------------------------------------------------------- report
    def _counts(self, outcomes: List[ActionOutcome]) -> Dict[str, int]:
        counts = {"success": 0, "failed": 0, "unverified": 0, "refused": 0, "blocked": 0}
        for o in outcomes:
            counts[o.kind] = counts.get(o.kind, 0) + 1
        return counts

    def rate(self, *, window_s: Optional[float] = None) -> Dict[str, Any]:
        outcomes = self._outcomes
        if window_s is not None:
            cutoff = time.time() - window_s
            outcomes = [o for o in outcomes if o.ts / 1000.0 >= cutoff]
        counts = self._counts(outcomes)
        # only executed actions can be "wrong"; refusals are the safety net working
        executed = counts["success"] + counts["failed"] + counts["unverified"]
        wrong = counts["failed"] + counts["unverified"]
        rate = (wrong / executed) if executed else 0.0
        return {"executed": executed, "wrong": wrong, "rate": round(rate, 4),
                "target": self.target, "within_target": rate <= self.target,
                "counts": counts, "sample": executed}

    def status(self) -> Dict[str, Any]:
        current = self.rate()
        return {"wrong_action_rate": current["rate"], "target": self.target,
                "within_target": current["within_target"], "executed": current["executed"],
                "wrong": current["wrong"], "counts": current["counts"],
                "note": ("within target" if current["within_target"] else
                         f"ABOVE target ({current['rate']} > {self.target}) — "
                         f"{current['wrong']} wrong of {current['executed']} executed")}

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [o.to_dict() for o in self._outcomes[-limit:]]

    def reset(self) -> None:
        self._outcomes.clear()
