"""User Control Center (core/controlcenter.py) — Phase 13.

The roadmap's exit gate is specific and it is about **owner sovereignty**, not decoration:

    owner can see "what do you remember / which agents run / which provider sees my data"
    in 3 clicks.

So this module answers exactly three questions, and it answers them from live services rather
than from a cached summary that could drift:

1. **What do you remember?** — recent memory records, grouped by type, each one forgettable.
2. **Which agents run?** — live teams, their agents, what they are doing, and what it costs.
3. **Which provider sees my data?** — every configured provider, whether it is local or remote
   (the privacy-relevant distinction), which models are enabled, and what is currently routed.

Design rules:
* **read-only by default** — seeing is always safe. The one mutating action is *forgetting* a
  memory, which is the owner explicitly reducing what GENIE knows.
* **never invent** — if a service is unavailable the section reports that, rather than showing
  an empty list that would read as "GENIE remembers nothing".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.controlcenter")

#: provider ids/shapes that keep data on this machine
LOCAL_HINTS = ("local", "offline", "ollama", "llama.cpp", "llamacpp", "vosk", "127.0.0.1",
               "localhost")


class ControlCenter:
    """The three answers the owner is entitled to, assembled from live services."""

    def __init__(self, *, memory=None, registry=None, agents=None, audit=None, gateway=None):
        self.memory = memory
        self.registry = registry
        self.agents = agents
        self.audit = audit
        self.gateway = gateway

    # ------------------------------------------------------------- the three
    def overview(self, *, memory_limit: int = 25) -> Dict[str, Any]:
        return {"memory": self.memory_view(limit=memory_limit),
                "agents": self.agents_view(),
                "providers": self.providers_view()}

    # 1 ---------------------------------------------------------- what do you remember
    def memory_view(self, limit: int = 25) -> Dict[str, Any]:
        if self.memory is None:
            return {"available": False, "reason": "memory service not available",
                    "records": [], "by_type": {}, "total": 0}
        try:
            from core.contracts import CallContext
            ctx = CallContext()
            hits = self.memory.recent(ctx, limit=limit) or []
            records = [h.to_dict() if hasattr(h, "to_dict") else dict(h) for h in hits]
        except Exception as exc:
            log.debug("control center memory failed: %s", exc)
            return {"available": False, "reason": str(exc), "records": [], "by_type": {},
                    "total": 0}
        by_type: Dict[str, int] = {}
        for r in records:
            key = str(r.get("record", {}).get("type") or r.get("type") or "other")
            by_type[key] = by_type.get(key, 0) + 1
        return {"available": True, "records": records, "by_type": by_type,
                "total": len(records), "limit": limit}

    # 2 ------------------------------------------------------------- which agents run
    def agents_view(self) -> Dict[str, Any]:
        if self.agents is None:
            return {"available": False, "reason": "agent service not available",
                    "teams": [], "live_agents": 0, "running_tasks": 0}
        try:
            status = self.agents.status()
            teams = status.get("teams", [])
            running = 0
            for team in teams:
                inner = team.get("status", {})
                running += len([t for t in inner.get("tasks", {}).get("tasks", [])
                                if t.get("status") == "running"])
            return {"available": True, "teams": teams,
                    "live_agents": sum(len(t.get("status", {}).get("team", []))
                                       for t in teams),
                    "running_tasks": running,
                    "failovers": status.get("failovers", 0),
                    "experience": status.get("experience", {})}
        except Exception as exc:
            log.debug("control center agents failed: %s", exc)
            return {"available": False, "reason": str(exc), "teams": [],
                    "live_agents": 0, "running_tasks": 0}

    # 3 ---------------------------------------------------- which provider sees my data
    def providers_view(self) -> Dict[str, Any]:
        if self.registry is None:
            return {"available": False, "reason": "provider registry not available",
                    "providers": [], "remote": 0, "local": 0}
        try:
            raw = self.registry.providers() or []
        except Exception as exc:
            log.debug("control center providers failed: %s", exc)
            return {"available": False, "reason": str(exc), "providers": [],
                    "remote": 0, "local": 0}

        providers: List[Dict[str, Any]] = []
        remote = local = 0
        for p in raw:
            is_local = self._is_local(p)
            if is_local:
                local += 1
            else:
                remote += 1
            models = self._models_for(p)
            providers.append({
                "provider_id": p.get("id") or p.get("provider_id") or "",
                "name": p.get("name") or p.get("id") or "",
                "enabled": bool(p.get("enabled", True)),
                "kind": p.get("kind") or p.get("type") or "",
                "locality": "local" if is_local else "remote",
                "sees_my_data": (not is_local) and bool(p.get("enabled", True)),
                "base_url": p.get("base_url") or p.get("url") or "",
                "models": models,
                "model_count": len(models),
            })
        return {"available": True, "providers": providers, "remote": remote,
                "local": local,
                "note": "'sees_my_data' is true only for enabled providers that are not local"}

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        """`registry.models()` returns `ModelSpec` dataclasses, not dicts. Accept either."""
        if isinstance(value, dict):
            return value
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            try:
                return to_dict()
            except Exception:
                pass
        return {key: getattr(value, key) for key in
                ("provider_id", "model_id", "id", "display_name", "name", "enabled")
                if hasattr(value, key)}

    def _models_for(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        pid = str(provider.get("id") or provider.get("provider_id") or "")
        try:
            models = self.registry.models(enabled_only=False) or []
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for m in models:
            d = self._as_dict(m)
            mpid = str(d.get("provider_id") or d.get("provider") or "")
            if pid and mpid and mpid != pid:
                continue
            out.append({"model_id": d.get("model_id") or d.get("id") or "",
                        "name": d.get("display_name") or d.get("name") or "",
                        "enabled": bool(d.get("enabled", True))})
        return out

    @staticmethod
    def _is_local(provider: Dict[str, Any]) -> bool:
        """Local vs remote is the privacy-relevant distinction the owner cares about."""
        if provider.get("local") is True or provider.get("offline") is True:
            return True
        haystack = " ".join(str(provider.get(k, "")) for k in
                            ("id", "provider_id", "name", "base_url", "url", "kind", "type")
                            ).lower()
        return any(hint in haystack for hint in LOCAL_HINTS)

    # ------------------------------------------------------- the one mutating action
    def forget(self, record_id: str, *, ctx=None) -> Dict[str, Any]:
        """The owner reducing what GENIE knows. Everything else here is read-only."""
        if self.memory is None:
            return {"ok": False, "error": "memory service not available"}
        try:
            from core.contracts import CallContext
            result = self.memory.forget(ctx or CallContext(), record_id=record_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if self.audit is not None:
            try:
                self.audit.record(who="owner", action="control_center.forget",
                                  why=record_id, result="ok")
            except Exception:
                pass
        return {"ok": True, "forgotten": record_id, "result": result}
