"""Budget controller (agents/budget.py) — master spec §10.10, §10.11, §10.28.

Budgets form a hierarchy and **a child can never exceed a parent's remaining budget**:

    global → mission → team → agent → task

Two things the spec insists on that shape this module:

* **Escalation must be measurable.** Choosing a stronger (more expensive) model is a decision with
  a recorded reason and a cost, not a silent retry. `request_escalation()` refuses to escalate
  without evidence.
* **Do not rely solely on provider self-reported usage.** GENIE records its own estimate as well,
  so a provider that under-reports cannot quietly exceed the ceiling.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from agents.contracts import Budget

log = get_logger("agents.budget")

#: Model tiers, cheapest first. Escalation moves up one tier and must justify itself.
MODEL_TIERS = ("deterministic", "cheap-fast", "standard", "strong", "specialist")

#: Rough cost per 1k tokens by tier, used for GENIE-side estimates when pricing is unknown.
ESTIMATED_COST_PER_1K = {
    "deterministic": 0.0,
    "cheap-fast": 0.0002,
    "standard": 0.0012,
    "strong": 0.006,
    "specialist": 0.02,
}


def tier_cost_estimate(tier: str, tokens: int) -> float:
    rate = ESTIMATED_COST_PER_1K.get(tier, ESTIMATED_COST_PER_1K["standard"])
    return round((max(0, int(tokens)) / 1000.0) * rate, 6)


def next_tier(tier: str) -> str:
    try:
        index = MODEL_TIERS.index(tier)
    except ValueError:
        return "standard"
    return MODEL_TIERS[min(index + 1, len(MODEL_TIERS) - 1)]


@dataclass
class EscalationRecord:
    from_tier: str
    to_tier: str
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"from_tier": self.from_tier, "to_tier": self.to_tier, "reason": self.reason,
                "evidence": self.evidence,
                "estimated_cost_usd": round(self.estimated_cost_usd, 6), "ts": self.ts}


@dataclass
class BudgetNode:
    """One level of the hierarchy."""

    name: str
    scope: str = "task"
    budget: Budget = field(default_factory=Budget)
    parent: Optional["BudgetNode"] = None
    children: List["BudgetNode"] = field(default_factory=list)

    def remaining(self) -> Dict[str, float]:
        """This node's remaining allowance, capped by every ancestor's remaining."""
        mine = self.budget.remaining()
        parent = self.parent
        while parent is not None:
            theirs = parent.budget.remaining()
            for key, value in theirs.items():
                if value is None:
                    continue
                mine[key] = min(mine[key], value) if mine.get(key) is not None else value
            parent = parent.parent
        return mine

    def can_afford(self, *, tokens: int = 0, cost_usd: float = 0.0, model_calls: int = 0,
                   tool_calls: int = 0) -> bool:
        left = self.remaining()
        return (left["tokens"] >= tokens and left["cost_usd"] >= cost_usd
                and left["model_calls"] >= model_calls
                and left["tool_calls"] >= tool_calls)

    def exhausted(self) -> Optional[str]:
        for name, left in self.remaining().items():
            if left is not None and left <= 0:
                return name
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "scope": self.scope, "budget": self.budget.to_dict(),
                "remaining": {k: (None if v == float("inf") else round(v, 6))
                              for k, v in self.remaining().items()},
                "exhausted": self.exhausted(),
                "children": [c.name for c in self.children]}


class BudgetController:
    """Owns the hierarchy and answers every affordability question."""

    def __init__(self, db=None, *, global_budget: Optional[Budget] = None):
        self.db = db
        self.global_node = BudgetNode(name="global", scope="global",
                                      budget=global_budget or Budget())
        self._nodes: Dict[str, BudgetNode] = {"global": self.global_node}
        self._escalations: List[EscalationRecord] = []

    # ------------------------------------------------------------------ hierarchy
    def child(self, name: str, scope: str, budget: Budget,
              parent: Optional[str] = None) -> BudgetNode:
        parent_node = self._nodes.get(parent or "global", self.global_node)
        node = BudgetNode(name=name, scope=scope, budget=budget, parent=parent_node)
        parent_node.children.append(node)
        self._nodes[name] = node
        return node

    def node(self, name: str) -> Optional[BudgetNode]:
        return self._nodes.get(name)

    def set_cap(self, name: str, budget: Budget) -> Dict[str, Any]:
        """Operator control surface (§11): re-cap an existing node, e.g. tighten a mission's
        spend limit mid-flight. Refuses unknown nodes and never touches the parent chain."""
        node = self._nodes.get(name)
        if node is None:
            return {"ok": False, "error": f"unknown budget node {name}"}
        node.budget = budget
        if self.db is not None:
            try:
                self.db.execute(
                    "UPDATE agent_budgets SET tokens=?, cost_usd=?, model_calls=?, "
                    "tool_calls=?, wall_ms=? WHERE name=?",
                    (budget.tokens, budget.cost_usd, budget.model_calls,
                     budget.tool_calls, budget.wall_ms, name))
            except Exception as exc:
                log.debug("budget cap persist failed: %s", exc)
        return {"ok": True, "name": name, "remaining": node.to_dict()["remaining"]}

    def remove(self, name: str) -> None:
        node = self._nodes.pop(name, None)
        if node is None:
            return
        if node.parent is not None:
            node.parent.children = [c for c in node.parent.children if c is not node]
        for child in list(node.children):
            self.remove(child.name)

    # ------------------------------------------------------------------- spending
    def spend(self, name: str, *, tokens: int = 0, cost_usd: float = 0.0,
              model_calls: int = 0, tool_calls: int = 0) -> Dict[str, Any]:
        """Spend against a node and every ancestor, so the whole chain stays accurate."""
        node = self._nodes.get(name)
        if node is None:
            return {"ok": False, "error": f"unknown budget node {name}"}
        if not node.can_afford(tokens=tokens, cost_usd=cost_usd, model_calls=model_calls,
                               tool_calls=tool_calls):
            exhausted = node.exhausted()
            log.warning("budget refused for %s: %s exhausted", name, exhausted)
            return {"ok": False, "error": f"budget exhausted: {exhausted}",
                    "exhausted": exhausted, "remaining": node.to_dict()["remaining"]}
        walker: Optional[BudgetNode] = node
        while walker is not None:
            walker.budget.spend(tokens=tokens, cost_usd=cost_usd, model_calls=model_calls,
                                tool_calls=tool_calls)
            walker = walker.parent
        return {"ok": True, "remaining": node.to_dict()["remaining"]}

    def record_model_call(self, name: str, *, tokens: int = 0, cost_usd: float = 0.0) -> Dict[str, Any]:
        return self.spend(name, tokens=tokens, cost_usd=cost_usd, model_calls=1)

    def record_tool_call(self, name: str) -> Dict[str, Any]:
        return self.spend(name, tool_calls=1)

    # ------------------------------------------------------------------ escalation
    def request_escalation(self, name: str, *, current_tier: str, reason: str,
                           evidence: Optional[Dict[str, Any]] = None,
                           expected_tokens: int = 2000) -> Dict[str, Any]:
        """Ask for a stronger model. Refused without evidence, and only one tier at a time."""
        evidence = evidence or {}
        if not evidence:
            return {"ok": False, "error": "escalation requires evidence (D-091)",
                    "detail": "record what failed before paying for a stronger model"}
        if not reason:
            return {"ok": False, "error": "escalation requires a reason"}
        target = next_tier(current_tier)
        if target == current_tier:
            return {"ok": False, "error": f"{current_tier} is already the strongest tier"}
        node = self._nodes.get(name)
        estimate = tier_cost_estimate(target, expected_tokens)
        if node is not None and not node.can_afford(cost_usd=estimate, tokens=expected_tokens):
            return {"ok": False, "error": "budget cannot afford the escalation",
                    "estimated_cost_usd": estimate, "exhausted": node.exhausted()}
        record = EscalationRecord(from_tier=current_tier, to_tier=target, reason=reason,
                                  evidence=evidence, estimated_cost_usd=estimate)
        self._escalations.append(record)
        if self.db is not None:
            import json as _json
            try:
                self.db.execute(
                    "INSERT INTO agent_escalations(from_tier, to_tier, reason, evidence,"
                    " estimated_cost_usd, ts) VALUES(?,?,?,?,?,?)",
                    (record.from_tier, record.to_tier, record.reason,
                     _json.dumps(record.evidence), record.estimated_cost_usd, record.ts))
            except Exception as exc:
                log.debug("escalation persist failed: %s", exc)
        log.info("escalating %s: %s -> %s (%s)", name, current_tier, target, reason)
        return {"ok": True, "from_tier": current_tier, "to_tier": target,
                "estimated_cost_usd": estimate, "record": record.to_dict()}

    def escalations(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._escalations]

    # ------------------------------------------------------------------- reporting
    def status(self) -> Dict[str, Any]:
        return {"nodes": {name: node.to_dict() for name, node in self._nodes.items()},
                "escalations": len(self._escalations),
                "global_remaining": self.global_node.to_dict()["remaining"]}
