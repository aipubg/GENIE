"""Persistent explicit owner grants. No model-facing mutation interface."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from urllib.parse import urlsplit
from pathlib import Path

from core.contracts import Persona


class OwnerPolicy:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._data = {"enabled": False, "grants": []}
        if self.path and self.path.exists():
            try:
                self._data = self.validate(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError, TypeError):
                pass  # damaged policy fails closed

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            raise ValueError("enabled must be boolean")
        grants = data.get("grants", [])
        if not isinstance(grants, list) or len(grants) > 64:
            raise ValueError("At most 64 scoped grants are supported")
        fields = {"screen_analysis": {"provider", "destination", "scope", "application"},
                  "network_toggle": {"window", "control", "enabled"},
                  "message": {"application", "recipient", "message_sha256"}}
        clean = []
        for grant in grants:
            if not isinstance(grant, dict) or grant.get("kind") not in fields:
                raise ValueError("Unknown grant kind")
            required = fields[grant["kind"]]
            legacy_screen = grant["kind"] == "screen_analysis" and "application" not in grant
            if legacy_screen:
                # Retain old authorizations for display, but never expand them
                # into an application scope the owner did not choose.
                grant = {**grant, "application": "legacy-unscoped-requires-reauthorization"}
            if set(grant) != required | {"kind", "expires_at"}:
                raise ValueError("Grant fields must match the exact scope")
            for key in required:
                if key == "enabled":
                    if type(grant[key]) is not bool:
                        raise ValueError("Network state must be boolean")
                elif not isinstance(grant[key], str) or not grant[key] or len(grant[key]) > 2048:
                    raise ValueError("Grant scope must be a nonempty exact string")
            if grant["kind"] == "screen_analysis" and grant["scope"] != "cropped-redacted-window":
                raise ValueError("Only cropped redacted windows may be authorized")
            if grant["kind"] == "screen_analysis":
                grant = dict(grant)
                parsed = urlsplit(grant["destination"] if "://" in grant["destination"] else "https://" + grant["destination"])
                if not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("Screen destination must be an exact provider hostname")
                grant["destination"] = parsed.hostname.lower()
            if grant["kind"] == "message" and (len(grant["message_sha256"]) != 64 or
                    any(c not in "0123456789abcdef" for c in grant["message_sha256"])):
                raise ValueError("Message authorization requires its exact SHA-256")
            expires = grant["expires_at"]
            if type(expires) not in (int, float) or not 0 < expires <= time.time() + 366 * 86400:
                raise ValueError("Grant expiry must be bounded to one year")
            clean.append(dict(grant))
        return {"enabled": data["enabled"], "grants": clean}

    def status(self):
        with self._lock:
            return json.loads(json.dumps(self._data))

    def configure(self, data):
        clean = self.validate(data)
        with self._lock:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
                os.replace(temp, self.path)
            self._data = clean
        return self.status()

    def allows(self, ctx, scope):
        if (ctx.person_id != "owner" or ctx.persona != Persona.OWNER or ctx.agent_id
                or ctx.mission_id or ctx.dry_run or not isinstance(scope, dict)):
            return False
        if scope == {"kind": "ordinary_visual"} and ctx.owner_action_requested:
            return True  # the authenticated owner's direct routine action instruction
        with self._lock:
            if not self._data["enabled"]:
                return False
            if scope == {"kind": "ordinary_visual"}:
                return True
            for grant in self._data["grants"]:
                if grant["expires_at"] > time.time() and {
                        k: v for k, v in grant.items() if k != "expires_at"} == scope:
                    return True
        return False

    def decide(self, ctx, scope):
        if (ctx.person_id != "owner" or ctx.persona != Persona.OWNER or ctx.agent_id
                or ctx.dry_run):
            return {"decision": "DENY_WITH_REASON", "reason": "Direct authenticated owner action required."}
        if self.allows(ctx, scope):
            return {"decision": "ALLOW_SILENT", "reason": "direct_owner_command" if
                    scope == {"kind": "ordinary_visual"} and ctx.owner_action_requested else "scoped_owner_grant"}
        return {"decision": "REQUIRE_EXPLICIT_AUTHORIZATION", "reason": "No matching current owner authorization."}

    @staticmethod
    def message_scope(application, recipient, message):
        return {"kind": "message", "application": application, "recipient": recipient,
                "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest()}
