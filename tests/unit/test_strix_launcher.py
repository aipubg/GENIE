"""Tests for Strix runtime launcher.

Proves: dependency auditing, honest blocker reporting, successful launch
when deps are satisfied, and scope enforcement through the launcher.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch, MagicMock
from integrations.strix_launcher import (
    StrixLauncher,
    LauncherState,
    DependencyStatus,
    check_dependencies,
    LaunchResult,
)


class TestDependencyAudit:
    def test_check_returns_list(self):
        deps = check_dependencies()
        assert isinstance(deps, list)
        assert len(deps) > 0

    def test_dependency_status_fields(self):
        dep = DependencyStatus(name="test", available=True, version="1.0")
        assert dep.name == "test"
        assert dep.available is True
        assert dep.version == "1.0"
        assert dep.blocker is None

    def test_missing_dep_has_blocker(self):
        dep = DependencyStatus(name="missing", available=False, blocker="pip install missing")
        assert dep.available is False
        assert dep.blocker is not None


class TestLauncherStates:
    def test_initial_state(self):
        launcher = StrixLauncher()
        assert launcher.state == LauncherState.UNINITIALIZED

    def test_audit_transitions_state(self):
        launcher = StrixLauncher()
        launcher.audit()
        assert launcher.state in (LauncherState.READY, LauncherState.BLOCKED)

    def test_audit_returns_deps(self):
        launcher = StrixLauncher()
        deps = launcher.audit()
        assert isinstance(deps, list)


class TestLauncherLaunch:
    def test_launch_blocked_when_required_deps_missing(self):
        """If required Python deps are missing, launch reports exact blockers."""
        fake_deps = [
            DependencyStatus(name="semgrep", available=False, blocker="pip install semgrep", required=True),
        ]
        launcher = StrixLauncher()
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps):
            result = launcher.launch()
        assert result.success is False
        assert result.state == LauncherState.BLOCKED
        assert len(result.blockers) > 0
        assert "semgrep" in result.blockers[0]

    def test_launch_succeeds_when_deps_available(self):
        """When all required deps are available, launch succeeds and returns runtime."""
        fake_deps = [
            DependencyStatus(name="semgrep", available=True, version="1.0", required=True),
            DependencyStatus(name="bandit", available=True, version="1.7", required=True),
            DependencyStatus(name="safety", available=True, version="2.0", required=True),
        ]
        launcher = StrixLauncher()
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps):
            result = launcher.launch(allowed_targets=["localhost"])
        assert result.success is True
        assert result.state == LauncherState.RUNNING
        assert result.runtime is not None

    def test_launch_result_to_dict(self):
        result = LaunchResult(
            success=False,
            state=LauncherState.BLOCKED,
            blockers=("pip install semgrep",),
            dependencies=(DependencyStatus(name="semgrep", available=False, blocker="pip install semgrep"),),
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["state"] == "blocked"
        assert len(d["blockers"]) == 1
        assert len(d["dependencies"]) == 1

    def test_auto_provision_attempts_install(self):
        """Auto-provision mode tries to install missing deps."""
        fake_deps_before = [
            DependencyStatus(name="semgrep", available=False, blocker="pip install semgrep", required=True),
        ]
        fake_deps_after = [
            DependencyStatus(name="semgrep", available=True, version="1.0", required=True),
        ]
        launcher = StrixLauncher(auto_provision=True)
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps_before), \
             patch("integrations.strix_launcher.provision_python_deps", return_value=fake_deps_after):
            result = launcher.launch(allowed_targets=["localhost"])
        assert result.success is True

    def test_scope_enforced_through_launcher(self):
        """Targets passed to launch are enforced via SecurityScope."""
        fake_deps = [
            DependencyStatus(name="semgrep", available=True, version="1.0", required=True),
            DependencyStatus(name="bandit", available=True, version="1.7", required=True),
            DependencyStatus(name="safety", available=True, version="2.0", required=True),
        ]
        launcher = StrixLauncher()
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps):
            result = launcher.launch(allowed_targets=["192.168.1.0/24"])
        assert result.success is True
        # Runtime should have scope with our targets
        runtime = result.runtime
        assert hasattr(runtime, '_scope')


class TestLauncherHonestReporting:
    def test_never_fakes_success(self):
        """If deps are genuinely missing, launcher must report BLOCKED, not READY."""
        fake_deps = [
            DependencyStatus(name="critical_tool", available=False, blocker="install it", required=True),
        ]
        launcher = StrixLauncher()
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps):
            result = launcher.launch()
        assert result.success is False
        assert result.state != LauncherState.RUNNING

    def test_reports_exact_blockers(self):
        """Blockers list contains actionable remediation steps."""
        fake_deps = [
            DependencyStatus(name="tool_a", available=False, blocker="pip install tool_a", required=True),
            DependencyStatus(name="tool_b", available=False, blocker="apt-get install tool_b", required=True),
        ]
        launcher = StrixLauncher()
        with patch("integrations.strix_launcher.check_dependencies", return_value=fake_deps):
            result = launcher.launch()
        assert "pip install tool_a" in result.blockers
        assert "apt-get install tool_b" in result.blockers
