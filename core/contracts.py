"""GENIE shared contract types (core/contracts).

This module is the single source of truth for the data shapes that cross module
boundaries. It must stay dependency-free (stdlib only) so every layer can import it.

Naming follows docs/CONTRACTS.md (C1..C16).
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    return int(time.time() * 1000)


# Canonical owner session identity — shared by typed Chat, Voice, and mission
# follow-ups so that every owner-facing transport participates in ONE conversation.
OWNER_SESSION_ID: str = "owner"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class DataClass(str, enum.Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"
    RESTRICTED = "RESTRICTED"


class Persona(str, enum.Enum):
    OWNER = "owner"
    MEMBER = "member"
    GUEST = "guest"
    SYSTEM = "system"
    AGENT = "agent"
    DEVICE = "device"


class MissionState(str, enum.Enum):
    CREATED = "CREATED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ALLOWED_TRANSITIONS: Dict[MissionState, set] = {
    MissionState.CREATED: {MissionState.PLANNED, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.PLANNED: {MissionState.RUNNING, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.RUNNING: {
        MissionState.WAITING, MissionState.BLOCKED, MissionState.PAUSED,
        MissionState.VERIFYING, MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELLED,
    },
    MissionState.WAITING: {MissionState.RUNNING, MissionState.BLOCKED, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.BLOCKED: {MissionState.RUNNING, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.PAUSED: {MissionState.RUNNING, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.VERIFYING: {MissionState.COMPLETED, MissionState.RUNNING, MissionState.FAILED, MissionState.CANCELLED},
    MissionState.COMPLETED: set(),
    MissionState.FAILED: set(),
    MissionState.CANCELLED: set(),
}


class TaskType(str, enum.Enum):
    DEVICE_ACTION = "device_action"
    APPLICATION_ACTION = "application_action"
    COMPUTER_ACTION = "computer_action"
    BROWSER_ACTION = "browser_action"
    FILE_ACTION = "file_action"
    MEMORY_WRITE = "memory_write"
    MEMORY_QUERY = "memory_query"
    SKILL_RUN = "skill_run"
    RESEARCH = "research"
    CODING = "coding"
    MEDIA = "media"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"


class GrantType(str, enum.Enum):
    ONE_TIME = "one_time"
    SESSION = "session"
    STANDING = "standing"
    CONDITIONAL = "conditional"


class LogLevel(str, enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Call context — mandatory on every cross-module call
# ---------------------------------------------------------------------------
@dataclass
class CallContext:
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    person_id: str = "owner"
    persona: Persona = Persona.OWNER
    session_id: str = OWNER_SESSION_ID
    device_id: str = "pc_main"
    mission_id: Optional[str] = None
    agent_id: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    data_class: DataClass = DataClass.INTERNAL
    idempotency_key: Optional[str] = None
    dry_run: bool = False
    owner_action_requested: bool = False  # set from the original utterance, never tool arguments
    created_ms: int = field(default_factory=now_ms)

    def with_(self, **kw: Any) -> "CallContext":
        data = dict(self.__dict__)
        data.update(kw)
        return CallContext(**data)

    @property
    def interaction_id(self) -> str:
        """Trusted durable identity shared by typed and spoken owner turns."""
        import json
        return json.dumps((self.person_id, self.device_id, self.session_id,
                           self.mission_id or "direct", self.agent_id or "owner"),
                          ensure_ascii=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# C1 Trust / C2 Secrets
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    allow: bool
    reason: str
    grant_id: Optional[str] = None
    needs_confirm: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"allow": self.allow, "reason": self.reason,
                "grant_id": self.grant_id, "needs_confirm": self.needs_confirm}


@dataclass
class Grant:
    grant_id: str
    principal: str
    scope: str
    grant_type: GrantType
    granted_by: str
    granted_at: int
    expires_at: Optional[int] = None
    revoked_at: Optional[int] = None
    reason: str = ""

    def active(self, at_ms: Optional[int] = None) -> bool:
        at = at_ms or now_ms()
        if self.revoked_at:
            return False
        if self.expires_at and at > self.expires_at:
            return False
        if self.grant_type is GrantType.ONE_TIME:
            # one-time grants are consumed by the trust service on first use
            return self.revoked_at is None and (self.expires_at is None or at <= self.expires_at)
        return True


# ---------------------------------------------------------------------------
# C3 Memory
# ---------------------------------------------------------------------------
@dataclass
class MemoryRecord:
    record_id: str = field(default_factory=lambda: new_id("mem"))
    type: str = "semantic"
    entity: str = ""
    value: str = ""
    confidence: float = 0.7
    source: str = "system"
    person_id: str = "owner"
    project: str = ""
    mission_id: str = ""
    privacy_scope: str = "private:owner"
    data_class: DataClass = DataClass.INTERNAL
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
    version: int = 1
    superseded_by: Optional[str] = None
    pinned: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryHit:
    record: MemoryRecord
    score: float

    def to_dict(self) -> Dict[str, Any]:
        d = self.record.__dict__.copy()
        d["score"] = self.score
        d["data_class"] = self.record.data_class.value
        return d


# ---------------------------------------------------------------------------
# C4 Mission
# ---------------------------------------------------------------------------
@dataclass
class MissionStep:
    step_id: str = field(default_factory=lambda: new_id("step"))
    type: TaskType = TaskType.UNKNOWN
    capability: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    # Pass 3 — durable step state. `done` means verified, not merely issued.
    status: str = "pending"          # pending|ready|running|blocked|waiting|
                                     # completed|failed|needs_revision|cancelled
    result: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    objective: str = ""
    required_role: str = "worker"
    completion_criteria: str = ""
    attempt: int = 0
    max_attempts: int = 2
    started_ms: int = 0
    finished_ms: int = 0
    provider_used: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass
class Mission:
    mission_id: str = field(default_factory=lambda: new_id("mis"))
    goal: str = ""
    owner: str = "owner"
    state: MissionState = MissionState.CREATED
    steps: List[MissionStep] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    criteria: List[str] = field(default_factory=list)
    # Point 6 — natural-language recurrence hint when the mission is scheduled
    # (e.g. "every morning"). Empty for a one-off mission.
    schedule: str = ""
    # Pass 3 — durable scheduling + continuous work. `continuous` marks a mission
    # that keeps iterating; `next_run_ms` is the next wake time (0 = none).
    continuous: bool = False
    next_run_ms: int = 0
    last_run_ms: int = 0
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
    errors: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    provider_usage: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["state"] = self.state.value
        d["steps"] = [
            {"step_id": s.step_id, "type": s.type.value, "capability": s.capability,
             "params": s.params, "status": s.status, "result": s.result}
            for s in self.steps
        ]
        return d


# ---------------------------------------------------------------------------
# C5 Model gateway
# ---------------------------------------------------------------------------
@dataclass
class ModelRequirement:
    capability: str = "reasoning"        # reasoning|coding|vision|embedding|fast
    min_quality: str = "medium"          # low|medium|strong
    max_latency_ms: int = 20000
    max_input_tokens: int = 8000
    budget_usd: float = 0.5
    needs_tools: bool = False
    needs_vision: bool = False
    data_class: DataClass = DataClass.INTERNAL
    allowed_provider_ids: Optional[List[str]] = None


@dataclass
class ModelSpec:
    provider_id: str
    model_id: str
    display_name: str
    protocol: str = "openai_chat"        # openai_chat|openai_responses|custom
    capabilities: List[str] = field(default_factory=list)   # tools, vision, reasoning, coding
    context_window: int = 8192
    max_output: int = 2048
    cost_in_per_1k: float = 0.0
    cost_out_per_1k: float = 0.0
    priority: int = 100                  # lower = preferred
    enabled: bool = True
    fallback_of: Optional[str] = None

    def supports(self, req: ModelRequirement) -> bool:
        if not self.enabled:
            return False
        if req.needs_tools and "tools" not in self.capabilities:
            return False
        if req.needs_vision and "vision" not in self.capabilities:
            return False
        if req.capability not in self.capabilities and "general" not in self.capabilities:
            return False
        if self.context_window < req.max_input_tokens:
            return False
        return True


@dataclass
class Completion:
    text: str
    model: str
    provider_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_error: str = ""
    tool_protocol: str = ""


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------------------
# C6 Agent
# ---------------------------------------------------------------------------
@dataclass
class AgentDefinition:
    """A scoped, budgeted worker. Extended in Phase 10 for the agent factory and teams.

    The Phase 1 fields are unchanged; everything added below has a default, so existing
    constructions keep working.
    """

    name: str
    purpose: str = ""
    tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    model_capability: str = "reasoning"
    max_steps: int = 20
    max_cost_usd: float = 0.5
    max_wall_ms: int = 120_000
    ephemeral: bool = True
    # ---- Phase 10: agent factory / teams ----
    agent_id: str = field(default_factory=lambda: new_id("agt"))
    role: str = "worker"
    #: builtin | persistent | mission | candidate  (see agents/contracts.py AgentKind)
    kind: str = "mission"
    #: capability + quality, never a hardcoded provider (D-090)
    model_requirement: Dict[str, Any] = field(default_factory=dict)
    memory_scope: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    profile_key: str = ""            # stable identity across versions

    def to_dict(self) -> Dict[str, Any]:
        return {"agent_id": self.agent_id, "name": self.name, "role": self.role,
                "kind": self.kind, "purpose": self.purpose, "tools": list(self.tools),
                "skills": list(self.skills), "scopes": list(self.scopes),
                "model_capability": self.model_capability,
                "model_requirement": dict(self.model_requirement),
                "memory_scope": dict(self.memory_scope), "budget": dict(self.budget),
                "max_steps": self.max_steps, "max_cost_usd": self.max_cost_usd,
                "max_wall_ms": self.max_wall_ms, "ephemeral": self.ephemeral,
                "version": self.version, "profile_key": self.profile_key or self.agent_id}


@dataclass
class AgentState:
    agent_id: str = field(default_factory=lambda: new_id("agt"))
    name: str = ""
    status: str = "idle"        # idle|running|done|failed|cancelled
    steps_used: int = 0
    cost_usd: float = 0.0
    mission_id: Optional[str] = None
    error: str = ""


# ---------------------------------------------------------------------------
# C7 Computer / C9 Device results
# ---------------------------------------------------------------------------
@dataclass
class ActionResult:
    ok: bool
    capability: str
    detail: str = ""
    verified: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "capability": self.capability, "detail": self.detail,
                "verified": self.verified, "data": self.data}


# ---------------------------------------------------------------------------
# C13 Events
# ---------------------------------------------------------------------------
@dataclass
class Event:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("ev"))
    ts_ms: int = field(default_factory=now_ms)
    trace_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"event_id": self.event_id, "type": self.type, "payload": self.payload,
                "ts_ms": self.ts_ms, "trace_id": self.trace_id}


# Canonical event names (master spec Appendix A)
class EventType:
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_FAILED = "AGENT_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PROVIDER_FAILOVER = "PROVIDER_FAILOVER"
    # A provider attempt failed, classified by models.failures. Carries the
    # failure kind plus retryable / failover_ok / committed guidance so callers
    # never replay work that already produced output (spec section 8).
    PROVIDER_ERROR = "PROVIDER_ERROR"
    APP_OPENED = "APP_OPENED"
    FILE_CHANGED = "FILE_CHANGED"
    DEVICE_CONNECTED = "DEVICE_CONNECTED"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    PERSON_ENTERED = "PERSON_ENTERED"
    USER_TAKEOVER = "USER_TAKEOVER"
    PLUGIN_LOADED = "PLUGIN_LOADED"
    SKILL_CREATED = "SKILL_CREATED"
    MEMORY_UPDATED = "MEMORY_UPDATED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    POLICY_VIOLATION_BLOCKED = "POLICY_VIOLATION_BLOCKED"
    COST_LIMIT_REACHED = "COST_LIMIT_REACHED"
    HIGH_PRIORITY_EVENT = "HIGH_PRIORITY_EVENT"
    # voice (Phase 3)
    SPEECH_STARTED = "SPEECH_STARTED"
    SPEECH_ENDED = "SPEECH_ENDED"
    VOICE_LISTENING = "VOICE_LISTENING"
    VOICE_TRANSCRIPT = "VOICE_TRANSCRIPT"
    VOICE_TRANSCRIPT_FAILED = "VOICE_TRANSCRIPT_FAILED"
    VOICE_SPOKEN = "VOICE_SPOKEN"
    VOICE_STOPPED = "VOICE_STOPPED"
    BARGE_IN = "BARGE_IN"
    # UI visual telemetry (added for the locked Home / blue-gem voice control).
    # VOICE_AMPLITUDE carries REAL microphone RMS measured by the existing capture
    # path. It is never synthesised from text, timers, word counts or randomness.
    VOICE_AMPLITUDE = "VOICE_AMPLITUDE"
    # VOICE_TTS_AMPLITUDE carries the REAL output level of the audio GENIE is
    # playing (RMS envelope of the actual PCM). When there is no audio buffer to
    # measure, it carries available=false instead of a synthesised envelope.
    VOICE_TTS_AMPLITUDE = "VOICE_TTS_AMPLITUDE"
