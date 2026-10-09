"""Agent middleware chain (agents/middleware.py) — re-audit 14.5.

Capability donor: **AgentScope** (middleware hook system).

As the agent runtime grows, cross-cutting concerns get scattered through worker code:
permission checks here, budget there, receipts somewhere else, audit tacked on at the end. That
is how each new concern becomes a rewrite of every worker.

This module gives one ordered chain instead:

    GENIE Agent Runtime
        -> PTEMiddleware      (is this allowed at all?)
        -> BudgetMiddleware   (is there budget left?)
        -> ReceiptMiddleware  (record evidence of what happened)
        -> ExperienceMiddleware (feed outcomes back into learning)
        -> AuditMiddleware    (durable accountable record)

A middleware may **short-circuit** (return a result without calling the worker) — that is how PTE
denies an action. Short-circuits are recorded, never silent.

Ordering is explicit and reversible; ``after`` hooks run in reverse so the outermost middleware
still sees the final result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.middleware")


@dataclass
class CallContextLite:
    """Minimum a middleware needs. Keeps the chain independent of the full runtime."""

    agent_id: str = ""
    mission_id: str = ""
    action: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class Middleware:
    """Base hook. Subclass and override what you need."""

    name = "middleware"

    def before(self, context: CallContextLite) -> Optional[Dict[str, Any]]:
        """Return a dict to short-circuit the call (a denial/result), or None to continue."""
        return None

    def after(self, context: CallContextLite, result: Dict[str, Any]) -> None:
        return None


class MiddlewareChain:
    """Ordered chain around any unit of agent work."""

    def __init__(self, middlewares: Optional[List[Middleware]] = None):
        self.middlewares: List[Middleware] = list(middlewares or [])
        self.short_circuits: List[Dict[str, Any]] = []

    def add(self, middleware: Middleware) -> "MiddlewareChain":
        self.middlewares.append(middleware)
        return self

    def invoke(self, context: CallContextLite,
               work: Callable[[CallContextLite], Dict[str, Any]]) -> Dict[str, Any]:
        for middleware in self.middlewares:
            try:
                decision = middleware.before(context)
            except Exception as exc:
                log.warning("middleware %s.before failed: %s", middleware.name, exc)
                continue
            if decision is not None:
                # a middleware refused/answered — record it, never drop it silently
                record = {"ok": False, "short_circuit": middleware.name, **decision}
                self.short_circuits.append(record)
                log.info("middleware %s short-circuited %s", middleware.name, context.action)
                return record

        result = work(context)

        for middleware in reversed(self.middlewares):
            try:
                middleware.after(context, result)
            except Exception as exc:
                # a failing observer must never lose the actual result
                log.warning("middleware %s.after failed: %s", middleware.name, exc)
        return result

    def names(self) -> List[str]:
        return [m.name for m in self.middlewares]


# ------------------------------------------------------------ concrete hooks
class PTEMiddleware(Middleware):
    """Permission gate. Deny by default unless a policy allows the action."""

    name = "pte"

    def __init__(self, allowed: Optional[set] = None):
        self.allowed = allowed or set()

    def before(self, context: CallContextLite) -> Optional[Dict[str, Any]]:
        if context.action not in self.allowed:
            return {"denied": True, "reason": f"action not permitted: {context.action}"}
        return None


class BudgetMiddleware(Middleware):
    """Refuse work once the budget is exhausted."""

    name = "budget"

    def __init__(self, limit: float = 1.0):
        self.limit = float(limit)
        self.spent = 0.0

    def before(self, context: CallContextLite) -> Optional[Dict[str, Any]]:
        if self.spent >= self.limit:
            return {"denied": True, "reason": f"budget exhausted ({self.spent}/{self.limit})"}
        return None

    def after(self, context: CallContextLite, result: Dict[str, Any]) -> None:
        self.spent += float(result.get("cost", 0.0) or 0.0)


class ReceiptMiddleware(Middleware):
    """Record evidence for every executed action (see agents/receipts.py)."""

    name = "receipt"

    def __init__(self, ledger=None):
        self.ledger = ledger
        self.recorded: List[str] = []

    def after(self, context: CallContextLite, result: Dict[str, Any]) -> None:
        if self.ledger is None:
            return
        receipt = self.ledger.record(
            action=context.action,
            status="ok" if result.get("ok") else "failed",
            verified=bool(result.get("verified")), verifier=str(result.get("verifier", "")),
            inputs={"agent": context.agent_id, "mission": context.mission_id},
            outputs=result.get("data"), detail=str(result.get("detail", ""))[:200])
        self.recorded.append(receipt.receipt_id)


class AuditMiddleware(Middleware):
    """Durable record of both executions and refusals."""

    name = "audit"

    def __init__(self, audit=None):
        self.audit = audit
        self.entries: List[str] = []

    def _write(self, who: str, action: str, result: str) -> None:
        self.entries.append(action)
        if self.audit is not None:
            try:
                self.audit.record(who=who, action=action, why="agent middleware",
                                  result=result[:200])
            except Exception as exc:
                log.debug("audit middleware write failed: %s", exc)

    def after(self, context: CallContextLite, result: Dict[str, Any]) -> None:
        self._write(context.agent_id or "agent", f"agent.{context.action}",
                    "ok" if result.get("ok") else "failed")
