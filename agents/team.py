"""Team orchestration (agents/team.py) — master spec §2.6, §10.3–§10.8, §10.14, §10.15.

    NEDLE2 → Mission Engine → Team Orchestrator → agents
                                    ↓
                    task DAG · mailbox · blackboard · artifacts · locks · budget

The single rule that shapes this module: **agents do not share one giant transcript.** They share a
DAG, structured messages, a blackboard of mission-relevant facts, and artifact *references*. An
agent's private reasoning never enters another agent's context.

The orchestrator owns:
  * the DAG (a task cannot start until its dependencies are complete)
  * assignment and scheduling of the ready set
  * failure handling (retry → new provider → replacement agent → replan)
  * the reviewer pattern, gated by a risk/complexity threshold
  * cancellation, pause/resume, and retirement of mission-scoped agents
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.contracts import AgentDefinition
from core.logging_setup import get_logger
from agents.artifacts import ArtifactService
from agents.budget import BudgetController
from agents.contracts import (ALLOWED_AGENT_TRANSITIONS, AgentKind, AgentLifecycle, ArtifactKind,
                              BlackboardEntry, Message, MessageType, TaskNode, TaskStatus,
                              TERMINAL_TASK_STATUSES, TeamPlan)

log = get_logger("agents.team")

#: How many agents a mission may have before the orchestrator refuses to grow the team further.
MAX_TEAM_SIZE = 6

#: A task at or above this complexity gets a reviewer (producer and verifier stay separable).
REVIEW_COMPLEXITY_THRESHOLD = 0.6


class CycleError(ValueError):
    """A task graph must be a DAG."""


# --------------------------------------------------------------------------- mailbox
class Mailbox:
    """Structured agent-to-agent messages. Nothing else is delivered."""

    def __init__(self, *, audit=None):
        self.audit = audit
        self._messages: List[Message] = []

    def send(self, message: Message) -> Message:
        self._messages.append(message)
        if self.audit:
            try:
                self.audit.record(who=message.from_agent or "system",
                                  action=f"agent.message:{message.type}",
                                  why=f"{message.from_agent}->{message.to_agent} "
                                      f"{message.subject}"[:180], result="ok")
            except Exception as exc:
                log.debug("mailbox audit failed: %s", exc)
        return message

    def inbox(self, agent_id: str, *, unread_only: bool = False) -> List[Message]:
        return [m for m in self._messages
                if m.to_agent in (agent_id, "*", "") and (not unread_only or not getattr(m, "_read", False))]

    def thread(self, task_id: str) -> List[Message]:
        return [m for m in self._messages if m.task_id == task_id]

    def all(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self._messages[-limit:]]

    def status(self) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        for message in self._messages:
            by_type[message.type] = by_type.get(message.type, 0) + 1
        return {"count": len(self._messages), "by_type": by_type}


# ------------------------------------------------------------------------ blackboard
class Blackboard:
    """Mission-relevant shared state. Not a place for private reasoning."""

    #: Only these kinds may be written — the list is what keeps the blackboard small.
    ALLOWED_KINDS = ("decision", "interface", "constraint", "blocker", "assumption", "note")

    def __init__(self, mission_id: str = "", *, db=None):
        self.mission_id = mission_id
        self.db = db
        self._entries: List[BlackboardEntry] = []

    def write(self, kind: str, key: str, value: Any, *, author: str = "") -> BlackboardEntry:
        if kind not in self.ALLOWED_KINDS:
            kind = "note"
        entry = BlackboardEntry(kind=kind, key=key, value=value, author=author,
                                mission_id=self.mission_id)
        self._entries.append(entry)
        if self.db is not None:
            import json as _json
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO agent_blackboard(entry_id, mission_id, kind, key,"
                    " value, author, ts) VALUES(?,?,?,?,?,?,?)",
                    (entry.entry_id, self.mission_id, kind, key,
                     _json.dumps(value, default=str), author, entry.ts))
            except Exception as exc:
                log.debug("blackboard persist failed: %s", exc)
        return entry

    def read(self, key: str) -> Optional[Any]:
        for entry in reversed(self._entries):
            if entry.key == key:
                return entry.value
        return None

    def by_kind(self, kind: str) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries if e.kind == kind]

    def entries(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def to_dict(self) -> Dict[str, Any]:
        """The compact form given to an agent: facts, not a transcript."""
        return {"mission_id": self.mission_id,
                "decisions": [{"key": e.key, "value": e.value}
                              for e in self._entries if e.kind == "decision"],
                "interfaces": [{"key": e.key, "value": e.value}
                               for e in self._entries if e.kind == "interface"],
                "constraints": [e.value for e in self._entries if e.kind == "constraint"],
                "blockers": [e.value for e in self._entries if e.kind == "blocker"],
                "assumptions": [e.value for e in self._entries if e.kind == "assumption"]}

    def status(self) -> Dict[str, Any]:
        by_kind: Dict[str, int] = {}
        for entry in self._entries:
            by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
        return {"count": len(self._entries), "by_kind": by_kind}


# ------------------------------------------------------------------------------ DAG
class TaskGraph:
    """A mission's tasks and their dependencies."""

    def __init__(self):
        self._tasks: Dict[str, TaskNode] = {}

    def add(self, node: TaskNode) -> TaskNode:
        for dependency in node.depends_on:
            if dependency not in self._tasks:
                raise KeyError(f"task {node.task_id} depends on unknown task {dependency}")
        self._tasks[node.task_id] = node
        if self._has_cycle():
            del self._tasks[node.task_id]
            raise CycleError(f"adding {node.task_id} would create a cycle")
        return node

    def _has_cycle(self) -> bool:
        visiting: set = set()
        visited: set = set()

        def walk(task_id: str) -> bool:
            if task_id in visiting:
                return True
            if task_id in visited:
                return False
            visiting.add(task_id)
            for dependency in self._tasks[task_id].depends_on:
                if walk(dependency):
                    return True
            visiting.discard(task_id)
            visited.add(task_id)
            return False

        return any(walk(task_id) for task_id in list(self._tasks))

    def get(self, task_id: str) -> Optional[TaskNode]:
        return self._tasks.get(task_id)

    def all(self) -> List[TaskNode]:
        return list(self._tasks.values())

    def ready(self) -> List[TaskNode]:
        """Pending tasks whose dependencies are all DONE."""
        out: List[TaskNode] = []
        for node in self._tasks.values():
            if node.status != TaskStatus.PENDING.value:
                continue
            if all(self._tasks[d].status == TaskStatus.DONE.value for d in node.depends_on):
                out.append(node)
        return out

    def blocked(self) -> List[TaskNode]:
        """Pending tasks with a failed/cancelled dependency — they can never run as-is."""
        out: List[TaskNode] = []
        for node in self._tasks.values():
            if node.status != TaskStatus.PENDING.value:
                continue
            if any(self._tasks[d].status in (TaskStatus.FAILED.value,
                                             TaskStatus.CANCELLED.value)
                   for d in node.depends_on):
                out.append(node)
        return out

    def dependents(self, task_id: str) -> List[TaskNode]:
        return [t for t in self._tasks.values() if task_id in t.depends_on]

    def unfinished(self) -> List[TaskNode]:
        return [t for t in self._tasks.values() if t.status not in TERMINAL_TASK_STATUSES]

    def to_dict(self) -> Dict[str, Any]:
        return {"tasks": [t.to_dict() for t in self._tasks.values()],
                "ready": [t.task_id for t in self.ready()],
                "blocked": [t.task_id for t in self.blocked()],
                "complete": not self.unfinished()}


# ------------------------------------------------------------------------ orchestrator
@dataclass
class TeamMember:
    definition: AgentDefinition
    lifecycle: str = AgentLifecycle.CREATED.value
    tasks_completed: int = 0
    tasks_failed: int = 0
    provider_used: str = ""

    def can_transition(self, target: str) -> bool:
        return target in ALLOWED_AGENT_TRANSITIONS.get(self.lifecycle, set())

    def move(self, target: str) -> bool:
        if target == self.lifecycle:
            return True
        if not self.can_transition(target):
            return False
        self.lifecycle = target
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {**self.definition.to_dict(), "lifecycle": self.lifecycle,
                "tasks_completed": self.tasks_completed, "tasks_failed": self.tasks_failed,
                "provider_used": self.provider_used}


Runner = Callable[[AgentDefinition, TaskNode, Dict[str, Any]], Dict[str, Any]]


class TeamOrchestrator:
    """Runs a mission as a team of agents over a shared DAG."""

    def __init__(self, *, mission_id: str = "", objective: str = "", db=None, audit=None,
                 artifacts: Optional[ArtifactService] = None,
                 budgets: Optional[BudgetController] = None,
                 locks=None, experience=None, runner: Optional[Runner] = None,
                 cancel_event: Optional[threading.Event] = None):
        self.mission_id = mission_id
        self.objective = objective
        self.db = db
        self.audit = audit
        self.artifacts = artifacts or ArtifactService(db, audit=audit)
        self.budgets = budgets or BudgetController(db)
        self.locks = locks
        self.experience = experience
        self.runner = runner
        self.mailbox = Mailbox(audit=audit)
        self.blackboard = Blackboard(mission_id, db=db)
        self.graph = TaskGraph()
        self.members: Dict[str, TeamMember] = {}
        self.plan: Optional[TeamPlan] = None
        self.cancel_event = cancel_event or threading.Event()
        self.paused = False
        self.pause_reason = ""
        #: Operator control surface (§11) — cap on simultaneously-WORKING agents. Defaults to
        #: the team-size ceiling; the operator can lower it live to throttle a hot mission.
        self.max_concurrency = MAX_TEAM_SIZE
        self.history: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []
        self.trace: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------- team
    def add_member(self, definition: AgentDefinition, *,
                   lifecycle: str = AgentLifecycle.READY.value) -> Dict[str, Any]:
        """Add an agent. Refuses beyond the team-size ceiling (D-094)."""
        if len(self.members) >= MAX_TEAM_SIZE:
            return {"ok": False, "error": f"team is at its ceiling of {MAX_TEAM_SIZE} agents",
                    "detail": "extra agents must justify themselves; see the cost estimate"}
        member = TeamMember(definition=definition, lifecycle=lifecycle)
        self.members[definition.agent_id] = member
        self.budgets.child(f"agent:{definition.agent_id}", "agent",
                           _budget_from_dict(definition.budget),
                           parent=f"mission:{self.mission_id}"
                           if self.budgets.node(f"mission:{self.mission_id}") else "global")
        return {"ok": True, "agent_id": definition.agent_id,
                "team_size": len(self.members)}

    def member(self, agent_id: str) -> Optional[TeamMember]:
        return self.members.get(agent_id)

    def all_members(self) -> List[str]:
        """Agent ids in insertion order — handy for wiring a plan to tasks."""
        return list(self.members.keys())

    def idle_members(self) -> List[TeamMember]:
        return [m for m in self.members.values()
                if m.lifecycle in (AgentLifecycle.READY.value, AgentLifecycle.COMPLETED.value)]

    def working_members(self) -> List[TeamMember]:
        return [m for m in self.members.values() if m.lifecycle == AgentLifecycle.WORKING.value]

    # -------------------------------------------------------------------- tasks
    def add_task(self, node: TaskNode) -> Dict[str, Any]:
        try:
            self.graph.add(node)
        except (KeyError, CycleError) as exc:
            return {"ok": False, "error": str(exc)}
        if node.budget.tokens or node.budget.cost_usd:
            self.budgets.child(f"task:{node.task_id}", "task", node.budget,
                               parent=f"agent:{node.owner_agent}"
                               if node.owner_agent and
                               self.budgets.node(f"agent:{node.owner_agent}") else
                               f"mission:{self.mission_id}"
                               if self.budgets.node(f"mission:{self.mission_id}") else "global")
        return {"ok": True, "task_id": node.task_id}

    def assign(self, task_id: str, agent_id: str) -> Dict[str, Any]:
        node = self.graph.get(task_id)
        if node is None:
            return {"ok": False, "error": f"unknown task {task_id}"}
        if agent_id not in self.members:
            return {"ok": False, "error": f"unknown agent {agent_id}"}
        node.owner_agent = agent_id
        return {"ok": True, "task_id": task_id, "owner_agent": agent_id}

    # ---------------------------------------------------------------------- run
    def run(self, *, max_steps: int = 200) -> Dict[str, Any]:
        """Execute until the DAG is complete, blocked, cancelled or paused."""
        if self.runner is None:
            return {"ok": False, "error": "no runner configured"}
        steps = 0
        while steps < max_steps:
            steps += 1
            if self.cancel_event.is_set():
                self._cancel_remaining()
                return self._result("cancelled", "cancelled by the owner")
            if self.paused:
                return self._result("paused", self.pause_reason or "paused")
            # Operator control surface (§11): throttle — refuse to start new work once the
            # live concurrency cap is saturated. A synchronous runner pins one task at a time,
            # so the practical effect is that max_concurrency must be >= 1 for anything to run.
            if len(self.working_members()) >= self.max_concurrency:
                return self._result("throttled",
                                    f"concurrency cap {self.max_concurrency} reached")

            ready = self.graph.ready()
            if not ready:
                blocked = self.graph.blocked()
                if blocked:
                    for node in blocked:
                        node.status = TaskStatus.BLOCKED.value
                        node.error = "a dependency failed"
                    return self._result("blocked",
                                        f"{len(blocked)} task(s) blocked by a failed dependency")
                if not self.graph.unfinished():
                    return self._result("completed", "all tasks done")
                return self._result("stalled", "no runnable task and no blocked dependency")

            for node in ready:
                outcome = self._run_task(node)
                if outcome.get("status") == "paused":
                    return self._result("paused", self.pause_reason)
                if outcome.get("status") == "cancelled":
                    self._cancel_remaining()
                    return self._result("cancelled", "cancelled by the owner")
                if outcome.get("status") == "failed":
                    # mark the dependents now: the caller must see what is now unreachable,
                    # not discover it later
                    for dependent in self.graph.blocked():
                        dependent.status = TaskStatus.BLOCKED.value
                        dependent.error = f"dependency {node.task_id} failed"
                    return self._result("failed",
                                        f"task {node.task_id} failed: {node.error}")
        return self._result("exhausted", f"stopped after {max_steps} scheduling steps")

    def _run_task(self, node: TaskNode) -> Dict[str, Any]:
        agent_id = node.owner_agent or self._pick_agent(node)
        if not agent_id:
            node.status = TaskStatus.BLOCKED.value
            node.error = "no agent available for this task"
            return {"status": "failed"}
        member = self.members[agent_id]
        node.owner_agent = agent_id
        node.attempt += 1
        node.status = TaskStatus.RUNNING.value
        node.started_ms = int(time.time() * 1000)
        member.move(AgentLifecycle.WORKING.value)

        context = self._task_context(node, member)
        outcome = self._invoke(member, node, context)

        if outcome.get("paused"):
            node.status = TaskStatus.PENDING.value
            member.move(AgentLifecycle.WAITING.value)
            return {"status": "paused"}
        if outcome.get("cancelled"):
            node.status = TaskStatus.CANCELLED.value
            member.move(AgentLifecycle.WAITING.value)
            return {"status": "cancelled"}

        if outcome.get("ok"):
            node.status = TaskStatus.DONE.value
            node.finished_ms = int(time.time() * 1000)
            node.provider_used = str(outcome.get("provider", ""))
            member.tasks_completed += 1
            member.provider_used = node.provider_used or member.provider_used
            member.move(AgentLifecycle.COMPLETED.value)
            for artifact in outcome.get("artifacts") or []:
                node.outputs.append(str(artifact))
            self._review_if_needed(node, member, outcome)
            self._record_experience(node, member, outcome, success=True)
            self._note(node, f"done by {agent_id}")
            return {"status": "done"}

        return self._handle_failure(node, member, outcome)

    def _invoke(self, member: TeamMember, node: TaskNode,
                context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            outcome = self.runner(member.definition, node, context) or {}
        except Exception as exc:
            outcome = {"ok": False, "error": str(exc), "error_code": "runner_exception"}
        if self.audit:
            try:
                self.audit.record(who=member.definition.agent_id,
                                  action=f"agent.task:{node.task_id}",
                                  why=node.objective[:150],
                                  mission_id=self.mission_id,
                                  result="ok" if outcome.get("ok") else "failed")
            except Exception as exc:
                log.debug("task audit failed: %s", exc)
        return outcome

    # ------------------------------------------------------------ failure handling
    def _handle_failure(self, node: TaskNode, member: TeamMember,
                        outcome: Dict[str, Any]) -> Dict[str, Any]:
        """classify → retry → new provider → replacement agent → replan (§10.14)."""
        error = str(outcome.get("error") or outcome.get("detail") or "task failed")
        code = str(outcome.get("error_code") or "unknown")
        member.tasks_failed += 1
        self.failures.append({"task_id": node.task_id, "agent_id": member.definition.agent_id,
                              "error": error[:300], "error_code": code, "attempt": node.attempt})
        node.error = error
        self._note(node, f"failed attempt {node.attempt}: {error}")

        # 1. retry the same agent — transient failures are common and cheap to retry
        if node.attempt < node.max_attempts and code in ("timeout", "transient", "unknown", ""):
            node.status = TaskStatus.PENDING.value
            member.move(AgentLifecycle.READY.value)
            self._note(node, "retrying the same agent")
            return {"status": "retry"}

        # 2. a provider problem is not an agent problem: ask for a different provider
        if code in ("provider_unavailable", "model_unavailable", "budget_exhausted"):
            replacement = self._replacement_for(member)
            if replacement is not None:
                node.status = TaskStatus.PENDING.value
                node.owner_agent = replacement.definition.agent_id
                member.move(AgentLifecycle.RETIRED.value)
                self._note(node, f"reassigned to {replacement.definition.agent_id} "
                                 f"after a provider problem")
                return {"status": "retry"}

        # 3. give up on this task, but do not fail the whole mission on one worker
        node.status = TaskStatus.FAILED.value
        node.finished_ms = int(time.time() * 1000)
        member.move(AgentLifecycle.RETIRED.value)
        self._record_experience(node, member, outcome, success=False)
        return {"status": "failed"}

    def _replacement_for(self, member: TeamMember) -> Optional[TeamMember]:
        """Find an idle member with the same role, else spawn a replacement."""
        for candidate in self.members.values():
            if candidate is member:
                continue
            if candidate.definition.role == member.definition.role \
                    and candidate.lifecycle in (AgentLifecycle.READY.value,
                                                AgentLifecycle.COMPLETED.value):
                return candidate
        if len(self.members) >= MAX_TEAM_SIZE:
            return None
        clone = AgentDefinition(**{**member.definition.__dict__})
        clone.agent_id = f"{member.definition.agent_id}_r{len(self.members)}"
        clone.role = member.definition.role
        self.add_member(clone)
        return self.members.get(clone.agent_id)

    # ------------------------------------------------------------------ reviewer
    def _review_if_needed(self, node: TaskNode, member: TeamMember,
                          outcome: Dict[str, Any]) -> None:
        """Producer and verifier stay separable — but only above a threshold (§10.15)."""
        if not node.requires_review:
            return
        complexity = float(node.inputs.get("complexity", 0.0) or 0.0)
        if complexity < REVIEW_COMPLEXITY_THRESHOLD and not node.inputs.get("force_review"):
            self._note(node, f"review skipped (complexity {complexity:.2f} below threshold)")
            return
        reviewer = self._reviewer_for(member)
        if reviewer is None:
            self._note(node, "no reviewer available; review skipped and recorded")
            return
        review_task = TaskNode(objective=f"review: {node.objective}",
                               owner_agent=reviewer.definition.agent_id,
                               inputs={"review_of": node.task_id,
                                       "artifacts": list(node.outputs)},
                               completion_criteria="accept or reject with a reason",
                               depends_on=[node.task_id])
        self.graph.add(review_task)
        review_task.status = TaskStatus.RUNNING.value
        reviewer.move(AgentLifecycle.WORKING.value)
        context = self._task_context(review_task, reviewer)
        context["artifacts_to_review"] = list(node.outputs)
        verdict = self._invoke(reviewer, review_task, context)
        accepted = bool(verdict.get("ok")) and str(verdict.get("verdict", "accept")) != "reject"
        review_task.status = TaskStatus.DONE.value if accepted else TaskStatus.FAILED.value
        review_task.error = "" if accepted else str(verdict.get("reason", "rejected"))
        reviewer.move(AgentLifecycle.COMPLETED.value)
        self.mailbox.send(Message(from_agent=reviewer.definition.agent_id,
                                  to_agent=member.definition.agent_id,
                                  type=MessageType.REVIEW_RESULT.value,
                                  subject=f"review of {node.task_id}",
                                  body=str(verdict.get("reason", "")),
                                  task_id=node.task_id,
                                  payload={"accepted": accepted,
                                           "verdict": verdict.get("verdict", "accept")}))
        node.inputs["review"] = {"accepted": accepted,
                                 "reviewer": reviewer.definition.agent_id,
                                 "reason": verdict.get("reason", "")}
        if not accepted:
            node.status = TaskStatus.PENDING.value
            node.attempt = max(0, node.attempt - 1)      # the fix attempt is not a fresh failure
            self._note(node, f"reviewer rejected: {verdict.get('reason', '')}")
        else:
            self._note(node, "reviewer accepted")

    def _reviewer_for(self, producer: TeamMember) -> Optional[TeamMember]:
        for candidate in self.members.values():
            if candidate is producer:
                continue
            if candidate.definition.role in ("reviewer", "tester") and \
                    candidate.lifecycle in (AgentLifecycle.READY.value,
                                            AgentLifecycle.COMPLETED.value):
                return candidate
        if len(self.members) >= MAX_TEAM_SIZE:
            return None
        reviewer = AgentDefinition(name="reviewer", role="reviewer",
                                   purpose="verify a produced artifact",
                                   model_capability="reasoning", kind=AgentKind.MISSION.value)
        self.add_member(reviewer)
        return self.members.get(reviewer.agent_id)

    # -------------------------------------------------------------------- context
    def _task_context(self, node: TaskNode, member: TeamMember) -> Dict[str, Any]:
        """The compact packet an agent gets: its task, the shared facts, artifact references.

        Note what is absent: other agents' transcripts.
        """
        return {"mission_id": self.mission_id,
                "objective": self.objective,
                "task": node.to_dict(),
                "role": member.definition.role,
                "purpose": member.definition.purpose,
                "tools": list(member.definition.tools),
                "skills": list(member.definition.skills),
                "blackboard": self.blackboard.to_dict(),
                "inputs": {k: v for k, v in node.inputs.items() if k != "artifacts"},
                "artifact_refs": [
                    {"artifact_id": a, "name": (self.artifacts.get(a).name
                                                if self.artifacts.get(a) else "")}
                    for a in self._dependency_artifacts(node)],
                "budget_remaining": (self.budgets.node(f"agent:{member.definition.agent_id}")
                                     or self.budgets.global_node).to_dict()["remaining"],
                "mailbox": [m.to_dict() for m in self.mailbox.inbox(member.definition.agent_id)][-10:]}

    def _dependency_artifacts(self, node: TaskNode) -> List[str]:
        out: List[str] = []
        for dependency in node.depends_on:
            parent = self.graph.get(dependency)
            if parent is not None:
                out.extend(parent.outputs)
        return out

    def _pick_agent(self, node: TaskNode) -> str:
        """Prefer an agent whose role matches the task; otherwise take any idle agent.

        Role matters: handing a test task to the backend agent produces work nobody asked for and
        leaves the tester idle — which is exactly the "duplicate work" failure the spec warns about.
        """
        idle = [m for m in self.members.values()
                if m.lifecycle in (AgentLifecycle.READY.value, AgentLifecycle.COMPLETED.value)]
        wanted = node.required_role or str(node.inputs.get("role", ""))
        if wanted:
            for member in idle:
                if member.definition.role == wanted:
                    return member.definition.agent_id
        return idle[0].definition.agent_id if idle else ""

    def _note(self, node: TaskNode, text: str) -> None:
        self.trace.append({"task_id": node.task_id, "text": text,
                           "ts": int(time.time() * 1000)})

    def _record_experience(self, node: TaskNode, member: TeamMember, outcome: Dict[str, Any],
                           *, success: bool) -> None:
        """Structured experience only — never a raw conversation (§10.16)."""
        if self.experience is None:
            return
        try:
            self.experience.record(
                task_type=str(node.inputs.get("task_type", "general")),
                role=member.definition.role,
                strategy=str(node.inputs.get("strategy", "direct")),
                provider=str(outcome.get("provider", "")),
                tools=list(member.definition.tools),
                success=success,
                failures=[] if success else [str(outcome.get("error", ""))[:200]],
                latency_ms=max(0, node.finished_ms - node.started_ms) if node.finished_ms else 0,
                cost_usd=float(outcome.get("cost_usd", 0.0) or 0.0),
                mission_id=self.mission_id)
        except Exception as exc:
            log.debug("experience recording failed: %s", exc)

    # --------------------------------------------------------- control & lifecycle
    def pause(self, reason: str = "owner requested") -> Dict[str, Any]:
        self.paused = True
        self.pause_reason = reason
        return {"ok": True, "paused": True, "reason": reason}

    def resume(self) -> Dict[str, Any]:
        """Resume without rebuilding the team (§10.21)."""
        self.paused = False
        self.pause_reason = ""
        for member in self.members.values():
            if member.lifecycle in (AgentLifecycle.WAITING.value, AgentLifecycle.BLOCKED.value):
                member.move(AgentLifecycle.READY.value)
        return {"ok": True, "resumed": True, "team_size": len(self.members),
                "note": "existing agents reused; nothing was rebuilt"}

    def cancel(self, reason: str = "owner requested") -> Dict[str, Any]:
        self.cancel_event.set()
        self._cancel_remaining()
        return {"ok": True, "cancelled": True, "reason": reason,
                "retired": self._retire_disposable()}

    def _cancel_remaining(self) -> None:
        for node in self.graph.unfinished():
            node.status = TaskStatus.CANCELLED.value
            node.error = node.error or "mission cancelled"
        for member in self.members.values():
            if member.lifecycle == AgentLifecycle.WORKING.value:
                member.move(AgentLifecycle.WAITING.value)

    def _retire_disposable(self) -> List[str]:
        """Mission-scoped agents are disposable; persistent ones are retained (§10.2)."""
        retired: List[str] = []
        for member in self.members.values():
            if member.definition.kind in (AgentKind.MISSION.value, AgentKind.CANDIDATE.value) \
                    or member.definition.ephemeral:
                member.move(AgentLifecycle.RETIRED.value)
                retired.append(member.definition.agent_id)
                self.budgets.remove(f"agent:{member.definition.agent_id}")
        return retired

    def cleanup(self) -> Dict[str, Any]:
        """Called at the end of a mission so generated agents do not accumulate."""
        retired = self._retire_disposable()
        return {"ok": True, "retired": retired, "remaining": sorted(self.members.keys())}

    def retire_orphans(self) -> Dict[str, Any]:
        """Operator control surface (§11): retire only agents stuck in the WORKING lifecycle
        (orphans left behind by a crash or a kill) without tearing down the rest of the team.
        Distinct from cleanup(), which retires every mission-scoped agent."""
        retired: List[str] = []
        for member in list(self.members.values()):
            if member.lifecycle == AgentLifecycle.WORKING.value:
                member.move(AgentLifecycle.RETIRED.value)
                retired.append(member.definition.agent_id)
                self.budgets.remove(f"agent:{member.definition.agent_id}")
        return {"ok": True, "retired_orphans": retired,
                "remaining": sorted(self.members.keys())}

    # -------------------------------------------------------------------- report
    def _result(self, status: str, detail: str) -> Dict[str, Any]:
        payload = {"ok": status == "completed", "status": status, "detail": detail,
                   "mission_id": self.mission_id, "team_size": len(self.members),
                   "tasks": self.graph.to_dict(),
                   "mailbox": self.mailbox.status(), "blackboard": self.blackboard.status(),
                   "artifacts": self.artifacts.for_mission(self.mission_id),
                   "failures": self.failures, "trace": self.trace[-50:],
                   "cost": self.budgets.status()["nodes"].get(
                       f"mission:{self.mission_id}", {}).get("budget", {}),
                   "orphans": [m.definition.agent_id for m in self.members.values()
                               if m.lifecycle == AgentLifecycle.WORKING.value]}
        self.history.append({"status": status, "detail": detail,
                             "ts": int(time.time() * 1000)})
        return payload

    def status(self) -> Dict[str, Any]:
        return {"mission_id": self.mission_id, "objective": self.objective,
                "paused": self.paused, "cancelled": self.cancel_event.is_set(),
                "team": [m.to_dict() for m in self.members.values()],
                "tasks": self.graph.to_dict(),
                "mailbox": self.mailbox.status(), "blackboard": self.blackboard.status(),
                "artifacts": len(self.artifacts.for_mission(self.mission_id)),
                "failures": len(self.failures),
                "orphans": [m.definition.agent_id for m in self.members.values()
                            if m.lifecycle == AgentLifecycle.WORKING.value],
                "history": self.history[-10:]}


def _budget_from_dict(raw: Dict[str, Any]):
    from agents.contracts import Budget
    return Budget(tokens=int(raw.get("tokens", 0) or 0),
                  cost_usd=float(raw.get("cost_usd", 0.0) or 0.0),
                  model_calls=int(raw.get("model_calls", 0) or 0),
                  tool_calls=int(raw.get("tool_calls", 0) or 0),
                  wall_ms=int(raw.get("wall_ms", 0) or 0))
