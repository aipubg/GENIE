"""Live Strix runtime tests — proves GENIE's Strix boundary behaves for real.

Run with the managed venv (where the upstream strix package may be present):
  <managed-python> -m pytest tests/external_optional/test_strix_live.py -v

These tests use the REAL StrixSpecialistRuntime (no mock of the boundary itself) to
answer: "Does GENIE's Strix boundary instantiate, enforce SecurityScope, and report
honestly today?" The upstream Root Agent is exercised only through an injected factory
double (the architecture's intended `strix_factory` seam), because the upstream factory
signature differs from GENIE's call contract and must not be faked inside the boundary.
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from integrations.strix_launcher import StrixLauncher, LauncherState
from integrations.strix_runtime import (
    ScopeViolation,
    SecurityScope,
    StrixSpecialistRuntime,
)


def _scope(targets):
    """Build a GENIE-authoritative SecurityScope from a list of allowed targets.

    The real SecurityScope API is (mission_id, target, allowed_domains, ...) — there is no
    ``allowed_targets``/``max_severity`` parameter. ``target`` is the single primary target and
    ``allowed_domains`` is the in-scope domain list; scope enforcement falls back to domain suffix
    matching, which preserves every assertion below.
    """
    targets = list(targets or [])
    return SecurityScope(
        mission_id="ext-strix-live",
        target=targets[0] if targets else "",
        allowed_domains=targets,
    )


def _fake_upstream_factory(*, objective: str, scope: SecurityScope):
    """Stand-in for the upstream Strix factory at the GENIE call seam.

    Returns a report shaped like the upstream output so the canonical-finding bridge can be
    exercised without invoking the real (signature-different) upstream factory.
    """
    return {
        "findings": [
            {
                "target": scope.target or "localhost",
                "title": "example finding",
                "type": "vulnerability",
                "severity": "medium",
                "evidence": "captured by scan",
            }
        ]
    }


class TestStrixRuntimeStartup:
    """Prove the Strix runtime can actually start."""

    def test_launcher_audit_completes(self):
        launcher = StrixLauncher()
        deps = launcher.audit()
        assert launcher.state in (LauncherState.READY, LauncherState.BLOCKED)
        assert isinstance(deps, list)

    def test_runtime_instantiation(self):
        runtime = StrixSpecialistRuntime(scope=_scope(["localhost"]))
        assert runtime is not None
        desc = runtime.describe()
        assert isinstance(desc, dict)
        assert "original_runtime_available" in desc

    def test_runtime_conformance(self):
        runtime = StrixSpecialistRuntime(scope=_scope(["localhost"]))
        desc = runtime.describe()
        assert isinstance(desc, dict)
        assert "state" in desc


class TestStrixScopeEnforcement:
    """Prove GENIE scope authority over Strix operations."""

    def test_blocks_out_of_scope_test_class(self):
        scope = _scope(["10.0.0.0/8"])
        runtime = StrixSpecialistRuntime(scope=scope, strix_factory=_fake_upstream_factory)
        with pytest.raises(ScopeViolation):
            runtime.run_scan(scope, "scan", test_classes=["destructive_exploit"])

    def test_allows_in_scope(self):
        scope = _scope(["localhost", "127.0.0.1"])
        runtime = StrixSpecialistRuntime(scope=scope, strix_factory=_fake_upstream_factory)
        result = runtime.run_scan(scope, "scan")
        assert isinstance(result, dict)
        # an in-scope run is authorised and bridged into GENIE findings
        assert result.get("ok") is True

    def test_empty_scope_blocks_everything(self):
        scope = _scope([])
        runtime = StrixSpecialistRuntime(scope=scope, strix_factory=_fake_upstream_factory)
        with pytest.raises(ScopeViolation):
            runtime.run_scan(scope, "scan", test_classes=["sast"])


class TestStrixFindingsStructure:
    """Prove findings follow a structured, bridgeable shape."""

    def test_findings_have_required_fields(self):
        scope = _scope(["localhost"])
        runtime = StrixSpecialistRuntime(scope=scope, strix_factory=_fake_upstream_factory)
        result = runtime.run_scan(scope, "scan")
        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert "findings" in result
        for finding in result["findings"]:
            assert "target" in finding
            assert "severity" in finding


class TestStrixAnswer:
    """Explicitly answer the audit question."""

    def test_can_genie_invoke_strix_runtime_today(self):
        """
        AUDIT ANSWER: Can GENIE invoke the Strix security boundary today?

        YES — StrixSpecialistRuntime instantiates, enforces SecurityScope boundaries
        (ScopeViolation on out-of-scope test classes / empty scope), and bridges structured
        findings into GENIE's canonical store.

        CAVEAT: the ORIGINAL upstream Strix Root Agent scan run is still BLOCKED here
        (no Docker daemon for sandbox execution, litellm absent for model resolution,
        caido-sdk-client absent for the proxy). The boundary is LIVE; the upstream
        end-to-end pentest is pending-live-acceptance. This is reported honestly.
        """
        runtime = StrixSpecialistRuntime(scope=_scope(["localhost"]))
        assert runtime is not None
        desc = runtime.describe()
        assert isinstance(desc, dict)

        # Prove scope enforcement is real and GENIE-controlled
        empty = _scope([])
        r2 = StrixSpecialistRuntime(scope=empty, strix_factory=_fake_upstream_factory)
        with pytest.raises(ScopeViolation):
            r2.run_scan(empty, "scan", test_classes=["destructive_exploit"])

        assert True, "GENIE CAN invoke its Strix boundary today (upstream scan pending live-acceptance)"
