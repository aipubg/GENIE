"""Strix specialist-runtime boundary (`integrations/strix_runtime.py`).

ONE GENIE AUTHORITY, MULTIPLE SPECIALIST RUNTIMES.

Strix is a mature *security* agent runtime: `strix/agents/factory.py` builds a Root Agent plus
child specialist agents (`agents_graph`: create_agent / send_message_to_agent / stop_agent), driving
proxy + browser + shell tools and emitting vulnerability reports.

Why this module exists — the honest answer to "is the original Strix runtime callable from GENIE?":

* GENIE previously adapted Strix **outputs** (canonical findings, dedupe, SARIF) and Strix's
  **MCP supervision** idea. That is NOT the same as running the Strix agent.
* The Root Agent runs on `agents.sandbox.SandboxAgent` with `Filesystem`/`Shell` sandbox
  capabilities — an external dependency plus a sandbox that GENIE has deferred.
* Strix's own scope is **agent-managed** (`scope_rules` is a tool the agent calls), so scope must be
  imposed externally by GENIE, never self-expanded by the runtime.

Therefore GENIE gets an explicit boundary: `SecurityScope` (GENIE-authoritative, immutable-widening)
and `StrixSpecialistRuntime` (lazy adapter + findings bridge). When Strix and its sandbox are
available the runtime is callable; until then it reports `pending-live-acceptance` honestly rather
than faking a scan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from core.logging_setup import get_logger
    log = get_logger("integrations.strix_runtime")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("integrations.strix_runtime")

RUNTIME_ID = "strix_security"

#: Test classes GENIE is willing to authorise; Strix may only use a subset of these.
PERMITTED_TEST_CLASSES = (
    "recon", "web", "api", "config", "dependency", "secret", "authz",
)


class ScopeViolation(Exception):
    """An attempt to act outside the GENIE-authorised security scope."""


@dataclass(frozen=True)
class SecurityScope:
    """GENIE-authoritative authorisation for one security mission.

    Created by GENIE (PTE), never by Strix. There is deliberately **no** method that widens an
    existing scope — a new scope must be minted by GENIE.
    """

    mission_id: str
    target: str
    allowed_domains: List[str] = field(default_factory=list)
    repo_paths: List[str] = field(default_factory=list)
    permitted_test_classes: List[str] = field(default_factory=lambda: list(PERMITTED_TEST_CLASSES))
    destructive: bool = False            # destructive=false by default
    expiry_ms: int = 0                   # 0 = no expiry recorded (still GENIE-controlled)
    budget_usd: float = 0.0

    def allows(self, *, target: Optional[str] = None, test_class: Optional[str] = None,
               destructive: bool = False) -> bool:
        if destructive and not self.destructive:
            return False
        if test_class and test_class not in self.permitted_test_classes:
            return False
        if target and target != self.target:
            # a target is in scope only if it equals the target or sits under an allowed domain
            if not any(target == d or target.endswith("." + d) for d in self.allowed_domains):
                return False
        return True

    def require(self, **kw: Any) -> None:
        """Raise ScopeViolation unless the action is authorised."""
        if not self.allows(**kw):
            raise ScopeViolation(
                f"outside authorised scope: {kw} (mission {self.mission_id}, "
                f"target={self.target!r}, destructive={self.destructive})")

    def to_dict(self) -> Dict[str, Any]:
        return {"mission_id": self.mission_id, "target": self.target,
                "allowed_domains": list(self.allowed_domains),
                "repo_paths": list(self.repo_paths),
                "permitted_test_classes": list(self.permitted_test_classes),
                "destructive": self.destructive, "expiry_ms": self.expiry_ms,
                "budget_usd": self.budget_usd}


class StrixSpecialistRuntime:
    """GENIE-side boundary for the preserved Strix security runtime."""

    id = RUNTIME_ID

    def __init__(self, findings_service=None, *, strix_factory=None, scope=None):
        self.findings_service = findings_service
        self._factory = strix_factory        # injectable double / adapter
        # GENIE-minted authorisation bound at construction by the launcher.
        # Strix may never mint or widen this; only GENIE (PTE) may.
        self._scope = scope

    # ---------------------------------------------------------------- status
    @staticmethod
    def strix_present() -> bool:
        """True when the upstream Strix package (and its sandbox deps) are importable."""
        try:
            import importlib
            importlib.import_module("strix.agents.factory")
            importlib.import_module("agents.sandbox")
            return True
        except Exception:
            return False

    def describe(self) -> Dict[str, Any]:
        present = self.strix_present()
        return {"id": self.id, "original_runtime_available": present,
                "state": "live" if present else "pending-live-acceptance",
                "authority": "GENIE SecurityScope; Strix cannot self-expand",
                "blockers": [] if present else [
                    "upstream 'strix' package not importable in this environment",
                    "requires agents.sandbox (SandboxAgent + Filesystem/Shell) — GENIE sandbox deferred",
                    "requires proxy/browser tooling (Caido) for full capability",
                ]}

    # ------------------------------------------------------------------ run
    def run_scan(self, scope: SecurityScope, objective: str, *,
                 test_classes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Authorise and (if available) run a Strix scan. Never fakes a result."""
        for tc in (test_classes or []):
            scope.require(test_class=tc)
        if not self.strix_present() and self._factory is None:
            return {"ok": False, "state": "pending-live-acceptance",
                    "error": "original Strix runtime is not available; not faking a scan",
                    "scope": scope.to_dict()}
        factory = self._factory
        if factory is None:                       # pragma: no cover - real path
            from strix.agents import factory as factory  # type: ignore
        result = factory(objective=objective, scope=scope)
        return self._bridge(result, scope)

    # --------------------------------------------------------------- bridge
    def _bridge(self, raw: Any, scope: SecurityScope) -> Dict[str, Any]:
        """Bring Strix reports into GENIE's canonical finding store (never trusts blindly)."""
        findings = []
        for item in _iter_reports(raw):
            fid = self._record(item, scope)
            if fid:
                findings.append(fid)
        return {"ok": True, "state": "live", "mission_id": scope.mission_id,
                "findings": findings, "count": len(findings)}

    def _record(self, item: Dict[str, Any], scope: SecurityScope) -> Optional[str]:
        if self.findings_service is None:
            return None
        try:
            rec = self.findings_service.record(
                target=item.get("target") or scope.target,
                title=item.get("title") or "strix finding",
                finding_class=item.get("type") or "vulnerability",
                severity=item.get("severity") or "medium",
                evidence=str(item.get("evidence") or "")[:2000],
                reproduction=str(item.get("reproduction") or ""),
                remediation=str(item.get("remediation") or ""),
                mission_id=scope.mission_id,
                reported_by=["strix"],
            )
            return getattr(rec, "finding_id", None) or (rec or {}).get("finding_id")
        except Exception as exc:
            log.warning("strix finding bridge failed: %s", exc)
            return None


def _iter_reports(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("vulnerabilities", "findings", "reports"):
            value = raw.get(key)
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []
