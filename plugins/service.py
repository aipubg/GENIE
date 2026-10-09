"""Plugin Service (plugins/service.py).

The daemon-side facade: discovers plugins, supervises their host processes, enforces
permissions, and routes capability invocations. Plugin capabilities look exactly like any
other GENIE capability from the outside:

    plugin.spotify.pause        -> PTE scope  plugin:spotify:pause
                                -> declared permission application.spotify.control (must be granted)

Rules enforced here:
  * default deny — a plugin with no granted permissions cannot be invoked at all
  * a plugin never receives GENIE's database, memory or NEDLE2 state
  * every invocation is audited and emits events
  * plugin failures are contained: the daemon keeps running and the caller gets a structured error
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, EventType
from core.events import get_bus
from core.logging_setup import get_logger
from plugins.client import PluginClient
from plugins.registry import PluginRecord, PluginRegistry
from plugins.sdk import PluginManifest

log = get_logger("plugins.service")


@dataclass
class PluginInvocation:
    capability: str
    plugin_id: str
    ok: bool
    available: bool = True
    detail: str = ""
    latency_ms: int = 0
    error: str = ""
    error_code: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"capability": self.capability, "plugin": self.plugin_id, "ok": self.ok,
                "available": self.available, "detail": self.detail,
                "latency_ms": self.latency_ms, "error": self.error,
                "error_code": self.error_code, "raw": self.raw}


class PluginService:
    def __init__(self, db, audit=None, trust=None, root: str | Path | None = None,
                 crash_limit: int = 3, autostart: bool = True):
        self.db = db
        self.audit = audit
        self.trust = trust
        self.registry = PluginRegistry(db, root=root)
        self.crash_limit = crash_limit
        self._clients: Dict[str, PluginClient] = {}
        self._bus = get_bus()
        self.registry.load_all()
        if autostart:
            self.start_enabled()

    # ------------------------------------------------------------------ lifecycle
    def start_enabled(self) -> Dict[str, Any]:
        started, skipped = [], []
        for record in self.registry.all():
            if not record.enabled or record.state == "unsupported":
                skipped.append(record.manifest.id)
                continue
            result = self.client(record.manifest.id).start()
            (started if result.get("ok") else skipped).append(record.manifest.id)
            self._bus.publish(EventType.PLUGIN_LOADED,
                              {"plugin": record.manifest.id, "ok": result.get("ok")})
        return {"ok": True, "started": started, "skipped": skipped}

    def client(self, plugin_id: str) -> PluginClient:
        client = self._clients.get(plugin_id)
        if client is not None:
            return client
        record = self.registry.get(plugin_id)
        if record is None:
            raise KeyError(plugin_id)
        client = PluginClient(record.manifest, record.directory, crash_limit=self.crash_limit)
        self._clients[plugin_id] = client
        return client

    def stop_all(self) -> None:
        for client in self._clients.values():
            client.stop()

    # ------------------------------------------------------------------ discovery
    def install(self, source: str | Path, grant: Optional[List[str]] = None) -> Dict[str, Any]:
        result = self.registry.install(source, grant_permissions=grant)
        if result.get("ok"):
            self.registry.load_all()
        return result

    def uninstall(self, plugin_id: str, remove_files: bool = True) -> Dict[str, Any]:
        client = self._clients.pop(plugin_id, None)
        if client:
            client.stop()
        return self.registry.uninstall(plugin_id, remove_files=remove_files)

    def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        result = self.registry.set_enabled(plugin_id, enabled)
        client = self._clients.get(plugin_id)
        if client is not None:
            if enabled:
                client.enable()
            else:
                client.stop()
        return result

    def grant(self, plugin_id: str, permissions: List[str]) -> Dict[str, Any]:
        return self.registry.grant(plugin_id, permissions)

    def revoke(self, plugin_id: str, permissions: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.registry.revoke(plugin_id, permissions)

    # --------------------------------------------------------------- capability API
    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        return self.registry.capabilities()

    def owns(self, capability: str) -> bool:
        return capability.startswith("plugin.") and capability in self.capabilities()

    def scope_for(self, capability: str) -> str:
        parts = capability.split(".")
        if len(parts) >= 3 and parts[0] == "plugin":
            return f"plugin:{parts[1]}:{parts[2]}"
        return f"plugin:{capability}"

    def required_permissions(self, capability: str) -> List[str]:
        entry = self.capabilities().get(capability)
        if not entry:
            return []
        spec = entry["spec"]
        declared = getattr(spec, "permissions", None) or entry["manifest"].permissions
        return list(declared)

    def missing_permissions(self, capability: str) -> List[str]:
        entry = self.capabilities().get(capability)
        if not entry:
            return []
        granted = set(entry["record"].granted_permissions)
        return [p for p in self.required_permissions(capability) if p not in granted]

    def execute(self, ctx: CallContext, capability: str,
                params: Dict[str, Any] | None = None) -> PluginInvocation:
        params = dict(params or {})
        entry = self.capabilities().get(capability)
        if entry is None:
            return PluginInvocation(capability, "", False, available=False,
                                    error=f"unknown plugin capability {capability}",
                                    error_code="unknown_capability")
        plugin_id = entry["plugin_id"]
        record: PluginRecord = entry["record"]

        # 1. declared permissions must be granted (default deny)
        missing = self.missing_permissions(capability)
        if missing:
            self._audit(ctx, capability, "denied", f"missing permissions: {missing}")
            self._bus.publish(EventType.PERMISSION_DENIED,
                              {"capability": capability, "plugin": plugin_id,
                               "missing": missing}, trace_id=ctx.trace_id)
            return PluginInvocation(capability, plugin_id, False, available=True,
                                    detail="permission denied",
                                    error=f"plugin {plugin_id} lacks permissions: {', '.join(missing)}",
                                    error_code="permission_denied")

        # 2. PTE check on the capability scope
        if self.trust:
            decision = self.trust.check(ctx, self.scope_for(capability), params)
            if not decision.allow:
                self._audit(ctx, capability, "denied", decision.reason)
                return PluginInvocation(capability, plugin_id, False, available=True,
                                        detail="denied by trust engine",
                                        error=decision.reason, error_code="permission_denied")

        # 3. invoke in the plugin host process
        started = time.time()
        result = self.client(plugin_id).invoke(capability, params)
        latency = int((time.time() - started) * 1000)
        ok = bool(result.get("ok"))
        available = bool(result.get("available", True))
        detail = str(result.get("detail") or result.get("error") or "")
        invocation = PluginInvocation(
            capability=capability, plugin_id=plugin_id, ok=ok, available=available,
            detail=detail, latency_ms=result.get("latency_ms") or latency,
            error="" if ok else str(result.get("error") or detail),
            error_code=str(result.get("error_code", "")), raw=result)
        self._audit(ctx, capability, "ok" if ok else "failed",
                    detail or invocation.error)

        client = self._clients.get(plugin_id)
        if client is not None and client.state == "disabled":
            self.registry.mark_state(plugin_id, "disabled", client.stats.last_error)
            self._bus.publish("PLUGIN_DISABLED", {"plugin": plugin_id,
                                                  "reason": client.stats.last_error})
        return invocation

    # ------------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        plugins = []
        for record in self.registry.all():
            client = self._clients.get(record.manifest.id)
            info = record.to_dict()
            info["runtime"] = client.status() if client else {"state": "not_started"}
            info["missing_permissions"] = [
                p for p in record.manifest.permissions if p not in record.granted_permissions]
            plugins.append(info)
        return {"count": len(plugins), "plugins": plugins,
                "capabilities": sorted(self.capabilities().keys())}

    def health(self, plugin_id: str | None = None) -> Dict[str, Any]:
        if plugin_id:
            return {plugin_id: self.client(plugin_id).health()}
        return {record.manifest.id: self.client(record.manifest.id).health()
                for record in self.registry.all() if record.enabled}

    # ------------------------------------------------------------------ internals
    def _audit(self, ctx: CallContext, capability: str, result: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who=ctx.person_id, device=ctx.device_id,
                              action=f"plugin.{capability}", why=ctx.mission_id or "direct",
                              mission_id=ctx.mission_id,
                              result=f"{result}: {detail}"[:300], trace_id=ctx.trace_id)
