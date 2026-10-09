"""Agent team contracts (agents/contracts.py) — master spec §2.6, Phase 10.

The rule that shapes every type here: **agents do not share one giant transcript.** They share a
task DAG, a mailbox, a blackboard, an artifact store and a budget. Private reasoning stays private.

    NEDLE2 → Mission Engine → Team Orchestrator → agents
                                    ↓
                          shared mission state (blackboard + artifacts + DAG)
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import new_id


class AgentKind(str, enum.Enum):
    """Where an agent came from. Mission-scoped agents are disposable."""

    BUILTIN = "builtin"              # ships with GENIE
    PERSISTENT = "persistent"        # a retained specialist
    MISSION = "mission"              # generated for one mission, retired after it
    CANDIDATE = "candidate"          # generated and awaiting evaluation


class AgentLifecycle(str, enum.Enum):
    CREATED = "created"
    VALIDATED = "validated"
    READY = "ready"
    WORKING = "working"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    RETIRED = "retired"


#: Legal lifecycle moves. Anything else is refused rather than silently allowed.
ALLOWED_AGENT_TRANSITIONS: Dict[str, set] = {
    AgentLifecycle.CREATED.value: {AgentLifecycle.VALIDATED.value, AgentLifecycle.RETIRED.value},
    AgentLifecycle.VALIDATED.value: {AgentLifecycle.READY.value, AgentLifecycle.RETIRED.value},
    AgentLifecycle.READY.value: {AgentLifecycle.WORKING.value, AgentLifecycle.RETIRED.value},
    AgentLifecycle.WORKING.value: {AgentLifecycle.WAITING.value, AgentLifecycle.BLOCKED.value,
                                   AgentLifecycle.COMPLETED.value,
                                   AgentLifecycle.RETIRED.value},
    AgentLifecycle.WAITING.value: {AgentLifecycle.WORKING.value, AgentLifecycle.RETIRED.value},
    AgentLifecycle.BLOCKED.value: {AgentLifecycle.WORKING.value, AgentLifecycle.RETIRED.value},
    AgentLifecycle.COMPLETED.value: {AgentLifecycle.RETIRED.value, AgentLifecycle.WORKING.value},
    AgentLifecycle.RETIRED.value: set(),
}


class TaskStatus(str, enum.Enum):
    PENDING = "pending"              # waiting for dependencies
    READY = "ready"                  # dependencies satisfied, unclaimed
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"              # a dependency failed
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Terminal statuses — a task in one of these will not run again.
TERMINAL_TASK_STATUSES = {TaskStatus.DONE.value, TaskStatus.FAILED.value,
                          TaskStatus.CANCELLED.value}


class MessageType(str, enum.Enum):
    ARTIFACT_READY = "artifact_ready"
    DECISION = "decision"
    BLOCKER = "blocker"
    REQUEST = "request"
    RESPONSE = "response"
    HANDOFF = "handoff"
    REVIEW_RESULT = "review_result"
    NOTE = "note"


class ArtifactKind(str, enum.Enum):
    FILE = "file"
    DOCUMENT = "document"
    CODE = "code"
    TEST_RESULT = "test_result"
    DATA = "data"
    DECISION = "decision"
    OTHER = "other"


@dataclass
class Budget:
    """A measurable allowance. Children may never exceed a parent's remaining budget."""

    tokens: int = 0                  # 0 = unlimited
    cost_usd: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    wall_ms: int = 0
    # ---- consumed ----
    tokens_used: int = 0
    cost_used: float = 0.0
    model_calls_used: int = 0
    tool_calls_used: int = 0
    started_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def elapsed_ms(self) -> int:
        return int(time.time() * 1000) - self.started_ms

    def remaining(self) -> Dict[str, float]:
        """Remaining allowance. `inf` where the dimension is unlimited."""
        return {
            "tokens": (self.tokens - self.tokens_used) if self.tokens else float("inf"),
            "cost_usd": (self.cost_usd - self.cost_used) if self.cost_usd else float("inf"),
            "model_calls": (self.model_calls - self.model_calls_used) if self.model_calls
            else float("inf"),
            "tool_calls": (self.tool_calls - self.tool_calls_used) if self.tool_calls
            else float("inf"),
            "wall_ms": (self.wall_ms - self.elapsed_ms()) if self.wall_ms else float("inf"),
        }

    def exhausted(self) -> Optional[str]:
        """The name of the first exhausted dimension, or None."""
        for name, left in self.remaining().items():
            if left is not None and left <= 0:
                return name
        return None

    def can_afford(self, *, tokens: int = 0, cost_usd: float = 0.0, model_calls: int = 0,
                   tool_calls: int = 0) -> bool:
        left = self.remaining()
        return (left["tokens"] >= tokens and left["cost_usd"] >= cost_usd
                and left["model_calls"] >= model_calls
                and left["tool_calls"] >= tool_calls)

    def spend(self, *, tokens: int = 0, cost_usd: float = 0.0, model_calls: int = 0,
              tool_calls: int = 0) -> None:
        self.tokens_used += max(0, int(tokens))
        self.cost_used += max(0.0, float(cost_usd))
        self.model_calls_used += max(0, int(model_calls))
        self.tool_calls_used += max(0, int(tool_calls))

    def to_dict(self) -> Dict[str, Any]:
        remaining = self.remaining()
        return {"tokens": self.tokens, "cost_usd": round(self.cost_usd, 6),
                "model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "wall_ms": self.wall_ms, "tokens_used": self.tokens_used,
                "cost_used": round(self.cost_used, 6),
                "model_calls_used": self.model_calls_used,
                "tool_calls_used": self.tool_calls_used,
                "elapsed_ms": self.elapsed_ms(),
                "remaining": {k: (None if v == float("inf") else round(v, 6))
                              for k, v in remaining.items()},
                "exhausted": self.exhausted()}


@dataclass
class TaskNode:
    """One node of the mission DAG."""

    task_id: str = field(default_factory=lambda: new_id("task"))
    objective: str = ""
    owner_agent: str = ""
    status: str = TaskStatus.PENDING.value
    depends_on: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: List[str] = field(default_factory=list)      # artifact ids
    attempt: int = 0
    max_attempts: int = 2
    budget: Budget = field(default_factory=Budget)
    completion_criteria: str = ""
    #: Which role should own this task. Without it the orchestrator would hand every task to
    #: whichever agent happens to be first — which is how a tester's job ends up with the backend.
    required_role: str = ""
    requires_review: bool = False
    reviewer_agent: str = ""
    error: str = ""
    started_ms: int = 0
    finished_ms: int = 0
    provider_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "objective": self.objective,
                "owner_agent": self.owner_agent, "status": self.status,
                "depends_on": list(self.depends_on), "inputs": self.inputs,
                "outputs": list(self.outputs), "attempt": self.attempt,
                "max_attempts": self.max_attempts, "budget": self.budget.to_dict(),
                "completion_criteria": self.completion_criteria,
                "required_role": self.required_role,
                "requires_review": self.requires_review,
                "reviewer_agent": self.reviewer_agent, "error": self.error,
                "provider_used": self.provider_used}


@dataclass
class Message:
    """A structured message between agents. Not a transcript excerpt."""

    from_agent: str = ""
    to_agent: str = ""
    type: str = MessageType.NOTE.value
    subject: str = ""
    body: str = ""
    artifact_id: str = ""
    task_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: new_id("msg"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"message_id": self.message_id, "from": self.from_agent,
                "to": self.to_agent, "type": self.type, "subject": self.subject,
                "body": self.body, "artifact_id": self.artifact_id, "task_id": self.task_id,
                "payload": self.payload, "ts": self.ts}


@dataclass
class BlackboardEntry:
    """Mission-relevant shared state. Deliberately not a place for private reasoning."""

    kind: str = "note"               # decision | interface | constraint | blocker | assumption | note
    key: str = ""
    value: Any = None
    author: str = ""
    mission_id: str = ""
    entry_id: str = field(default_factory=lambda: new_id("bb"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"entry_id": self.entry_id, "kind": self.kind, "key": self.key,
                "value": self.value, "author": self.author, "mission_id": self.mission_id,
                "ts": self.ts}


@dataclass
class Artifact:
    """A produced thing, referenced by id. Two agents needing the same file share this."""

    artifact_id: str = field(default_factory=lambda: new_id("art"))
    kind: str = ArtifactKind.FILE.value
    name: str = ""
    path: str = ""
    sha256: str = ""
    bytes: int = 0
    version: int = 1
    producer_agent: str = ""
    mission_id: str = ""
    task_id: str = ""
    dependencies: List[str] = field(default_factory=list)
    content: Any = None              # inline content for small artifacts
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"artifact_id": self.artifact_id, "kind": self.kind, "name": self.name,
                "path": self.path, "sha256": self.sha256, "bytes": self.bytes,
                "version": self.version, "producer_agent": self.producer_agent,
                "mission_id": self.mission_id, "task_id": self.task_id,
                "dependencies": list(self.dependencies),
                "has_content": self.content is not None, "created_ms": self.created_ms}


@dataclass
class ContinuationPacket:
    """What a *replacement* model needs to continue — provider-independent, compact (§10.13).

    Deliberately not a transcript dump. It carries the objective, the plan, what is done, what is
    pending, the decisions, the artifact references and the known errors.
    """

    objective: str = ""
    current_task: str = ""
    current_plan: List[str] = field(default_factory=list)
    completed_steps: List[str] = field(default_factory=list)
    pending_steps: List[str] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    relevant_context: List[str] = field(default_factory=list)
    known_errors: List[str] = field(default_factory=list)
    current_tool_state: Dict[str, Any] = field(default_factory=dict)
    mission_id: str = ""
    from_provider: str = ""
    reason: str = ""
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"objective": self.objective, "current_task": self.current_task,
                "current_plan": list(self.current_plan),
                "completed_steps": list(self.completed_steps),
                "pending_steps": list(self.pending_steps), "decisions": self.decisions,
                "artifacts": self.artifacts, "relevant_context": self.relevant_context,
                "known_errors": self.known_errors,
                "current_tool_state": self.current_tool_state,
                "mission_id": self.mission_id, "from_provider": self.from_provider,
                "reason": self.reason, "ts": self.ts}

    def render(self) -> str:
        """A compact text form for a prompt — bounded, so it cannot become a transcript."""
        lines = [f"OBJECTIVE: {self.objective}"]
        if self.current_task:
            lines.append(f"CURRENT TASK: {self.current_task}")
        if self.current_plan:
            lines.append("PLAN: " + " | ".join(self.current_plan[:12]))
        if self.completed_steps:
            lines.append("DONE: " + "; ".join(self.completed_steps[-12:]))
        if self.pending_steps:
            lines.append("PENDING: " + "; ".join(self.pending_steps[:12]))
        for decision in self.decisions[:8]:
            lines.append(f"DECISION: {decision.get('key')} = {decision.get('value')}")
        for artifact in self.artifacts[:8]:
            lines.append(f"ARTIFACT: {artifact.get('name')} ({artifact.get('artifact_id')})")
        if self.known_errors:
            lines.append("KNOWN ERRORS: " + "; ".join(self.known_errors[-6:]))
        if self.current_tool_state:
            lines.append(f"TOOL STATE: {self.current_tool_state}")
        return "\n".join(lines)


@dataclass
class TeamPlan:
    """The composition decision, with the reasoning that produced it."""

    mission_id: str = ""
    objective: str = ""
    team: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float = 0.0
    estimated_model_calls: int = 0
    rationale: str = ""
    single_agent_alternative: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"mission_id": self.mission_id, "objective": self.objective,
                "team": self.team, "tasks": self.tasks,
                "estimated_cost_usd": round(self.estimated_cost_usd, 6),
                "estimated_model_calls": self.estimated_model_calls,
                "rationale": self.rationale,
                "single_agent_alternative": self.single_agent_alternative,
                "created_ms": self.created_ms}
