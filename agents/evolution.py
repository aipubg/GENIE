"""Governed self-evolution (agents/evolution.py) — Phase 12.3.

Capability adapted from **enoch** (lineage + conformance + owner approval). GENIE may propose
improvements to itself, but it may never adopt one silently.

The governance rule is deliberately strict, and it is the whole point of this module:

    propose  ->  conformance MUST pass  ->  owner MUST approve  ->  apply

* a proposal that has not passed its conformance checks **cannot be approved**;
* a proposal that is not approved **cannot be applied**;
* every transition is written to the tamper-evident audit log.

Nothing here auto-applies. An unattended GENIE cannot rewrite itself.

Lineage: each proposal may name a parent, so a change can be traced back to the observation or
proposal that produced it — "why does the system behave this way?" stays answerable.

Real isolation (agents/evolution_vcs)
-------------------------------------
When an `EvolutionVcsBackend` is supplied, isolation becomes PHYSICAL rather
than conceptual:

    propose  ->  real branch  ->  real worktree (a separate directory)
    candidate edits land ONLY in that worktree; the live tree is never touched
    adopt    ->  real merge into the base branch
    reject   ->  worktree + branch removed, lineage preserved in audit
    rollback ->  real reset to a known-good commit

Without a backend the engine behaves exactly as before (caller-supplied
`applier`), so existing behaviour and tests are unaffected.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging_setup import get_logger

log = get_logger("agents.evolution")

PROPOSED = "proposed"
CONFORMED = "conformed"
APPROVED = "approved"
REJECTED = "rejected"
APPLIED = "applied"
FAILED = "failed"

# Gate kinds beyond conformance (item 37). All are blocking.
GATE_TEST = "test"
GATE_SECURITY = "security"
GATE_BENCHMARK = "benchmark"
GATE_KINDS = (GATE_TEST, GATE_SECURITY, GATE_BENCHMARK)


@dataclass
class ChangeProposal:
    """A proposed change to GENIE, with its evidence and its ancestry."""

    proposal_id: str
    title: str
    description: str
    target: str
    payload: Dict[str, Any] = field(default_factory=dict)
    parent_id: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    state: str = PROPOSED
    conformance_passed: bool = False
    conformance_results: List[Dict[str, Any]] = field(default_factory=list)
    decided_by: str = ""
    decided_ms: int = 0
    note: str = ""
    # real VCS isolation (None when the engine runs without a backend)
    candidate: Optional[Dict[str, Any]] = None
    gates_passed: bool = False
    gate_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"proposal_id": self.proposal_id, "title": self.title,
                "description": self.description, "target": self.target,
                "parent_id": self.parent_id, "created_ms": self.created_ms,
                "state": self.state, "conformance_passed": self.conformance_passed,
                "conformance_results": list(self.conformance_results),
                "decided_by": self.decided_by, "decided_ms": self.decided_ms,
                "note": self.note,
                "candidate": self.candidate,
                "gates_passed": self.gates_passed,
                "gate_results": list(self.gate_results)}

    @property
    def worktree(self) -> Optional[str]:
        """Path candidates must edit — never the live tree."""
        return (self.candidate or {}).get("worktree") if self.candidate else None


class EvolutionEngine:
    """Propose, verify, approve — in that order, always."""

    def __init__(self, audit=None, experience=None, vcs=None):
        self.audit = audit
        self.experience = experience
        # Optional EvolutionVcsBackend. When present, candidates get a real
        # branch + worktree, and adopt/reject/rollback are real git operations.
        self.vcs = vcs
        self._proposals: Dict[str, ChangeProposal] = {}
        self._checks: Dict[str, List[Tuple[str, Callable[[Dict[str, Any]], Tuple[bool, str]]]]] = {}
        self._gates: Dict[str, List[Tuple[str, str, Callable[[Dict[str, Any]], Tuple[bool, str]]]]] = {}

    # ------------------------------------------------------------- proposing
    def propose(self, *, title: str, description: str, target: str,
                payload: Optional[Dict[str, Any]] = None, parent_id: str = "",
                isolate: bool = True) -> ChangeProposal:
        proposal = ChangeProposal(
            proposal_id=f"chg-{uuid.uuid4().hex[:12]}", title=title, description=description,
            target=target, payload=dict(payload or {}), parent_id=parent_id)

        # Real isolation: create a branch + worktree BEFORE any editing happens.
        if self.vcs is not None and isolate:
            try:
                cand = self.vcs.create_candidate(proposal.proposal_id, title=title)
                proposal.candidate = cand.to_dict()
            except Exception as exc:  # noqa: BLE001
                # A proposal without isolation must not be silently governable.
                proposal.state = FAILED
                proposal.note = f"isolation failed: {exc}"
                self._audit("evolution.isolate", proposal, f"failed: {exc}")
                self._proposals[proposal.proposal_id] = proposal
                return proposal

        self._proposals[proposal.proposal_id] = proposal
        self._audit("evolution.propose", proposal, f"{target}: {title}")
        return proposal

    def add_conformance(self, proposal_id: str, name: str,
                        check: Callable[[Dict[str, Any]], Tuple[bool, str]]) -> None:
        self._checks.setdefault(proposal_id, []).append((name, check))

    # ----------------------------------------------------------- conformance
    def run_conformance(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}
        checks = self._checks.get(proposal_id, [])
        if not checks:
            # no checks registered is NOT a pass — otherwise governance is decorative
            proposal.conformance_passed = False
            proposal.conformance_results = [{"name": "has_checks", "passed": False,
                                             "detail": "no conformance checks registered"}]
            self._audit("evolution.conformance", proposal, "no checks — refused")
            return {"ok": False, "error": "no conformance checks registered",
                    "results": proposal.conformance_results}

        results: List[Dict[str, Any]] = []
        for name, check in checks:
            try:
                passed, detail = check(proposal.payload)
            except Exception as exc:
                passed, detail = False, f"check raised: {exc}"
            results.append({"name": name, "passed": bool(passed), "detail": str(detail)})

        proposal.conformance_results = results
        proposal.conformance_passed = all(r["passed"] for r in results)
        proposal.state = CONFORMED if proposal.conformance_passed else PROPOSED
        self._audit("evolution.conformance", proposal,
                    "passed" if proposal.conformance_passed else "failed")
        return {"ok": proposal.conformance_passed, "results": results}

    # ----------------------------------------------------------------- gates
    def add_gate(self, proposal_id: str, name: str, kind: str,
                 check: Callable[[Dict[str, Any]], Tuple[bool, str]]) -> None:
        """Register a blocking gate: test / security / benchmark.

        Gates are separate from conformance so that "the tests failed" and
        "the security scan failed" are distinguishable in the audit trail.
        """
        if kind not in GATE_KINDS:
            raise ValueError(f"unknown gate kind {kind!r}; expected one of {GATE_KINDS}")
        self._gates.setdefault(proposal_id, []).append((name, kind, check))

    def run_gates(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}

        gates = self._gates.get(proposal_id, [])
        results: List[Dict[str, Any]] = []
        for name, kind, check in gates:
            try:
                passed, detail = check(proposal.payload)
            except Exception as exc:  # noqa: BLE001
                passed, detail = False, f"gate raised: {exc}"
            results.append({"name": name, "kind": kind,
                            "passed": bool(passed), "detail": str(detail)})

        proposal.gate_results = results
        proposal.gates_passed = all(r["passed"] for r in results) if results else True
        self._audit("evolution.gates", proposal,
                    "passed" if proposal.gates_passed else "failed")
        return {"ok": proposal.gates_passed, "results": results}

    # -------------------------------------------------------------- approval
    def approve(self, proposal_id: str, *, by: str, note: str = "") -> Dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}
        if proposal.state in (REJECTED, APPLIED):
            return {"ok": False, "error": f"proposal is already {proposal.state}"}
        if not proposal.conformance_passed:
            return {"ok": False,
                    "error": "conformance must pass before approval — run run_conformance() first",
                    "state": proposal.state}
        # Registered gates must have been run AND passed. Unrun gates are not a pass.
        if self._gates.get(proposal_id) and not proposal.gates_passed:
            return {"ok": False,
                    "error": "gates must pass before approval — run run_gates() first",
                    "state": proposal.state,
                    "gate_results": proposal.gate_results}
        proposal.state = APPROVED
        proposal.decided_by = by
        proposal.decided_ms = int(time.time() * 1000)
        proposal.note = note
        self._audit("evolution.approve", proposal, f"by {by}")
        return {"ok": True, "proposal": proposal.to_dict()}

    def reject(self, proposal_id: str, *, by: str, reason: str = "") -> Dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}
        proposal.state = REJECTED
        proposal.decided_by = by
        proposal.decided_ms = int(time.time() * 1000)
        proposal.note = reason

        # Real cleanup: drop the worktree and the branch. Lineage stays audited.
        rejection = None
        if self.vcs is not None and proposal.candidate:
            try:
                cand = self._candidate_obj(proposal)
                if cand is not None:
                    rejection = self.vcs.reject(cand)
            except Exception as exc:  # noqa: BLE001
                rejection = {"ok": False, "error": str(exc)}
            proposal.candidate = None
        self._audit("evolution.reject", proposal, f"by {by}: {reason[:120]}")
        return {"ok": True, "proposal": proposal.to_dict(), "cleanup": rejection}

    def _candidate_obj(self, proposal: ChangeProposal):
        """Rebuild the backend Candidate object from the stored dict."""
        from agents.evolution_vcs import Candidate  # local import: optional dep
        d = proposal.candidate
        if not d:
            return None
        return Candidate(proposal_id=d.get("proposal_id", proposal.proposal_id),
                         branch=d.get("branch", ""), worktree=d.get("worktree", ""),
                         base_branch=d.get("base_branch", "main"),
                         base_commit=d.get("base_commit", ""),
                         commits=list(d.get("commits", [])))

    def diff_candidate(self, proposal_id: str) -> Dict[str, Any]:
        """The candidate's isolated diff (never the live tree)."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}
        if self.vcs is None or not proposal.candidate:
            return {"ok": False, "error": "no isolated candidate for this proposal"}
        cand = self._candidate_obj(proposal)
        return self.vcs.diff(cand)

    def rollback(self, commit: str) -> Dict[str, Any]:
        """Restore the base branch to a known-good commit (real reset)."""
        if self.vcs is None:
            return {"ok": False, "error": "no VCS backend configured"}
        out = self.vcs.rollback(commit)
        self._audit_action("evolution.rollback", commit,
                           "restored" if out.get("ok") else str(out.get("error")))
        return out

    # ------------------------------------------------------------ applying
    def apply(self, proposal_id: str,
              applier: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
              *, by: str = "") -> Dict[str, Any]:
        """Apply an approved change. Refuses unless approved AND conformant.

        With a VCS backend this performs a REAL merge of the isolated branch and
        the applier is not used. Without one, the caller-supplied applier runs
        (legacy behaviour, unchanged).
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"unknown proposal {proposal_id}"}
        if proposal.state != APPROVED:
            return {"ok": False, "error": f"proposal is {proposal.state}, not approved",
                    "hint": "approve() it first"}
        if not proposal.conformance_passed:
            return {"ok": False, "error": "conformance no longer passes — refusing to apply"}

        merged = None
        try:
            # REAL adoption: merge the isolated branch into the base branch.
            # This only happens when the caller handed us a VCS backend; when it
            # does, the live tree is only ever touched by this merge.
            if self.vcs is not None and proposal.candidate:
                cand = self._candidate_obj(proposal)
                merged = self.vcs.adopt(cand)
                if not merged.get("ok"):
                    proposal.state = FAILED
                    proposal.note = f"adopt failed: {merged.get('error')}"
                    self._audit("evolution.apply", proposal, "adopt failed")
                    return {"ok": False, "error": merged.get("error"), "merge": merged}
                outcome = {"ok": True, "merged": merged}
            elif applier is not None:
                outcome = applier(proposal.payload) or {}
            else:
                proposal.state = FAILED
                proposal.note = "no applier and no VCS backend — nothing to apply"
                self._audit("evolution.apply", proposal, "no applier")
                return {"ok": False, "error": proposal.note}
        except Exception as exc:
            proposal.state = FAILED
            proposal.note = f"apply raised: {exc}"
            self._audit("evolution.apply", proposal, f"failed: {exc}")
            return {"ok": False, "error": f"apply raised: {exc}"}

        if outcome.get("ok") is False:
            proposal.state = FAILED
            proposal.note = str(outcome.get("error", "applier reported failure"))
            self._audit("evolution.apply", proposal, "applier reported failure")
            return {"ok": False, **outcome}

        proposal.state = APPLIED
        proposal.decided_by = proposal.decided_by or by
        self._audit("evolution.apply", proposal, f"applied by {by or proposal.decided_by}")
        return {"ok": True, "proposal": proposal.to_dict(), "result": outcome,
                "merge": merged}

    # -------------------------------------------------------------- querying
    def get(self, proposal_id: str) -> Optional[ChangeProposal]:
        return self._proposals.get(proposal_id)

    def pending(self) -> List[ChangeProposal]:
        return [p for p in self._proposals.values() if p.state in (PROPOSED, CONFORMED, APPROVED)]

    def history(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in
                sorted(self._proposals.values(), key=lambda p: p.created_ms)]

    def lineage(self, proposal_id: str) -> List[Dict[str, Any]]:
        """Ancestors of a proposal, nearest first — so a change can be traced to its origin."""
        chain: List[Dict[str, Any]] = []
        seen = set()
        current = self._proposals.get(proposal_id)
        while current is not None and current.proposal_id not in seen:
            seen.add(current.proposal_id)
            chain.append(current.to_dict())
            current = self._proposals.get(current.parent_id) if current.parent_id else None
        return chain

    # ---------------------------------------------------------------- audit
    def _audit(self, action: str, proposal: ChangeProposal, detail: str) -> None:
        if self.audit is None:
            return
        try:
            self.audit.record(who="system", action=action, why=f"{proposal.proposal_id}: "
                              f"{proposal.title[:100]}", result=detail[:120])
        except Exception as exc:
            log.debug("evolution audit failed: %s", exc)

    def _audit_action(self, action: str, subject: str, detail: str) -> None:
        """Audit an action that is not tied to a proposal (e.g. rollback)."""
        if self.audit is None:
            return
        try:
            self.audit.record(who="system", action=action, why=subject[:120],
                              result=detail[:120])
        except Exception as exc:
            log.debug("evolution audit failed: %s", exc)
