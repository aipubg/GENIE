"""Tool receipts & deterministic acceptance (re-audit 14.5, donor: DeerFlow).

The behaviour that matters most: **"no evidence" must never be treated as "evidence of success"**.
A claim citing a receipt that isn't in the ledger is UNVERIFIED, not accepted.
"""
from __future__ import annotations

from pathlib import Path

from agents.receipts import ACCEPTED, REJECTED, UNVERIFIED, ReceiptLedger


def _passing(ledger, action="shell.pytest", **kw):
    return ledger.record(action=action, status="ok", verified=True,
                         verifier="pytest-exit-code", **kw)


# ------------------------------------------------------------------ recording
def test_a_receipt_records_what_actually_happened():
    ledger = ReceiptLedger()
    receipt = _passing(ledger, inputs={"cmd": "pytest -q"}, outputs={"exit": 0})
    assert receipt.receipt_id
    assert receipt.action == "shell.pytest"
    assert receipt.passed is True
    assert receipt.inputs_digest and receipt.outputs_digest
    assert receipt.ts > 0
    assert len(ledger) == 1


def test_verified_is_required_for_a_receipt_to_pass():
    ledger = ReceiptLedger()
    unverified = ledger.record(action="files.write", status="ok", verified=False)
    assert unverified.passed is False, "an unverified success is not proof"


def test_artifact_hash_is_captured(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("print('hi')", encoding="utf-8")
    receipt = ReceiptLedger().record(action="files.write", status="ok", verified=True,
                                     artifact=target)
    assert receipt.artifact_ref == str(target)
    assert len(receipt.artifact_sha256) == 64, "artifact must be content-addressed"


def test_different_inputs_produce_different_digests():
    a = ReceiptLedger().record(action="x", inputs={"a": 1}).inputs_digest
    b = ReceiptLedger().record(action="x", inputs={"a": 2}).inputs_digest
    assert a != b


# ------------------------------------------------------------------- claims
def test_a_claim_with_no_receipts_is_unverified():
    result = ReceiptLedger().verify_claim(statement="tests passed", cited=[])
    assert result["verdict"] == UNVERIFIED
    assert "cites no receipts" in result["reason"]


def test_a_claim_citing_passing_receipts_is_accepted():
    ledger = ReceiptLedger()
    r1 = _passing(ledger, "files.write")
    r2 = _passing(ledger, "shell.pytest")
    result = ledger.verify_claim(statement="I wrote app.py and tests passed",
                                 cited=[r1.receipt_id, r2.receipt_id])
    assert result["verdict"] == ACCEPTED
    assert set(result["checked"]) == {r1.receipt_id, r2.receipt_id}


def test_a_claim_citing_a_failed_receipt_is_rejected():
    ledger = ReceiptLedger()
    good = _passing(ledger, "files.write")
    bad = ledger.record(action="shell.pytest", status="failed", verified=True,
                        verifier="pytest-exit-code", detail="2 failed")
    result = ledger.verify_claim(statement="tests passed",
                                 cited=[good.receipt_id, bad.receipt_id])
    assert result["verdict"] == REJECTED
    assert result["failed"][0]["receipt_id"] == bad.receipt_id


def test_a_claim_citing_an_unknown_receipt_is_unverified_not_accepted():
    """The critical case: a fabricated citation must not pass."""
    ledger = ReceiptLedger()
    result = ledger.verify_claim(statement="tests passed", cited=["r1-doesnotexist"])
    assert result["verdict"] == UNVERIFIED
    assert result["missing"] == ["r1-doesnotexist"]


def test_an_unverified_success_is_not_accepted_as_proof():
    ledger = ReceiptLedger()
    shaky = ledger.record(action="shell.pytest", status="ok", verified=False)
    result = ledger.verify_claim(statement="tests passed", cited=[shaky.receipt_id])
    assert result["verdict"] == REJECTED, "unverified success must not be accepted"


def test_required_action_without_a_passing_receipt_fails_the_claim():
    ledger = ReceiptLedger()
    wrote = _passing(ledger, "files.write")
    result = ledger.verify_claim(statement="built and tested", cited=[wrote.receipt_id],
                                 required_actions=["shell.pytest"])
    assert result["verdict"] == UNVERIFIED
    assert "shell.pytest" in result["unmet_required"]


def test_required_action_satisfied_passes():
    ledger = ReceiptLedger()
    r = _passing(ledger, "shell.pytest")
    result = ledger.verify_claim(statement="tested", cited=[r.receipt_id],
                                 required_actions=["shell.pytest"])
    assert result["verdict"] == ACCEPTED
    assert result["unmet_required"] == []


# ------------------------------------------------------------------- ledger
def test_ledger_is_append_only_and_exportable():
    ledger = ReceiptLedger()
    a = _passing(ledger, "a")
    b = _passing(ledger, "b")
    assert len(ledger) == 2
    assert ledger.get(a.receipt_id) is a
    assert ledger.get("nope") is None
    exported = ledger.to_dict()
    assert exported["ledger_id"]
    assert len(exported["receipts"]) == 2
