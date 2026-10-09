"""DirectorProvider contract (director/base).

NEDLE2 is the local DIRECTOR — not GENIE's reasoning brain. Its only job is to turn an
utterance/event into a routing decision. It must never own truth (master spec §2.2).

Implementations:
  * HeuristicDirector  — deterministic, offline, always available (fast path)
  * NeedleDirector     — Cactus Needle 2 runtime (cli | python | http)
Anything that fails to produce a valid decision falls back to the heuristic director.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, TaskType


@dataclass
class DirectorTask:
    type: TaskType = TaskType.UNKNOWN
    device: str = "pc_main"
    capability: str = ""
    target: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type.value, "device": self.device, "capability": self.capability,
                "target": self.target, "params": self.params}


@dataclass
class DirectorDecision:
    tasks: List[DirectorTask] = field(default_factory=list)
    reasoning_required: bool = False
    provider_category: str = "none"        # none|reasoning|coding|vision|research
    memory_writes: List[Dict[str, Any]] = field(default_factory=list)
    memory_query: str = ""
    reply_hint: str = ""
    confidence: float = 0.5
    source: str = "heuristic"              # needle|heuristic|remote
    # Point 6 — intent classification. CONVERSATION is a normal exchange and must
    # NOT become a Mission; SIMPLE_ACTION is a single bounded action; MISSION is
    # durable/autonomous work. `mission_required` is the gate the orchestrator
    # uses to decide whether a durable Mission is created; `schedule` carries a
    # recurrence hint ("every morning") when one is present.
    intent: str = "conversation"           # conversation|simple_action|mission
    mission_required: bool = False
    schedule: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "reasoning_required": self.reasoning_required,
            "provider_category": self.provider_category,
            "memory_writes": self.memory_writes,
            "memory_query": self.memory_query,
            "reply_hint": self.reply_hint,
            "confidence": self.confidence,
            "source": self.source,
            "intent": self.intent,
            "mission_required": self.mission_required,
            "schedule": self.schedule,
            # routing diagnostics (why this decision was made)
            "engine": self.raw.get("engine"),
            "runtime": self.raw.get("runtime"),
            "reason": self.raw.get("reason"),
            "latency_ms": self.raw.get("latency_ms"),
            "escalated": bool(self.raw.get("escalated")),
            "model_input": self.raw.get("model_input"),
            # present only when the semantic guard overruled the route (see semantic_guard.py)
            "semantic_guard": self.raw.get("semantic_guard"),
        }


class DirectorProvider:
    """Interface every director implementation must satisfy."""

    name = "base"

    def classify(self, text: str, ctx: CallContext,
                 context_hint: Dict[str, Any] | None = None) -> DirectorDecision:
        raise NotImplementedError

    def available(self) -> bool:
        return True


def parse_decision(payload: Dict[str, Any], source: str) -> DirectorDecision:
    """Validate/clean a model-produced decision. Never trust it blindly (spec §1.7)."""
    tasks: List[DirectorTask] = []
    for t in payload.get("tasks", []) or []:
        try:
            ttype = TaskType(t.get("type", "unknown"))
        except ValueError:
            ttype = TaskType.UNKNOWN
        tasks.append(DirectorTask(
            type=ttype,
            device=str(t.get("device", "pc_main"))[:64],
            capability=str(t.get("capability", ""))[:128],
            target=str(t.get("target", ""))[:256],
            params=t.get("params", {}) if isinstance(t.get("params"), dict) else {},
        ))
    return DirectorDecision(
        tasks=tasks,
        reasoning_required=bool(payload.get("reasoning_required", False)),
        provider_category=str(payload.get("provider_category", "none"))[:32],
        memory_writes=[m for m in (payload.get("memory_writes") or []) if isinstance(m, dict)][:5],
        memory_query=str(payload.get("memory_query", ""))[:512],
        reply_hint=str(payload.get("reply_hint", ""))[:512],
        confidence=float(payload.get("confidence", 0.5) or 0.5),
        source=source,
        intent=str(payload.get("intent", "conversation"))[:32],
        mission_required=bool(payload.get("mission_required", False)),
        schedule=str(payload.get("schedule", ""))[:64],
        raw=payload,
    )
