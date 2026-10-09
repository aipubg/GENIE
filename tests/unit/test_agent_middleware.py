"""Agent middleware chain (re-audit 14.5, donor: AgentScope).

Proves the chain actually gates work: a PTE denial must prevent the worker from ever running, and
a failing observer must never swallow the real result.
"""
from __future__ import annotations

from agents.middleware import (AuditMiddleware, BudgetMiddleware, CallContextLite,
                               Middleware, MiddlewareChain, PTEMiddleware, ReceiptMiddleware)
from agents.receipts import ReceiptLedger


def _ctx(action="files.read", **kw):
    return CallContextLite(agent_id="a1", mission_id="m1", action=action, **kw)


# ------------------------------------------------------------------- ordering
def test_middlewares_run_in_order_and_after_hooks_in_reverse():
    seen = []

    class Recorder(Middleware):
        def __init__(self, tag):
            self.tag = tag
            self.name = tag

        def before(self, ctx):
            seen.append(f"before:{self.tag}")

        def after(self, ctx, result):
            seen.append(f"after:{self.tag}")

    chain = MiddlewareChain([Recorder("a"), Recorder("b")])
    chain.invoke(_ctx(), lambda c: {"ok": True})
    assert seen == ["before:a", "before:b", "after:b", "after:a"]


# ------------------------------------------------------------- short-circuit
def test_a_pte_denial_prevents_the_worker_from_running():
    ran = {"called": False}

    def work(ctx):
        ran["called"] = True
        return {"ok": True}

    chain = MiddlewareChain([PTEMiddleware(allowed={"files.read"})])
    result = chain.invoke(_ctx(action="shell.run"), work)
    assert result["ok"] is False
    assert result["short_circuit"] == "pte"
    assert ran["called"] is False, "a denied action must never execute"


def test_an_allowed_action_reaches_the_worker():
    chain = MiddlewareChain([PTEMiddleware(allowed={"files.read"})])
    result = chain.invoke(_ctx(action="files.read"), lambda c: {"ok": True, "data": "x"})
    assert result["ok"] is True


def test_short_circuits_are_recorded_not_silent():
    chain = MiddlewareChain([PTEMiddleware(allowed=set())])
    chain.invoke(_ctx(action="anything"), lambda c: {"ok": True})
    assert len(chain.short_circuits) == 1
    assert chain.short_circuits[0]["short_circuit"] == "pte"


# -------------------------------------------------------------------- budget
def test_budget_middleware_accumulates_and_then_blocks():
    budget = BudgetMiddleware(limit=1.0)
    chain = MiddlewareChain([budget])
    chain.invoke(_ctx(), lambda c: {"ok": True, "cost": 0.6})
    assert budget.spent == 0.6
    chain.invoke(_ctx(), lambda c: {"ok": True, "cost": 0.5})
    assert budget.spent == 1.1
    result = chain.invoke(_ctx(), lambda c: {"ok": True})
    assert result["ok"] is False and result["short_circuit"] == "budget"


# ------------------------------------------------------------------ receipts
def test_receipt_middleware_records_evidence_for_each_action():
    ledger = ReceiptLedger()
    receipts = ReceiptMiddleware(ledger=ledger)
    chain = MiddlewareChain([receipts])
    chain.invoke(_ctx(action="shell.pytest"),
                 lambda c: {"ok": True, "verified": True, "verifier": "pytest"})
    assert len(ledger) == 1
    assert receipts.recorded
    assert ledger.get(receipts.recorded[0]).passed is True


def test_a_failed_action_produces_a_failed_receipt():
    ledger = ReceiptLedger()
    receipts = ReceiptMiddleware(ledger=ledger)
    MiddlewareChain([receipts]).invoke(_ctx(action="shell.run"),
                                       lambda c: {"ok": False, "detail": "boom"})
    assert ledger.all()[0].passed is False


# ---------------------------------------------------------------- resilience
def test_a_failing_observer_does_not_lose_the_result():
    class Boom(Middleware):
        name = "boom"

        def after(self, ctx, result):
            raise RuntimeError("observer exploded")

    chain = MiddlewareChain([Boom()])
    result = chain.invoke(_ctx(), lambda c: {"ok": True, "data": "important"})
    assert result["data"] == "important"


def test_a_failing_before_hook_does_not_block_the_work():
    class Boom(Middleware):
        name = "boom"

        def before(self, ctx):
            raise RuntimeError("gate exploded")

    chain = MiddlewareChain([Boom()])
    result = chain.invoke(_ctx(), lambda c: {"ok": True})
    assert result["ok"] is True, "a broken gate must not wedge the runtime"


def test_audit_middleware_logs_executions():
    audit = AuditMiddleware()
    MiddlewareChain([audit]).invoke(_ctx(action="files.read"), lambda c: {"ok": True})
    assert audit.entries == ["agent.files.read"]


def test_chain_reports_its_names():
    chain = MiddlewareChain([PTEMiddleware(), BudgetMiddleware()])
    assert chain.names() == ["pte", "budget"]
    assert chain.add(AuditMiddleware()).names() == ["pte", "budget", "audit"]
