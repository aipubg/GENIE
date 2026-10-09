"""Agent service (agents/service.py) — the daemon facade for Phase 10.

Owns the factory, the budgets, the artifact store, the experience store and the live
orchestrators, and provides the two things the golden tasks depend on:

* **Continuation** (§10.13) — a provider-independent packet describing where a mission is, so a
  replacement model can carry on without replaying the mission from zero.
* **Provider failover** (§10.12, golden #7) — snapshot → pick another provider → continue, with the
  completed work preserved and *not repeated*.

The failover path deliberately reuses the existing model gateway: there is no second provider
stack. What this service adds is the *state* that has to survive the switch.
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
from agents.contracts import (AgentKind, AgentLifecycle, ContinuationPacket, TaskNode,
                              TaskStatus, TeamPlan)
from agents.factory import AgentFactory, ExperienceStore
from agents.ops import OperatorConsole
from agents.team import Runner, TeamOrchestrator, TaskGraph
from agents.competition import CompetitionEngine

log = get_logger("agents.service")


@dataclass
class ActiveTeam:
    orchestrator: TeamOrchestrator
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    provider: str = ""
    failovers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"mission_id": self.orchestrator.mission_id,
                "provider": self.provider, "failovers": self.failovers,
                "status": self.orchestrator.status()}


class AgentService:
    def __init__(self, db=None, *, audit=None, locks=None, gateway=None, runner: Runner = None,
                 global_budget: Optional[Dict[str, Any]] = None,
                 artifact_root: Optional[str] = None, verifier=None,
                 competition: Optional[Any] = None):
        self.db = db
        self.audit = audit
        self.locks = locks
        self.gateway = gateway
        self.runner = runner
        self.experience = ExperienceStore(db)
        self.factory = AgentFactory(db=db, experience=self.experience, audit=audit)
        self.artifacts = ArtifactService(db, audit=audit, root=artifact_root)
        self.budgets = BudgetController(db)
        if global_budget:
            from agents.contracts import Budget
            self.budgets.global_node.budget = Budget(**global_budget)
        self._teams: Dict[str, ActiveTeam] = {}
        self._failovers: List[Dict[str, Any]] = []
        # Competition (roadmap §16-§18): several agents attempt one task, an
        # independent verifier judges, the best survives. No verifier means no
        # winner can be declared — the engine says so instead of guessing.
        self.competition = competition or CompetitionEngine(
            runner=self.runner, verifier=verifier, experience=self.experience,
            audit=self.audit)
        # Phase 11 — operator observability & control surface (§11). One console, one code
        # path, shared by IPC, CLI and tests.
        self.ops = OperatorConsole(self)

    # -------------------------------------------------------------------- teams
    def start_team(self, *, mission_id: str, objective: str, plan: TeamPlan,
                   runner: Optional[Runner] = None, complexity: float = 0.5,
                   budget: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create the team from a plan and register it as an active mission."""
        if mission_id in self._teams:
            return {"ok": False, "error": f"mission {mission_id} already has a team"}
        # Validate the whole plan before creating workers/budgets. Plans may list
        # dependent tasks before their prerequisites; execution still uses a DAG.
        graph = TaskGraph()
        try:
            fields = {"task_id", "objective", "depends_on", "inputs",
                      "completion_criteria", "required_role", "requires_review"}
            pending = [TaskNode(**{k: v for k, v in spec.items() if k in fields})
                       for spec in plan.tasks]
            ids = [node.task_id for node in pending]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate task IDs in plan")
            while pending:
                ready = [node for node in pending
                         if all(graph.get(dep) is not None for dep in node.depends_on)]
                if not ready:
                    raise ValueError("plan has a cycle or missing task dependency")
                for node in ready:
                    graph.add(node)
                    pending.remove(node)
        except (TypeError, AttributeError, ValueError) as exc:
            return {"ok": False, "error": f"invalid task plan: {exc}"}
        mission_budget = budget or {"tokens": 200_000, "cost_usd": 1.0, "model_calls": 60}
        from agents.contracts import Budget
        self.budgets.child(f"mission:{mission_id}", "mission", Budget(**mission_budget))
        orchestrator = TeamOrchestrator(
            mission_id=mission_id, objective=objective, db=self.db, audit=self.audit,
            artifacts=self.artifacts, budgets=self.budgets, locks=self.locks,
            experience=self.experience, runner=runner or self.runner)
        orchestrator.plan = plan
        added: List[str] = []
        for spec in plan.team:
            result = self.factory.create(
                role=str(spec.get("role", "worker")),
                capability=str(spec.get("capability", "")),
                quality=str(spec.get("quality", "")),
                budget={"tokens": int(mission_budget["tokens"] / max(1, len(plan.team))),
                        "cost_usd": float(mission_budget["cost_usd"]) / max(1, len(plan.team)),
                        "model_calls": int(mission_budget["model_calls"] / max(1, len(plan.team)))},
                kind=AgentKind.MISSION.value, allow_reuse=False)
            if result.definition is not None:
                orchestrator.add_member(result.definition)
                added.append(result.definition.agent_id)
        for node in graph.all():
            orchestrator.add_task(node)
        self._teams[mission_id] = ActiveTeam(orchestrator=orchestrator)
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.start_team",
                                  why=f"{objective[:120]}", mission_id=mission_id,
                                  result=f"{len(added)} agents")
            except Exception as exc:
                log.debug("team audit failed: %s", exc)
        return {"ok": True, "mission_id": mission_id, "agents": added,
                "plan": plan.to_dict()}

    def team(self, mission_id: str) -> Optional[TeamOrchestrator]:
        active = self._teams.get(mission_id)
        return active.orchestrator if active else None

    # ------------------------------------------------- competition §16-§18
    def compete_task(self, mission_id: str, task_id: str, *, commit=None,
                     requested: Optional[int] = None,
                     cancel_event=None) -> Dict[str, Any]:
        """Run the team's agents against ONE task and keep the verified best.

        The commit callable is invoked at most once, for the winner only, so
        side effects cannot happen twice just because two agents attempted the
        same work.
        """
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        node = orchestrator.graph.get(task_id)
        if node is None:
            return {"ok": False, "error": f"no task {task_id} in mission {mission_id}"}
        candidates = [m.definition for m in orchestrator.members.values()
                      if getattr(m, "definition", None) is not None]
        result = self.competition.compete_and_commit(
            node, candidates, commit=commit, criteria=node.completion_criteria,
            requested=requested,
            cancel_event=cancel_event or orchestrator.cancel_event)
        return {"ok": result.has_winner, "outcome": result.outcome,
                "competition": result.to_dict()}

    def run(self, mission_id: str, **kwargs) -> Dict[str, Any]:
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        result = orchestrator.run(**kwargs)
        if result.get("status") in ("completed", "failed", "blocked", "cancelled"):
            result["cleanup"] = orchestrator.cleanup()
        return result

    # ------------------------------------------------------------------ control
    def pause(self, mission_id: str, reason: str = "owner requested") -> Dict[str, Any]:
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        return orchestrator.pause(reason)

    def resume(self, mission_id: str, **kwargs) -> Dict[str, Any]:
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        outcome = orchestrator.resume()
        outcome["run"] = orchestrator.run(**kwargs)
        return outcome

    def cancel(self, mission_id: str, reason: str = "owner requested") -> Dict[str, Any]:
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        outcome = orchestrator.cancel(reason)
        if self.locks is not None:
            # crash or cancel must never leave a lock behind
            released = self.locks.release_all(holder=mission_id)
            outcome["locks_released"] = released
        outcome["cleanup"] = orchestrator.cleanup()
        return outcome

    # ------------------------------------------------------- continuation §10.13
    def continuation_packet(self, mission_id: str, *, from_provider: str = "",
                            reason: str = "") -> ContinuationPacket:
        """Build the compact, provider-independent packet a replacement model receives."""
        orchestrator = self.team(mission_id)
        if orchestrator is None:
            return ContinuationPacket(objective="", mission_id=mission_id, reason=reason)
        tasks = orchestrator.graph.all()
        done = [t for t in tasks if t.status == TaskStatus.DONE.value]
        pending = [t for t in tasks if t.status not in (TaskStatus.DONE.value,
                                                        TaskStatus.CANCELLED.value)]
        current = next((t for t in tasks if t.status == TaskStatus.RUNNING.value), None) \
            or next((t for t in tasks if t.status == TaskStatus.PENDING.value), None)
        return ContinuationPacket(
            objective=orchestrator.objective,
            current_task=current.objective if current else "",
            current_plan=[t.objective for t in tasks][:20],
            completed_steps=[f"{t.task_id}: {t.objective}" for t in done],
            pending_steps=[f"{t.task_id}: {t.objective}" for t in pending],
            decisions=orchestrator.blackboard.by_kind("decision"),
            artifacts=orchestrator.artifacts.for_mission(mission_id),
            relevant_context=[e.get("value") for e in orchestrator.blackboard.by_kind("interface")
                              if isinstance(e.get("value"), str)][:10],
            known_errors=[f"{t.task_id}: {t.error}" for t in tasks if t.error][:10],
            current_tool_state={"artifacts": len(orchestrator.artifacts.for_mission(mission_id)),
                                "team_size": len(orchestrator.members)},
            mission_id=mission_id, from_provider=from_provider, reason=reason)

    # --------------------------------------------------------- failover §10.12
    def failover(self, mission_id: str, *, from_provider: str, reason: str,
                 to_provider: str = "") -> Dict[str, Any]:
        """Provider A dies mid-mission: snapshot, switch, continue. Never restart from zero."""
        active = self._teams.get(mission_id)
        if active is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        packet = self.continuation_packet(mission_id, from_provider=from_provider,
                                          reason=reason)
        # the circuit breaker for the failing provider is the gateway's job
        breaker: Dict[str, Any] = {}
        if self.gateway is not None:
            try:
                recorder = getattr(self.gateway, "record_failure", None) \
                    or getattr(getattr(self.gateway, "health", None), "record_failure", None)
                if recorder is not None:
                    recorder(from_provider, reason)
                    breaker = {"recorded": True, "provider": from_provider}
            except Exception as exc:
                log.debug("circuit breaker update failed: %s", exc)
        chosen = to_provider or self._next_provider(exclude=[from_provider])
        active.provider = chosen
        active.failovers += 1
        record = {"mission_id": mission_id, "from_provider": from_provider,
                  "to_provider": chosen, "reason": reason,
                  "completed_preserved": len(packet.completed_steps),
                  "packet": packet.to_dict(), "ts": int(time.time() * 1000)}
        self._failovers.append(record)
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.provider_failover",
                                  why=f"{from_provider} -> {chosen}: {reason}"[:180],
                                  mission_id=mission_id, result="continued")
            except Exception as exc:
                log.debug("failover audit failed: %s", exc)
        return {"ok": True, "from_provider": from_provider, "to_provider": chosen,
                "continuation": packet.to_dict(), "continuation_text": packet.render(),
                "breaker": breaker,
                "completed_preserved": len(packet.completed_steps),
                "pending": len(packet.pending_steps),
                "note": "the mission continues from this packet; completed work is not repeated"}

    def _next_provider(self, *, exclude: List[str]) -> str:
        """Ask the gateway for another usable provider; fall back to a local label."""
        if self.gateway is not None:
            try:
                summary = getattr(self.gateway, "available_providers", None)
                candidates = summary() if callable(summary) else None
                if candidates:
                    for candidate in candidates:
                        if candidate not in exclude:
                            return str(candidate)
            except Exception as exc:
                log.debug("provider lookup failed: %s", exc)
        return "local-fallback"

    def failovers(self) -> List[Dict[str, Any]]:
        return list(self._failovers)

    # ------------------------------------------------------------------ report
    def status(self) -> Dict[str, Any]:
        return {"factory": self.factory.status(),
                "budgets": self.budgets.status(),
                "artifacts": self.artifacts.status(),
                "teams": [a.to_dict() for a in self._teams.values()],
                "failovers": len(self._failovers),
                "experience": self.experience.statistics()}

    # ----------------------------------------------- operator console (Phase 11 §11)
    def ops_dashboard(self, *, limit_audit: int = 25) -> Dict[str, Any]:
        """Live operator view of the whole fleet."""
        return self.ops.dashboard(limit_audit=limit_audit)

    def ops_control(self, action: str, *, mission_id: str = "", actor: str = "operator",
                    **params: Any) -> Dict[str, Any]:
        """Perform one audited operator action on a live team."""
        return self.ops.control(action, mission_id=mission_id, actor=actor, **params)

    def ops_audit(self, *, limit: int = 25, mission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.ops.recent_audit(limit=limit, mission_id=mission_id)

    def ops_record_outcome(self, mission_id: str) -> Dict[str, Any]:
        return self.ops.record_outcome(mission_id)

    def ops_outcomes(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return self.ops.outcomes(limit=limit)

    def ops_improve(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return self.ops.improvement_suggestions(limit=limit)
