"""Permission & Trust Engine (security/trust) — contract C1.

Rules implemented:
  * default deny
  * scope grammar: <domain>:<resource>:<action>[:qualifier]  with * wildcards
  * grants: one_time (consumed on use), session, standing, conditional
  * destructive actions require explicit confirmation (+ undo plan) for everyone,
    and are denied for non-owner personas
  * every decision is audited and published on the event bus
"""
from __future__ import annotations

import fnmatch
import re
from typing import Dict, List, Optional

from core.contracts import (
    CallContext, Decision, Grant, GrantType, Persona, EventType, now_ms, new_id,
)
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("security.trust")

DESTRUCTIVE_HINTS = (
    "delete", "remove", "format", "uninstall", "overwrite", "truncate",
    "registry", "shutdown", "restart", "kill", "purchase", "payment", "send_message",
)
SENSITIVE_HINTS = ("credential", "secret", "token", "password", "key")

# Bootstrap grants for the owner so the system is usable out of the box (A-006).
# Applied idempotently per scope, so a GENIE upgrade adds new scopes without touching
# existing grants or the user's own choices. Non-owner personas stay default-deny.
DEFAULT_OWNER_SCOPES = [
    # applications, windows, processes
    "computer:app:read", "computer:app:open", "computer:app:close",
    "computer:window:read", "computer:window:control",
    "computer:processes:read", "computer:processes:kill",
    # system / audio / screen / state
    "computer:system:read", "computer:system.volume:read", "computer:system.volume:set",
    "computer:system.volume:up", "computer:system.volume:down", "computer:system.volume:mute",
    "computer:screen:read", "computer:state:read",
    "computer:desktop:read", "computer:desktop:control", "computer:desktop:write",
    # files, clipboard
    "computer:files:read", "computer:files:write", "computer:files:delete",
    "computer:clipboard:read", "computer:clipboard:write",
    # shell (tier 'elevated' still requires an explicit confirmation upstream)
    "computer:shell:run",
    # accessibility + input
    "computer:uia:read", "computer:uia:control",
    "computer:input:read", "computer:input:keyboard", "computer:input:mouse",
    "computer:input:control",
    # workspace — §11C GENIE Workspace / background computer
    "computer:workspace:read", "computer:workspace:write", "computer:workspace:exec",
    # browser
    "browser:read", "browser:navigate", "browser:dom:read", "browser:dom:control",
    "browser:tab:control", "browser:upload", "browser:download",
    # browser media (search + verified playback) and fullscreen — both reuse the
    # same authorized browser/CDP authority the scopes above already cover.
    "browser:media:play", "browser:fullscreen",
    # plugins: the PTE gate is "may the owner invoke plugin capabilities at all".
    # The plugin-specific gate is the declared-permission grant, which is still explicit and
    # default-deny per plugin (D-052).
    "plugin:*:*",
    # plugins: the PTE gate is "may the owner invoke plugin capabilities at all".
    # The plugin-specific gate is the declared-permission grant, which is still explicit and
    # default-deny per plugin (D-052).
    "plugin:*:*",
    # memory / missions / devices / providers
    "memory:*:read", "memory:user:write", "memory:user:delete",
    "mission:*:*", "device:*:read", "provider:*:use",
]


def scope_matches(pattern: str, scope: str) -> bool:
    return fnmatch.fnmatch(scope, pattern) or fnmatch.fnmatch(scope, pattern.replace("**", "*"))


class TrustService:
    def __init__(self, db, audit=None, default_deny: bool = True):
        self.db = db
        self.audit = audit
        self.default_deny = default_deny
        self._bus = get_bus()
        self._bootstrap_owner()

    # ------------------------------------------------------------------ grants
    def _bootstrap_owner(self) -> None:
        """Idempotent per scope: an upgrade adds newly introduced scopes without
        duplicating or overwriting grants the owner already has (A-006)."""
        for scope in DEFAULT_OWNER_SCOPES:
            row = self.db.query_one(
                "SELECT grant_id FROM grants WHERE principal='owner' AND scope=?"
                " AND revoked_at IS NULL", (scope,))
            if row:
                continue
            self.grant(
                principal="owner", scope=scope, grant_type=GrantType.STANDING,
                granted_by="system", reason="bootstrap owner grant (A-006)")

    def grant(self, principal: str, scope: str, grant_type: GrantType = GrantType.STANDING,
              granted_by: str = "owner", ttl_ms: int | None = None,
              reason: str = "") -> str:
        grant_id = new_id("grant")
        now = now_ms()
        self.db.execute(
            "INSERT INTO grants(grant_id, principal, scope, grant_type, granted_by,"
            " granted_at, expires_at, revoked_at, reason, consumed)"
            " VALUES(?,?,?,?,?,?,?,?,?,0)",
            (grant_id, principal, scope, grant_type.value, granted_by, now,
             now + ttl_ms if ttl_ms else None, None, reason))
        self._emit(EventType.PERMISSION_GRANTED, {"grant_id": grant_id, "principal": principal,
                                                  "scope": scope, "type": grant_type.value})
        return grant_id

    def revoke(self, grant_id: str) -> bool:
        cur = self.db.execute("UPDATE grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                              (now_ms(), grant_id))
        return cur.rowcount > 0

    def grants_for(self, principal: str) -> List[Dict[str, object]]:
        rows = self.db.query("SELECT * FROM grants WHERE principal=?", (principal,))
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- decisions
    def _candidate_grants(self, principal: str, scope: str) -> List[sqlite_row]:
        rows = self.db.query(
            "SELECT * FROM grants WHERE principal=? AND revoked_at IS NULL", (principal,))
        now = now_ms()
        out = []
        for r in rows:
            if r["expires_at"] and now > r["expires_at"]:
                continue
            if scope_matches(r["scope"], scope) or r["scope"] == "*":
                out.append(r)
        return out

    def _is_destructive(self, scope: str, params: Dict[str, object] | None = None) -> bool:
        """Word-boundary matching so PowerShell parameters like `-Format` are not mistaken
        for destructive intent (A-032)."""
        parts = [scope.lower()]
        for key, value in (params or {}).items():
            # Grounding metadata and literal editor content describe the target
            # or payload, not an operation. A document about "delete" is not a
            # deletion. Shell commands and actual destructive scopes stay checked.
            if key in {"task_id", "_holder", "_trace_id", "window", "verify_in_window", "title", "target_process"}:
                continue
            if scope in {"computer:input:keyboard", "computer:uia:control"} and key in {"text", "value"}:
                continue
            if isinstance(value, str):
                parts.append(value.lower())
            elif isinstance(value, (list, tuple)):
                parts.append(" ".join(str(v).lower() for v in value))
        blob = " ".join(parts)
        for hint in DESTRUCTIVE_HINTS:
            if re.search(rf"(?<![-\w]){re.escape(hint)}(?![-\w])", blob):
                return True
        return False

    def check(self, ctx: CallContext, scope: str,
              params: Dict[str, object] | None = None) -> Decision:
        principal = ctx.person_id
        persona = ctx.persona

        # 1. destructive guard -------------------------------------------------
        destructive = self._is_destructive(scope, params)
        if destructive and persona not in (Persona.OWNER,):
            decision = Decision(False, f"destructive action denied for persona={persona.value}")
            return self._record(ctx, scope, decision)

        # 2. explicit grants ---------------------------------------------------
        for row in self._candidate_grants(principal, scope):
            if row["grant_type"] == GrantType.ONE_TIME.value and row["consumed"]:
                continue
            if destructive and self.default_deny:
                decision = Decision(True, "destructive action requires confirmation",
                                    grant_id=row["grant_id"], needs_confirm=True)
                return self._record(ctx, scope, decision)
            if row["grant_type"] == GrantType.ONE_TIME.value:
                self.db.execute("UPDATE grants SET consumed=1 WHERE grant_id=?", (row["grant_id"],))
            decision = Decision(True, f"grant {row['grant_id']} ({row['grant_type']})",
                                grant_id=row["grant_id"])
            return self._record(ctx, scope, decision)

        # 3. explicit scopes carried by the call context (agent runtime) -------
        for s in ctx.scopes:
            if scope_matches(s, scope):
                decision = Decision(True, f"ctx scope {s}")
                return self._record(ctx, scope, decision)

        # 4. default deny ------------------------------------------------------
        decision = Decision(False, f"no grant for {scope} (principal={principal})")
        return self._record(ctx, scope, decision)

    def _record(self, ctx: CallContext, scope: str, decision: Decision) -> Decision:
        if self.audit:
            self.audit.record(who=ctx.person_id, device=ctx.device_id, action=f"trust.check:{scope}",
                              why=decision.reason, mission_id=ctx.mission_id,
                              result="allow" if decision.allow else "deny", trace_id=ctx.trace_id)
        if not decision.allow:
            self._emit(EventType.PERMISSION_DENIED,
                       {"scope": scope, "principal": ctx.person_id, "reason": decision.reason,
                        "trace_id": ctx.trace_id})
        log.info("trust.check scope=%s allow=%s reason=%s", scope, decision.allow, decision.reason)
        return decision

    def _emit(self, event_type: str, payload: Dict[str, object]) -> None:
        try:
            self._bus.publish(event_type, payload)
        except Exception:  # bus must never break authorization
            pass


sqlite_row = dict  # type: ignore  (sqlite3.Row behaves like a mapping)


_TRUST: Optional[TrustService] = None


def get_trust(db=None, audit=None) -> TrustService:
    global _TRUST
    if _TRUST is None:
        if db is None:
            from core.db import get_db
            db = get_db()
        _TRUST = TrustService(db, audit=audit)
    return _TRUST
