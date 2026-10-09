"""Strix Runtime Launcher — provisions and starts the actual security scanning runtime.

This module answers the audit question: "Can GENIE invoke the Strix security
runtime today?" by providing a concrete launcher that:

1. Checks for required dependencies (semgrep, nmap, etc.)
2. Provisions missing Python dependencies in the managed venv
3. Starts the StrixSpecialistRuntime with validated configuration
4. Reports exact blockers if startup fails

Architecture:
    GENIE Mission
      -> PTE / SecurityScope
        -> StrixLauncher.provision()
          -> StrixSpecialistRuntime.invoke()
            -> validated findings
              -> GENIE SecurityFindingService

GENIE remains authority. Strix may not widen scope.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LauncherState(Enum):
    UNINITIALIZED = "uninitialized"
    CHECKING_DEPS = "checking_deps"
    PROVISIONING = "provisioning"
    READY = "ready"
    BLOCKED = "blocked"
    RUNNING = "running"
    FAILED = "failed"


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    available: bool
    version: Optional[str] = None
    blocker: Optional[str] = None
    required: bool = True


@dataclass(frozen=True)
class LaunchResult:
    success: bool
    state: LauncherState
    runtime: Any = None
    blockers: tuple = ()
    dependencies: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state.value,
            "blockers": list(self.blockers),
            "dependencies": [
                {"name": d.name, "available": d.available, "version": d.version, "blocker": d.blocker}
                for d in self.dependencies
            ],
        }


# Python packages required for full Strix runtime capability
_PYTHON_DEPS = [
    ("semgrep", "semgrep"),
    ("bandit", "bandit"),
    ("safety", "safety"),
]

# External tools that enhance capability but don't block basic operation
_EXTERNAL_TOOLS = [
    ("semgrep", "semgrep --version"),
    ("nmap", "nmap --version"),
]


def _check_python_dep(module_name: str, pip_name: str) -> DependencyStatus:
    """Check if a Python package is importable."""
    try:
        mod = importlib.import_module(module_name)
        version = getattr(mod, "__version__", "unknown")
        return DependencyStatus(name=pip_name, available=True, version=version)
    except ImportError:
        return DependencyStatus(
            name=pip_name,
            available=False,
            blocker=f"pip install {pip_name}"
        )


def _check_external_tool(name: str, version_cmd: str) -> DependencyStatus:
    """Check if an external CLI tool is available."""
    try:
        result = subprocess.run(
            version_cmd.split(),
            capture_output=True,
            text=True,
            timeout=10
        )
        version_line = result.stdout.strip().split("\n")[0] if result.stdout else "unknown"
        return DependencyStatus(name=name, available=True, version=version_line)
    except FileNotFoundError:
        return DependencyStatus(
            name=name,
            available=False,
            blocker=f"{name} not found in PATH",
            required=False  # External tools are optional for basic runtime
        )
    except subprocess.TimeoutExpired:
        return DependencyStatus(
            name=name,
            available=False,
            blocker=f"{name} timed out",
            required=False
        )
    except Exception as e:
        return DependencyStatus(
            name=name,
            available=False,
            blocker=str(e),
            required=False
        )


def check_dependencies() -> List[DependencyStatus]:
    """Audit all dependencies for the Strix runtime."""
    deps = []
    for module_name, pip_name in _PYTHON_DEPS:
        deps.append(_check_python_dep(module_name, pip_name))
    for name, cmd in _EXTERNAL_TOOLS:
        deps.append(_check_external_tool(name, cmd))
    return deps


def provision_python_deps(
    python_exe: Optional[str] = None,
) -> List[DependencyStatus]:
    """Attempt to install missing Python dependencies in isolated environment.

    Args:
        python_exe: Path to Python interpreter. Defaults to managed venv.

    Returns:
        Updated dependency statuses after provisioning attempt.
    """
    if python_exe is None:
        python_exe = (
            "C:/Users/ghostt/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
        )

    results = []
    for module_name, pip_name in _PYTHON_DEPS:
        status = _check_python_dep(module_name, pip_name)
        if not status.available:
            try:
                subprocess.run(
                    [python_exe, "-m", "pip", "install", pip_name],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                # Re-check after install
                status = _check_python_dep(module_name, pip_name)
            except Exception:
                pass  # Keep original blocked status
        results.append(status)
    return results


class StrixLauncher:
    """Launches the actual Strix security runtime within GENIE authority.

    Usage:
        launcher = StrixLauncher()
        result = launcher.launch()
        if result.success:
            findings = result.runtime.invoke("scan", {"target": "..."})
    """

    def __init__(self, auto_provision: bool = False):
        self._state = LauncherState.UNINITIALIZED
        self._auto_provision = auto_provision
        self._deps: List[DependencyStatus] = []
        self._blockers: List[str] = []

    @property
    def state(self) -> LauncherState:
        return self._state

    def audit(self) -> List[DependencyStatus]:
        """Check all dependencies without launching."""
        self._state = LauncherState.CHECKING_DEPS
        self._deps = check_dependencies()
        self._blockers = [
            d.blocker for d in self._deps
            if d.required and not d.available and d.blocker
        ]
        if self._blockers:
            self._state = LauncherState.BLOCKED
        else:
            self._state = LauncherState.READY
        return self._deps

    def launch(
        self,
        allowed_targets: Optional[List[str]] = None,
        max_severity: str = "CRITICAL",
    ) -> LaunchResult:
        """Provision dependencies and start the Strix runtime.

        Args:
            allowed_targets: Scope-restricted target list (PTE enforced).
            max_severity: Maximum finding severity to report.

        Returns:
            LaunchResult with runtime instance or exact blockers.
        """
        self.audit()

        if self._auto_provision and self._state == LauncherState.BLOCKED:
            self._state = LauncherState.PROVISIONING
            self._deps = provision_python_deps()
            self._blockers = [
                d.blocker for d in self._deps
                if d.required and not d.available and d.blocker
            ]
            if not self._blockers:
                self._state = LauncherState.READY

        if self._state == LauncherState.BLOCKED:
            return LaunchResult(
                success=False,
                state=LauncherState.BLOCKED,
                blockers=tuple(self._blockers),
                dependencies=tuple(self._deps),
            )

        # Import and instantiate the actual runtime.
        # SecurityScope is GENIE-authoritative and lives in integrations.strix_runtime.
        # `max_severity` stays a launcher-level parameter: GENIE's SecurityScope has no
        # severity field, so severity is enforced at the findings-bridge layer instead.
        try:
            from integrations.strix_runtime import (
                SecurityScope,
                StrixSpecialistRuntime,
            )

            scope = SecurityScope(
                mission_id="strix-launch",
                target=allowed_targets[0] if allowed_targets else "",
                allowed_domains=list(allowed_targets or []),
            )
            runtime = StrixSpecialistRuntime(scope=scope)
            self._state = LauncherState.RUNNING

            return LaunchResult(
                success=True,
                state=LauncherState.RUNNING,
                runtime=runtime,
                dependencies=tuple(self._deps),
            )
        except ImportError as e:
            self._state = LauncherState.FAILED
            return LaunchResult(
                success=False,
                state=LauncherState.FAILED,
                blockers=(f"Import error: {e}",),
                dependencies=tuple(self._deps),
            )
        except Exception as e:
            self._state = LauncherState.FAILED
            return LaunchResult(
                success=False,
                state=LauncherState.FAILED,
                blockers=(f"Launch error: {e}",),
                dependencies=tuple(self._deps),
            )
