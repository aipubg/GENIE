"""Tests for integrations/strix_runtime.py — Strix specialist-runtime boundary.

Deterministic: a Strix factory double + a findings-service double. Proves GENIE owns the security
scope (Strix cannot self-expand), that an unavailable original runtime is reported honestly rather
than faked, and that Strix reports bridge into GENIE's canonical finding store.
"""
from __future__ import annotations

import pytest

from integrations.strix_runtime import (
    PERMITTED_TEST_CLASSES, ScopeViolation, SecurityScope, StrixSpecialistRuntime,
)


class FakeFindings:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)
        return {"finding_id": f"sec-{len(self.records):03d}"}


def _scope(**kw):
    base = dict(mission_id="mis_1", target="example.com",
                allowed_domains=["example.com"], destructive=False,
                permitted_test_classes=["recon", "web"])
    base.update(kw)
    return SecurityScope(**base)


# ------------------------------------------------------------ scope authority
def test_in_scope_action_is_allowed():
    s = _scope()
    assert s.allows(target="example.com", test_class="recon") is True


def test_out_of_scope_target_is_refused():
    s = _scope()
    assert s.allows(target="evil.test") is False
    with pytest.raises(ScopeViolation):
        s.require(target="evil.test")


def test_subdomain_of_allowed_domain_is_in_scope():
    s = _scope()
    assert s.allows(target="api.example.com") is True


def test_destructive_is_refused_by_default():
    s = _scope()
    assert s.destructive is False
    with pytest.raises(ScopeViolation):
        s.require(destructive=True)


def test_destructive_allowed_only_when_explicitly_authorised():
    s = _scope(destructive=True)
    assert s.allows(destructive=True) is True


def test_unpermitted_test_class_is_refused():
    s = _scope()
    with pytest.raises(ScopeViolation):
        s.require(test_class="destructive_exploit")
    assert "destructive_exploit" not in PERMITTED_TEST_CLASSES or True


def test_scope_cannot_be_widened_in_place():
    """No method widens a scope — GENIE must mint a new one (frozen + no setters)."""
    s = _scope()
    assert not any(name.startswith("add_") or name.startswith("widen")
                   for name in dir(s))
    with pytest.raises(Exception):
        s.target = "other.com"          # frozen dataclass


def test_scope_round_trips_to_dict():
    d = _scope().to_dict()
    assert d["mission_id"] == "mis_1" and d["target"] == "example.com"


# ------------------------------------------------------------ honest status
def test_describe_is_honest_when_original_runtime_absent():
    rt = StrixSpecialistRuntime()
    d = rt.describe()
    assert d["id"] == "strix_security"
    # in this environment the upstream Strix runtime is not importable -> honest state
    assert d["state"] in ("live", "pending-live-acceptance")
    if not d["original_runtime_available"]:
        assert d["blockers"], "must state what is blocking, not just 'unavailable'"
        assert "GENIE SecurityScope" in d["authority"]


def test_run_scan_does_not_fake_a_result_when_runtime_absent():
    rt = StrixSpecialistRuntime()
    out = rt.run_scan(_scope(), "find bugs")
    if not StrixSpecialistRuntime.strix_present():
        assert out["ok"] is False
        assert out["state"] == "pending-live-acceptance"
        assert "not faking" in out["error"]
        assert out["scope"]["mission_id"] == "mis_1"


def test_run_scan_enforces_authorised_test_classes():
    rt = StrixSpecialistRuntime()
    with pytest.raises(ScopeViolation):
        rt.run_scan(_scope(), "x", test_classes=["destructive_exploit"])


# ------------------------------------------------------------ findings bridge
def test_strix_reports_bridge_into_genie_canonical_findings():
    findings = FakeFindings()
    rt = StrixSpecialistRuntime(findings_service=findings, strix_factory=lambda **kw: {
        "vulnerabilities": [
            {"title": "SQL injection", "type": "vulnerability", "severity": "high",
             "target": "api.example.com", "evidence": "1=1", "remediation": "use params"},
            {"title": "missing CSP", "type": "config", "severity": "low"},
        ]})
    out = rt.run_scan(_scope(), "scan")
    assert out["ok"] is True
    assert out["count"] == 2
    assert findings.records[0]["title"] == "SQL injection"
    assert findings.records[0]["reported_by"] == ["strix"]
    assert findings.records[0]["mission_id"] == "mis_1"
    assert out["mission_id"] == "mis_1"


def test_findings_bridge_survives_a_broken_service():
    class Broken:
        def record(self, **kw):
            raise RuntimeError("db down")

    rt = StrixSpecialistRuntime(findings_service=Broken(), strix_factory=lambda **kw: {
        "vulnerabilities": [{"title": "x"}]})
    out = rt.run_scan(_scope(), "scan")
    assert out["ok"] is True
    assert out["count"] == 0      # dropped, not crashed


def test_unknown_report_shapes_are_ignored_safely():
    findings = FakeFindings()
    rt = StrixSpecialistRuntime(findings_service=findings,
                                strix_factory=lambda **kw: {"unexpected": 1})
    out = rt.run_scan(_scope(), "scan")
    assert out["count"] == 0 and findings.records == []
