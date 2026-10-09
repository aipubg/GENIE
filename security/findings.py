"""Canonical security findings & deduplication (security/findings.py) — re-audit 14.4.

Capability donor: **Strix** (validated security findings, dedupe, CVSS/SARIF).

Without this, security output is just conversational text: three agents probing the same target
each report "SQL injection" separately and nothing is ever aggregated, tracked or re-tested.

This module gives GENIE a **canonical finding record** and **deduplication**, so:

    Agent A -> SQL injection
    Agent B -> same SQL injection
    Agent C -> same root cause
                    |
    SecurityFindingService
                    |
         ONE canonical finding (with all reporters recorded)

Authorisation is NOT decided here. GENIE's PTE decides target, scope, network range, allowed test
types, whether destructive actions are permitted, the expiry and the budget. This module only
stores and reconciles what was found inside an authorised scope.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("security.findings")

SEVERITIES = ("info", "low", "medium", "high", "critical")
OPEN_STATES = ("new", "triaged", "confirmed", "fix-proposed", "fixed", "retest-pending")
CLOSED_STATES = ("resolved", "false-positive", "accepted-risk", "wont-fix")


def _normalise(text: str) -> str:
    """Collapse whitespace/case/punctuation so the same finding words match."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Finding:
    """Canonical security finding."""

    target: str
    title: str
    finding_class: str = ""
    severity: str = "medium"
    cwe: str = ""
    cve: str = ""
    cvss: Optional[float] = None
    evidence: str = ""
    affected_artifacts: List[str] = field(default_factory=list)
    reproduction: str = ""
    remediation: str = ""
    retest_evidence: str = ""
    status: str = "new"
    mission_id: str = ""
    finding_id: str = field(default_factory=lambda: f"sec-{uuid.uuid4().hex[:12]}")
    fingerprint: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    #: which agents/engines reported this (dedupe keeps them all)
    reported_by: List[str] = field(default_factory=list)
    duplicate_count: int = 0

    def compute_fingerprint(self) -> str:
        """Identity for dedupe: same target + same class + same root cause = one finding.

        ``affected_artifacts``, ``cwe`` and ``cve`` are deliberately EXCLUDED. Two agents hitting
        the same root cause often name slightly different files, or only one of them knows the
        CWE/CVE. Including any of those would split ONE finding into two — the exact failure
        dedupe exists to prevent. They are treated as attributes to be unioned/filled in.
        """
        parts = [self.target, self.finding_class, _normalise(self.title)]
        return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {"finding_id": self.finding_id, "mission_id": self.mission_id,
                "target": self.target, "title": self.title,
                "finding_class": self.finding_class, "severity": self.severity,
                "cwe": self.cwe, "cve": self.cve, "cvss": self.cvss,
                "evidence": self.evidence,
                "affected_artifacts": list(self.affected_artifacts),
                "reproduction": self.reproduction, "remediation": self.remediation,
                "retest_evidence": self.retest_evidence, "status": self.status,
                "fingerprint": self.fingerprint, "created_ms": self.created_ms,
                "reported_by": list(self.reported_by),
                "duplicate_count": self.duplicate_count}


class SecurityFindingService:
    """Stores findings, deduplicates them, and tracks their lifecycle."""

    def __init__(self):
        self._findings: Dict[str, Finding] = {}
        self._by_fingerprint: Dict[str, str] = {}

    # ------------------------------------------------------------------- add
    def add(self, finding: Finding, *, reporter: str = "") -> Dict[str, Any]:
        """Add a finding, merging it into an existing one if it is a duplicate."""
        if finding.severity not in SEVERITIES:
            finding.severity = "medium"
        fingerprint = finding.compute_fingerprint()
        finding.fingerprint = fingerprint
        if reporter:
            finding.reported_by.append(reporter)

        existing_id = self._by_fingerprint.get(fingerprint)
        if existing_id:
            existing = self._findings[existing_id]
            existing.duplicate_count += 1
            for who in finding.reported_by:
                if who not in existing.reported_by:
                    existing.reported_by.append(who)
            # keep the richer record: fill any fields the original lacked
            for key in ("evidence", "reproduction", "remediation", "cvss", "cwe", "cve"):
                if not getattr(existing, key) and getattr(finding, key):
                    setattr(existing, key, getattr(finding, key))
            if SEVERITIES.index(finding.severity) > SEVERITIES.index(existing.severity):
                existing.severity = finding.severity
            for artifact in finding.affected_artifacts:
                if artifact not in existing.affected_artifacts:
                    existing.affected_artifacts.append(artifact)
            return {"finding": existing, "duplicate": True,
                    "merged_into": existing.finding_id}

        self._findings[finding.finding_id] = finding
        self._by_fingerprint[fingerprint] = finding.finding_id
        return {"finding": finding, "duplicate": False, "merged_into": finding.finding_id}

    # ----------------------------------------------------------------- query
    def get(self, finding_id: str) -> Optional[Finding]:
        return self._findings.get(finding_id)

    def all(self) -> List[Finding]:
        return sorted(self._findings.values(),
                      key=lambda f: (-SEVERITIES.index(f.severity), f.created_ms))

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.all() if f.severity == severity]

    def by_target(self, target: str) -> List[Finding]:
        return [f for f in self.all() if f.target == target]

    def unresolved(self) -> List[Finding]:
        return [f for f in self.all() if f.status not in CLOSED_STATES]

    def summary(self) -> Dict[str, Any]:
        by_sev = {s: 0 for s in SEVERITIES}
        for f in self._findings.values():
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        return {"total": len(self._findings), "by_severity": by_sev,
                "unresolved": len(self.unresolved()),
                "duplicates_merged": sum(f.duplicate_count for f in self._findings.values())}

    # -------------------------------------------------------------- lifecycle
    def update_status(self, finding_id: str, status: str, *,
                      evidence: str = "") -> Optional[Finding]:
        finding = self._findings.get(finding_id)
        if finding is None:
            return None
        finding.status = status
        if evidence:
            finding.retest_evidence = evidence
        return finding

    def retest(self, finding_id: str, *, passed: bool, evidence: str) -> Optional[Finding]:
        """Record the outcome of re-testing a fix."""
        return self.update_status(finding_id, "resolved" if passed else "retest-pending",
                                  evidence=evidence)

    # ------------------------------------------------------------------ export
    def to_sarif(self, *, tool_name: str = "GENIE",
                 version: str = "1.0.0") -> Dict[str, Any]:
        """Export findings as SARIF 2.1.0 so CI/security tooling can consume them.

        Capability donor: Strix (mature SARIF support). This is the minimal valid structure —
        rules, results, levels and locations — enough for GitHub/CI ingestion without
        pretending to be a full SARIF implementation.
        """
        level_for = {"info": "note", "low": "note", "medium": "warning",
                     "high": "error", "critical": "error"}
        results = []
        rules = {}
        for finding in self.all():
            rule_id = finding.finding_class or _slug(finding.title) or "finding"
            if rule_id not in rules:
                rules[rule_id] = {
                    "id": rule_id,
                    "name": _slug(finding.title) or rule_id,
                    "shortDescription": {"text": finding.title},
                    "helpUri": (f"https://cwe.mitre.org/data/definitions/"
                                f"{finding.cwe.replace('CWE-', '')}.html") if finding.cwe else "",
                }
            result: Dict[str, Any] = {
                "ruleId": rule_id,
                "level": level_for.get(finding.severity, "warning"),
                "message": {"text": finding.title},
            }
            locations = []
            for artifact in finding.affected_artifacts:
                locations.append({"physicalLocation": {
                    "artifactLocation": {"uri": artifact}}})
            if locations:
                result["locations"] = locations
            properties: Dict[str, Any] = {"severity": finding.severity,
                                          "status": finding.status,
                                          "fingerprint": finding.fingerprint}
            if finding.cwe:
                properties["cwe"] = finding.cwe
            if finding.cve:
                properties["cve"] = finding.cve
            if finding.cvss is not None:
                properties["cvss"] = finding.cvss
            if finding.evidence:
                properties["evidence"] = finding.evidence
            result["properties"] = properties
            results.append(result)

        return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json",
                "version": "2.1.0",
                "runs": [{"tool": {"driver": {"name": tool_name, "version": version,
                                              "rules": list(rules.values())}},
                          "results": results}]}


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip())[:60].strip("_")
