"""Phase 12.3 — governed self-evolution.

The value of this module is what it **refuses**. These tests assert the gates hold:
conformance before approval, approval before applying, and no silent self-modification.
"""
from __future__ import annotations

import pytest

from agents.evolution import (APPLIED, APPROVED, CONFORMED, FAILED, PROPOSED, REJECTED,
                              EvolutionEngine)


@pytest.fixture()
def engine(app):
    return EvolutionEngine(audit=app.audit)


def _ok_check(payload):
    return True, "looks fine"


# ------------------------------------------------------------- the happy path
def test_propose_conform_approve_apply(engine, app):
    proposal = engine.propose(title="speed up retries", description="back off faster",
                              target="agents/team.py", payload={"backoff": 0.5})
    assert proposal.state == PROPOSED

    engine.add_conformance(proposal.proposal_id, "sane_value", _ok_check)
    assert engine.run_conformance(proposal.proposal_id)["ok"] is True
    assert engine.get(proposal.proposal_id).state == CONFORMED

    assert engine.approve(proposal.proposal_id, by="owner")["ok"] is True
    assert engine.get(proposal.proposal_id).state == APPROVED

    applied = {"done": False}

    def applier(payload):
        applied["done"] = True
        applied["payload"] = payload
        return {"ok": True}

    assert engine.apply(proposal.proposal_id, applier, by="owner")["ok"] is True
    assert engine.get(proposal.proposal_id).state == APPLIED
    assert applied["done"] is True and applied["payload"]["backoff"] == 0.5


# ---------------------------------------------------------------- the gates
def test_cannot_approve_before_conformance(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    # conformance was never run
    out = engine.approve(proposal.proposal_id, by="owner")
    assert out["ok"] is False
    assert "conformance" in out["error"]
    assert engine.get(proposal.proposal_id).state == PROPOSED


def test_no_checks_registered_is_a_refusal_not_a_pass(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    out = engine.run_conformance(proposal.proposal_id)
    assert out["ok"] is False
    assert "no conformance checks" in out["error"]
    assert engine.get(proposal.proposal_id).conformance_passed is False


def test_failing_conformance_blocks_approval(engine):
    proposal = engine.propose(title="risky", description="", target="core/db.py",
                              payload={"drop_table": True})
    engine.add_conformance(proposal.proposal_id, "no_destructive",
                           lambda p: (not p.get("drop_table"), "destructive payload"))
    out = engine.run_conformance(proposal.proposal_id)
    assert out["ok"] is False
    assert engine.approve(proposal.proposal_id, by="owner")["ok"] is False


def test_cannot_apply_before_approval(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    engine.run_conformance(proposal.proposal_id)
    # conformed but not approved
    out = engine.apply(proposal.proposal_id, lambda p: {"ok": True}, by="owner")
    assert out["ok"] is False
    assert "not approved" in out["error"] or "conformed" in out["error"]


def test_cannot_apply_after_rejection(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    engine.run_conformance(proposal.proposal_id)
    engine.approve(proposal.proposal_id, by="owner")
    engine.reject(proposal.proposal_id, by="owner", reason="changed my mind")
    assert engine.get(proposal.proposal_id).state == REJECTED
    assert engine.apply(proposal.proposal_id, lambda p: {"ok": True})["ok"] is False


# ------------------------------------------------------------------- failures
def test_applier_failure_marks_proposal_failed(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    engine.run_conformance(proposal.proposal_id)
    engine.approve(proposal.proposal_id, by="owner")

    out = engine.apply(proposal.proposal_id, lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert out["ok"] is False
    assert engine.get(proposal.proposal_id).state == FAILED


def test_applier_reporting_failure_marks_proposal_failed(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    engine.run_conformance(proposal.proposal_id)
    engine.approve(proposal.proposal_id, by="owner")
    out = engine.apply(proposal.proposal_id, lambda p: {"ok": False, "error": "nope"})
    assert out["ok"] is False
    assert engine.get(proposal.proposal_id).state == FAILED


def test_a_raising_check_counts_as_failed(engine):
    proposal = engine.propose(title="x", description="y", target="z", payload={})

    def boom(payload):
        raise ValueError("check exploded")

    engine.add_conformance(proposal.proposal_id, "exploding", boom)
    out = engine.run_conformance(proposal.proposal_id)
    assert out["ok"] is False
    assert "check raised" in out["results"][0]["detail"]


# ------------------------------------------------------------------- lineage
def test_lineage_traces_a_change_back_to_its_origin(engine):
    root = engine.propose(title="observation", description="tests were slow",
                          target="tests/", payload={})
    child = engine.propose(title="speed up tests", description="", target="tests/conftest.py",
                           payload={}, parent_id=root.proposal_id)
    grandchild = engine.propose(title="parallelise", description="", target="tests/",
                                payload={}, parent_id=child.proposal_id)

    chain = engine.lineage(grandchild.proposal_id)
    assert [c["proposal_id"] for c in chain] == [grandchild.proposal_id, child.proposal_id,
                                                 root.proposal_id]


def test_lineage_of_an_orphan_is_just_itself(engine):
    solo = engine.propose(title="solo", description="", target="x", payload={})
    assert len(engine.lineage(solo.proposal_id)) == 1


# --------------------------------------------------------------------- audit
def test_transitions_are_audited(engine, app):
    proposal = engine.propose(title="audited change", description="", target="x", payload={})
    engine.add_conformance(proposal.proposal_id, "check", _ok_check)
    engine.run_conformance(proposal.proposal_id)
    engine.approve(proposal.proposal_id, by="owner")

    actions = [a["action"] for a in app.audit.tail(limit=20)]
    assert "evolution.propose" in actions
    assert "evolution.conformance" in actions
    assert "evolution.approve" in actions


def test_pending_and_history(engine):
    p1 = engine.propose(title="a", description="", target="x", payload={})
    engine.add_conformance(p1.proposal_id, "check", _ok_check)
    engine.run_conformance(p1.proposal_id)
    p2 = engine.propose(title="b", description="", target="y", payload={})
    engine.reject(p2.proposal_id, by="owner", reason="no")

    pending_ids = {p.proposal_id for p in engine.pending()}
    assert p1.proposal_id in pending_ids
    assert p2.proposal_id not in pending_ids, "a rejected proposal is not pending"
    assert len(engine.history()) == 2
