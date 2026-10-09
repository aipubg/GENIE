"""A2A specialist-agent boundary (agents/a2a.py) — re-audit 14.5.

Capability donor: **AgentScope** (A2A protocol support).

Today a bespoke specialist (a PUBG agent, a security agent, a finance agent) would need a
GENIE-only protocol. A2A gives one standard boundary instead:

    GENIE
      -> SpecialistAgentRegistry   (what specialists exist, what can they do?)
        -> A2AProvider             (how to actually call one)
          -> external specialist agent (separate process/machine/project)

GENIE stays the authority: it decides *whether* and *when* to call a specialist. The specialist
never becomes a second brain — it is a callable capability with a declared contract.

Honesty rule: a registered card is **not** proof that the agent is live. Without a reachable
transport, ``invoke`` returns ``status="pending-live-acceptance"`` rather than pretending the call
succeeded. A registry entry alone must never be reported as working capability.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.a2a")

PENDING = "pending-live-acceptance"
LIVE = "live"


@dataclass
class AgentCard:
    """What a specialist agent declares about itself."""

    name: str
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    endpoint: str = ""
    version: str = ""
    owner: str = "genie"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "capabilities": list(self.capabilities), "endpoint": self.endpoint,
                "version": self.version, "owner": self.owner}

    def supports(self, method: str) -> bool:
        return method in self.capabilities


class SpecialistAgentRegistry:
    """Catalogue of specialist agents GENIE may call."""

    def __init__(self):
        self._cards: Dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> AgentCard:
        self._cards[card.name] = card
        return card

    def unregister(self, name: str) -> bool:
        return self._cards.pop(name, None) is not None

    def get(self, name: str) -> Optional[AgentCard]:
        return self._cards.get(name)

    def list(self) -> List[AgentCard]:
        return list(self._cards.values())

    def find_by_capability(self, method: str) -> List[AgentCard]:
        return [c for c in self._cards.values() if c.supports(method)]

    def __len__(self) -> int:
        return len(self._cards)


class A2AProvider:
    """Calls a specialist agent over a pluggable transport.

    ``transport`` is ``(card, method, params) -> dict``. Without one, calls are reported as pending
    rather than faked — a declared capability is not a working one.
    """

    def __init__(self, registry: Optional[SpecialistAgentRegistry] = None,
                 transport: Optional[Callable[[AgentCard, str, Dict[str, Any]], Dict[str, Any]]]
                 = None):
        self.registry = registry or SpecialistAgentRegistry()
        self.transport = transport
        self.calls: List[Dict[str, Any]] = []

    # ----------------------------------------------------------------- invoke
    def invoke(self, agent: str, method: str, params: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
        params = dict(params or {})
        card = self.registry.get(agent)
        if card is None:
            return self._record(agent, method, {"ok": False, "status": "unknown-agent",
                                                "error": f"no specialist registered: {agent}"})
        if not card.supports(method):
            return self._record(agent, method, {
                "ok": False, "status": "unsupported-method",
                "error": f"{agent} does not declare capability '{method}'; "
                         f"declared: {card.capabilities}"})
        if self.transport is None:
            # nothing to call yet — say so instead of inventing a result
            return self._record(agent, method, {
                "ok": False, "status": PENDING, "agent": agent, "method": method,
                "error": "no A2A transport configured — specialist is registered but not live",
                "card": card.to_dict()})

        try:
            result = self.transport(card, method, params) or {}
        except Exception as exc:
            log.debug("A2A call %s.%s failed: %s", agent, method, exc)
            return self._record(agent, method, {"ok": False, "status": "transport-error",
                                                "agent": agent, "method": method,
                                                "error": str(exc)})

        result.setdefault("agent", agent)
        result.setdefault("method", method)
        result.setdefault("ok", True)
        result.setdefault("status", LIVE)
        return self._record(agent, method, result)

    def _record(self, agent: str, method: str, result: Dict[str, Any]) -> Dict[str, Any]:
        result["invoked_ms"] = int(time.time() * 1000)
        self.calls.append({"agent": agent, "method": method,
                           "status": result.get("status"), "ok": result.get("ok")})
        return result

    # ------------------------------------------------------------------ state
    def status(self) -> Dict[str, Any]:
        return {"registered": len(self.registry),
                "agents": [c.name for c in self.registry.list()],
                "transport": "configured" if self.transport else "none",
                "calls": len(self.calls),
                "live_capable": bool(self.transport)}
