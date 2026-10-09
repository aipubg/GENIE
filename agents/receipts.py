"""Tool receipts & deterministic acceptance (agents/receipts.py) — re-audit 14.5.

Capability donor: **DeerFlow** (verifiable subagent execution).

The problem this solves: an agent can say *"I wrote app.py and the tests passed"* while having done
neither. Raw agent claims are not evidence. This module makes every meaningful action produce a
**receipt** — a runtime-stamped record of what actually happened — and lets a parent/reviewer
**deterministically verify** a claim against those receipts.

Crucially, an undecidable result is **UNVERIFIED**, never silently treated as success:

    ACCEPTED   every cited receipt exists and passed
    REJECTED   a cited receipt exists and failed (a real counter-example)
    UNVERIFIED a receipt is missing / unknown — we cannot prove it, so we do not accept it

That third state is the whole point. Failing to distinguish "no evidence" from "evidence of
success" is how a self-improving system learns from fiction.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.receipts")

ACCEPTED = "accepted"
REJECTED = "rejected"
UNVERIFIED = "unverified"


def _digest(payload: Any) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        raw = repr(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Receipt:
    """Evidence that an action really happened."""

    receipt_id: str
    action: str
    status: str                    # ok | failed | unknown
    verifier: str = ""
    verified: bool = False
    inputs_digest: str = ""
    outputs_digest: str = ""
    artifact_ref: str = ""
    artifact_sha256: str = ""
    ts: int = 0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"receipt_id": self.receipt_id, "action": self.action, "status": self.status,
                "verifier": self.verifier, "verified": self.verified,
                "inputs_digest": self.inputs_digest, "outputs_digest": self.outputs_digest,
                "artifact_ref": self.artifact_ref, "artifact_sha256": self.artifact_sha256,
                "ts": self.ts, "detail": self.detail}

    @property
    def passed(self) -> bool:
        return self.status == "ok" and self.verified


class ReceiptLedger:
    """Append-only evidence for one run/mission."""

    def __init__(self, ledger_id: str = ""):
        self.ledger_id = ledger_id or f"led-{uuid.uuid4().hex[:12]}"
        self._receipts: Dict[str, Receipt] = {}
        self._order: List[str] = []

    # ---------------------------------------------------------------- record
    def record(self, *, action: str, status: str = "ok", verifier: str = "",
               verified: bool = False, inputs: Any = None, outputs: Any = None,
               artifact: Optional[Path | str] = None, detail: str = "") -> Receipt:
        artifact_ref = ""
        artifact_hash = ""
        if artifact is not None:
            path = Path(artifact)
            artifact_ref = str(path)
            try:
                artifact_hash = _sha256_file(path) if path.is_file() else ""
            except OSError as exc:
                log.debug("artifact hash failed for %s: %s", path, exc)
        receipt = Receipt(
            receipt_id=f"r{len(self._order) + 1}-{uuid.uuid4().hex[:8]}",
            action=action, status=status, verifier=verifier, verified=verified,
            inputs_digest=_digest(inputs), outputs_digest=_digest(outputs),
            artifact_ref=artifact_ref, artifact_sha256=artifact_hash,
            ts=int(time.time() * 1000), detail=detail)
        self._receipts[receipt.receipt_id] = receipt
        self._order.append(receipt.receipt_id)
        return receipt

    def get(self, receipt_id: str) -> Optional[Receipt]:
        return self._receipts.get(receipt_id)

    def all(self) -> List[Receipt]:
        return [self._receipts[rid] for rid in self._order]

    def __len__(self) -> int:
        return len(self._order)

    # ---------------------------------------------------------------- verify
    def verify_claim(self, *, statement: str, cited: List[str],
                     required_actions: Optional[List[str]] = None) -> Dict[str, Any]:
        """Deterministically check a claim against the receipts it cites."""
        if not cited:
            return {"verdict": UNVERIFIED, "statement": statement, "missing": [],
                    "failed": [], "checked": [],
                    "reason": "claim cites no receipts — nothing to verify against"}

        missing, failed, checked = [], [], []
        for rid in cited:
            receipt = self._receipts.get(rid)
            if receipt is None:
                missing.append(rid)
                continue
            checked.append(rid)
            if not receipt.passed:
                failed.append({"receipt_id": rid, "action": receipt.action,
                               "status": receipt.status, "verified": receipt.verified})

        if failed:
            verdict = REJECTED
            reason = "a cited receipt did not pass"
        elif missing:
            # we cannot prove it — NOT the same as proving it happened
            verdict = UNVERIFIED
            reason = "cited receipts are absent from the ledger"
        else:
            verdict = ACCEPTED
            reason = "all cited receipts exist and passed"

        # an action may be mandatory regardless of what was cited
        unmet = []
        for action in (required_actions or []):
            if not any(r.action == action and r.passed for r in self._receipts.values()):
                unmet.append(action)
        if unmet:
            verdict = UNVERIFIED if verdict == ACCEPTED else verdict
            reason = f"{reason}; required action(s) with no passing receipt: {unmet}"

        return {"verdict": verdict, "statement": statement, "missing": missing,
                "failed": failed, "checked": checked, "unmet_required": unmet,
                "reason": reason}

    def to_dict(self) -> Dict[str, Any]:
        return {"ledger_id": self.ledger_id,
                "receipts": [r.to_dict() for r in self.all()]}
