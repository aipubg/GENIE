"""Agent Runtime (agents/runtime) — contract C6.

Phase 1 scope: a single capability worker with hard budgets (steps, cost, wall time) and
first-class cancellation. Multi-agent teams (Lead/Architect/...) arrive in Phase 10, but the
interface already reserves them (mailbox + blackboard are per-mission, not shared transcripts).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.contracts import (
    AgentDefinition, AgentState, CallContext, EventType, TaskType,
)
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("agents.runtime")

StepRunner = Callable[[CallContext, Dict[str, Any]], Dict[str, Any]]


class AgentRuntime:
    def __init__(self, step_runner: StepRunner, audit=None):
        self.step_runner = step_runner
        self.audit = audit
        self._bus = get_bus()
        self._lock = threading.Lock()
        self._cancel: Dict[str, threading.Event] = {}
        self._agents: Dict[str, AgentState] = {}

    # ----------------------------------------------------------------- spawn
    def spawn(self, definition: AgentDefinition, mission_id: Optional[str] = None) -> AgentState:
        st = AgentState(name=definition.name, mission_id=mission_id, status="idle")
        with self._lock:
            self._agents[st.agent_id] = st
            self._cancel[st.agent_id] = threading.Event()
        self._bus.publish(EventType.AGENT_STARTED,
                          {"agent_id": st.agent_id, "name": definition.name,
                           "mission_id": mission_id})
        return st

    def cancel(self, agent_id: str) -> bool:
        ev = self._cancel.get(agent_id)
        if not ev:
            return False
        ev.set()
        st = self._agents.get(agent_id)
        if st:
            st.status = "cancelled"
        return True

    def state(self, agent_id: str) -> Optional[AgentState]:
        return self._agents.get(agent_id)

    def active(self) -> List[Dict[str, Any]]:
        return [s.__dict__ for s in self._agents.values()]

    # ------------------------------------------------------------------- run
    def run(self, agent: AgentState, definition: AgentDefinition, ctx: CallContext,
            tasks: List[Dict[str, Any]]) -> AgentState:
        started = time.time()
        agent.status = "running"
        results: List[Dict[str, Any]] = []
        for task in tasks:
            if self._cancel.get(agent.agent_id) and self._cancel[agent.agent_id].is_set():
                agent.status = "cancelled"
                break
            if agent.steps_used >= definition.max_steps:
                agent.status = "failed"
                agent.error = "step budget exhausted"
                break
            if (time.time() - started) * 1000 > definition.max_wall_ms:
                agent.status = "failed"
                agent.error = "wall clock budget exhausted"
                break

            agent.steps_used += 1
            task_ctx = ctx.with_(agent_id=agent.agent_id)
            try:
                out = self.step_runner(task_ctx, task)
            except Exception as exc:
                out = {"ok": False, "error": str(exc)}
                log.warning("agent step failed: %s", exc)
            results.append({"task": task, "result": out})
            if not out.get("ok", False):
                agent.status = "failed"
                agent.error = str(out.get("error") or out.get("detail") or "step failed")
                self._bus.publish(EventType.AGENT_FAILED,
                                  {"agent_id": agent.agent_id, "error": agent.error})
                break

        if agent.status == "running":
            agent.status = "done"
        if self.audit:
            self.audit.record(who=f"agent:{agent.agent_id}", action="agent.run",
                              why=definition.purpose, mission_id=agent.mission_id,
                              result=f"{agent.status} steps={agent.steps_used}",
                              trace_id=ctx.trace_id)
        return agent


# --------------------------------------------------------------- first worker
# Generic capabilities that a plugin can serve natively. Plugins improve reliability but are
# never required: if the plugin is missing or lacks permission, GENIE falls back to the
# generic chain (OS automation -> UIA -> vision -> raw input).
PLUGIN_PREFERRED = {
    "media.play": "plugin.media.play",
    "media.pause": "plugin.media.pause",
    "media.next": "plugin.media.next",
    "media.previous": "plugin.media.previous",
}


class CapabilityWorker:
    """Executes director tasks by dispatching to the right service.

    Order: skill runtime -> plugin (native app integration) -> computer engine -> device mesh.
    """

    def __init__(self, computer=None, device=None, plugins=None, guard=None, skills=None):
        self.computer = computer
        self.device = device
        self.plugins = plugins
        self.guard = guard
        self.skills = skills

    def _try_plugin(self, ctx: CallContext, capability: str,
                    params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.plugins:
            return None
        target = capability if self.plugins.owns(capability) else PLUGIN_PREFERRED.get(capability)
        if not target or not self.plugins.owns(target):
            return None
        invocation = self.plugins.execute(ctx, target, params)
        if invocation.error_code == "permission_denied":
            # permission problems are reported, not silently routed around
            return {"ok": False, "capability": target, "verified": False,
                    "detail": invocation.error, "error": invocation.error,
                    "plugin": invocation.plugin_id, "error_code": invocation.error_code,
                    "available": True}
        if not invocation.ok and not invocation.available:
            return None                     # plugin cannot serve: fall back to generic chain
        return {"ok": invocation.ok, "capability": target,
                "verified": invocation.ok and invocation.available,
                "detail": invocation.detail or invocation.error,
                "plugin": invocation.plugin_id, "available": invocation.available,
                "error_code": invocation.error_code, "latency_ms": invocation.latency_ms}

    def _run_skill(self, ctx: CallContext, capability: str,
                   params: Dict[str, Any]) -> Dict[str, Any]:
        """Route skill.search / skill.execute to the skill service.

        NEDLE2 only ever sees this one generic surface; the registry decides which concrete
        skill (if any) runs. No match is a normal, honest outcome — not an error.
        """
        if self.skills is None:
            return {"ok": False, "verified": False, "capability": capability,
                    "error": "skill service unavailable", "error_code": "no_skill_service"}
        goal = str(params.get("goal") or params.get("query") or params.get("text") or "")
        if capability == "skill.search":
            return {"ok": True, "capability": capability, **self.skills.search(ctx, goal)}
        result = self.skills.execute(ctx, goal, params)
        result.setdefault("capability", capability)
        return result

    def __call__(self, ctx: CallContext, task: Dict[str, Any], *, cancel_event=None) -> Dict[str, Any]:
        ttype = task.get("type", "")
        capability = task.get("capability", "")
        params = dict(task.get("params") or {})
        if task.get("target"):
            params.setdefault("target", task["target"])

        # 0. prompt-injection boundary: untrusted content never authorises an action
        if self.guard is not None:
            decision = self.guard.guard_action(ctx, capability, params,
                                               user_confirmed=bool(params.get("confirmed")))
            if not decision.allow:
                return {"ok": False, "capability": capability, "verified": False,
                        "detail": decision.reason, "error": decision.reason,
                        "error_code": "blocked_untrusted_content",
                        "findings": decision.findings, "needs_confirm": decision.needs_confirm}

        # 1. learned skills — the registry owns selection; a weak match simply declines
        if capability in ("skill.execute", "skill.search"):
            return self._run_skill(ctx, capability, params)

        # 2. plugin / native application integration
        plugin_result = self._try_plugin(ctx, capability, params)
        if plugin_result is not None:
            return plugin_result

        if ttype in (TaskType.APPLICATION_ACTION.value, TaskType.COMPUTER_ACTION.value,
                     TaskType.FILE_ACTION.value, TaskType.BROWSER_ACTION.value):
            if not self.computer:
                return {"ok": False, "error": "computer service unavailable"}
            res = (self.computer.execute(ctx, capability, params, cancel_event=cancel_event)
                   if cancel_event is not None else self.computer.execute(ctx, capability, params))
            data = res.data or {}
            return {
                "ok": res.ok, "detail": res.detail, "verified": res.verified,
                "capability": capability,
                # Preserve the bounded OS measurement required to answer a
                # storage query. Other capability payloads may contain private
                # data and are intentionally not copied into chat receipts.
                **({"data": data} if capability in ("files.disk_usage",
                                                       "system.audio.devices",
                                                       "system.network.state") else {}),
                # execution diagnostics travel with the step so the UI/trace can show how
                # the result was produced and verified
                "strategy_used": data.get("strategy_used"),
                "strategies_tried": data.get("strategies_tried"),
                "attempts": data.get("attempts"),
                "latency_ms": data.get("latency_ms"),
                "verify_ms": data.get("verify_ms"),
                "verification": data.get("verification"),
                "dry_run": data.get("dry_run"),
                "takeover": data.get("takeover"),
            }

        if ttype == TaskType.DEVICE_ACTION.value:
            if not self.device:
                return {"ok": False, "error": "device mesh not available",
                        "error_code": "no_device_mesh", "queued": True}
            invocation = self.device.execute(ctx, task.get("device", "pc_main"), capability,
                                             params)
            return {"ok": invocation.ok, "capability": capability,
                    "verified": invocation.verified, "detail": invocation.detail,
                    "device": invocation.device_id, "status": invocation.status,
                    "command_id": invocation.command_id, "queued": invocation.queued,
                    "latency_ms": invocation.latency_ms,
                    "error": invocation.error, "error_code": invocation.error_code}

        return {"ok": False, "error": f"no worker for task type {ttype}"}
