"""Mission runner (Pass 3) — makes a durable Mission actually EXECUTE.

It reuses the existing authorities and creates no second engine:

    MissionPlanner  -> the plan (objective -> tasks with dependencies)
    TaskGraph / TeamOrchestrator (agents.team) -> DAG execution, retry, agents
    AgentService    -> dynamic specialist members + team lifecycle
    CapabilityWorker / Director -> turning a step into a real capability action
    ArtifactService -> deliverables

The runner's only job is to bridge mission steps to that engine, persist a
checkpoint after every step transition, verify the outcome, and decide the
mission's next state (complete / waiting / failed).

Nothing here is the source of truth for mission state — MissionService is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.contracts import (
    CallContext, DataClass, MissionState, MissionStep, ModelRequirement, TaskType,
)
from core.logging_setup import get_logger

log = get_logger("missions.runner")

CONTINUOUS_DEFAULT_MS = 30 * 60 * 1000      # a continuous mission re-checks every 30 min

# ---------------------------------------------------------------- objectives
# B09: a mission step is not "done" because a model produced convincing prose.
# The objective's class decides what evidence is required.
OBJECTIVE_REASONING = "reasoning_only"       # text answer is the deliverable
OBJECTIVE_ARTIFACT = "artifact_producing"    # a real file/artifact must exist
OBJECTIVE_ACTION = "action_required"         # a real-world action must be receipted

_ACTION_VERB_RE = re.compile(
    r"\b(open|launch|create|make|write|save|send|upload|download|install|uninstall|"
    r"delete|remove|move|copy|rename|set|change|enable|disable|start|stop|run|execute|"
    r"click|type|fill|navigate|play|post|publish|submit|schedule|book|order|"
    r"kholo|khol|banao|banado|bhejo|chalao|band|likho|save karo|bhej do|karo)\b",
    re.IGNORECASE)
_ARTIFACT_NOUN_RE = re.compile(
    r"\b(file|document|report|folder|directory|image|photo|picture|spreadsheet|"
    r"csv|xlsx|pdf|note|slide|deck|presentation|backup|archive)\b", re.IGNORECASE)


# MissionPlanner emits PHASE steps whose objective is "<Phase>: <owner goal>".
# Real production acceptance found that the phase prefix was ignored, so
# "Clarify: every morning at 9 send me the weather" inherited the action verb
# "send" from the owner goal and was forced to produce an execution receipt it
# can never have. A Clarify/Plan/Verify/Inspect/Confirm/Report phase IS the
# reasoning work; only an Execute phase carries the action.
_PHASE_REASONING_RE = re.compile(
    r"^\s*(clarify|plan|verify|inspect|dry[\s_-]*run|confirm|report|review|"
    r"summari[sz]e|analyse|analyze)\b\s*:", re.IGNORECASE)
_PHASE_ACTION_RE = re.compile(
    r"^\s*(execute|perform|apply|carry\s*out)\b\s*:", re.IGNORECASE)


def classify_objective(objective: str) -> str:
    """Classify a mission objective so the right evidence is required.

    Phase-aware: a Clarify/Plan/Verify/Inspect/Dry-run/Confirm/Report step is
    reasoning work and is never forced to produce an execution receipt, even
    when the owner's goal contains an action verb. An Execute phase (or a
    plain non-phase objective) still requires a verified receipt for action and
    artifact objectives. Reasoning-only tasks are preserved throughout.
    """
    text = str(objective or "")
    if _PHASE_REASONING_RE.match(text):
        return OBJECTIVE_REASONING
    if _PHASE_ACTION_RE.match(text):
        return OBJECTIVE_ARTIFACT if _ARTIFACT_NOUN_RE.search(text) else OBJECTIVE_ACTION
    has_action = bool(_ACTION_VERB_RE.search(text))
    has_artifact = bool(_ARTIFACT_NOUN_RE.search(text))
    if has_action and has_artifact:
        return OBJECTIVE_ARTIFACT
    if has_action:
        return OBJECTIVE_ACTION
    return OBJECTIVE_REASONING


class _NeverCancel:
    """A cancel token that is never set, for runner-driven tool loops."""

    def is_set(self) -> bool:
        return False

    def wait(self, timeout=None) -> bool:
        return False

    def set(self) -> None:
        return None


class MissionRunner:
    def __init__(self, services: Dict[str, Any]):
        self.services = services

    # ------------------------------------------------------------- helpers
    @property
    def _ms(self):
        return self.services["missions"]

    def _director(self):
        return self.services.get("director")

    def _worker(self):
        return self.services.get("worker")

    def _gateway(self):
        return self.services.get("gateway")

    def _computer(self):
        return self.services.get("computer")

    def _agents(self):
        return self.services.get("agents_service")

    # ---------------------------------------------------------------- plan
    def ensure_plan(self, mission) -> List[Dict[str, Any]]:
        """Return the persisted step DAG, planning it once if it does not exist."""
        existing = self._ms.steps(mission.mission_id)
        if existing:
            return existing
        from missions.planner import MissionPlanner
        plan = MissionPlanner().plan(mission.goal)
        steps: List[MissionStep] = []
        for t in plan.tasks:
            steps.append(MissionStep(
                step_id=t.task_id, type=TaskType.UNKNOWN, capability="",
                objective=t.objective,
                completion_criteria=t.completion_criteria,
                depends_on=list(t.depends_on),
                required_role=t.required_role,
                max_attempts=2, status="pending",
                idempotency_key=f"{mission.mission_id}:{t.task_id}"))
        self._ms.replace_steps(mission.mission_id, steps)
        log.info("mission %s planned: %d steps", mission.mission_id, len(steps))
        return self._ms.steps(mission.mission_id)

    def _team_plan(self, mission, steps: List[Dict[str, Any]]):
        from agents.contracts import TeamPlan
        roles: List[str] = []
        for s in steps:
            r = (s.get("required_role") or "worker").strip() or "worker"
            if r not in roles:
                roles.append(r)
        roles = roles[:6] or ["worker"]          # MAX_TEAM_SIZE is 6
        team = [{"role": r, "capability": "", "quality": ""} for r in roles]
        # ALL steps are included (completed ones too) so dependencies resolve;
        # the runner short-circuits a completed step instead of re-running it.
        tasks = [{
            "task_id": s["step_id"],
            "objective": s.get("objective") or mission.goal,
            "depends_on": json.loads(s.get("depends_on") or "[]"),
            "completion_criteria": s.get("completion_criteria") or "",
            "required_role": (s.get("required_role") or "worker"),
            "requires_review": False,
        } for s in steps]
        return TeamPlan(mission_id=mission.mission_id, objective=mission.goal,
                        team=team, tasks=tasks)

    # ------------------------------------------------------------- execute
    def execute(self, mission_id: str, *, max_steps: int = 40) -> Dict[str, Any]:
        mission = self._ms.get(mission_id)
        if mission is None:
            return {"ok": False, "error": "unknown mission"}
        steps = self.ensure_plan(mission)
        unfinished = [s for s in steps if s["status"] not in ("completed", "cancelled")]
        if not unfinished:
            return self._finalize(mission_id)

        agents = self._agents()
        if agents is None:
            return {"ok": False, "error": "agent service unavailable"}
        plan = self._team_plan(mission, steps)

        # a mission may have run before in this process; start a fresh team.
        if agents.team(mission_id) is not None:
            try:
                agents._teams.pop(mission_id, None)      # noqa: SLF001 (documented)
            except Exception:
                pass

        self._ensure_running(mission_id)
        started = agents.start_team(mission_id=mission_id, objective=mission.goal,
                                    plan=plan, runner=self._make_runner(mission_id))
        if not started.get("ok"):
            log.error("mission %s could not start a team: %s", mission_id,
                      started.get("error"))
            self._ms.record_error(mission_id, f"team start failed: {started.get('error')}")
            return {"ok": False, "error": started.get("error", "start_team failed")}
        try:
            agents.run(mission_id, max_steps=max_steps)
        except Exception as exc:  # noqa: BLE001
            log.error("mission %s run failed: %s", mission_id, exc)
            self._ms.record_error(mission_id, f"run failed: {exc}")
        return self._finalize(mission_id)

    # ------------------------------------------------------------ per step
    def _make_runner(self, mission_id: str):
        def runner(agent_def, node, context):        # Runner contract (agents.team)
            return self._run_node(mission_id, agent_def, node, context)
        return runner

    def _run_node(self, mission_id: str, agent_def, node, context) -> Dict[str, Any]:
        task_id = getattr(node, "task_id", "")
        step = next((s for s in self._ms.steps(mission_id)
                     if s["step_id"] == task_id), None)
        # Idempotency / checkpoint: a verified step is never re-executed, so a
        # resume cannot duplicate its side effect.
        if step and step["status"] == "completed" and step.get("evidence"):
            return {"ok": True, "cached": True, "output": step.get("result") or {}}

        attempt = int((step or {}).get("attempt") or 0) + 1
        self._ms.set_step_status(mission_id, task_id, "running", attempt=attempt)
        objective = (step or {}).get("objective") or getattr(node, "objective", "")

        try:
            outcome = self._act(mission_id, task_id, objective, context)
        except Exception as exc:  # noqa: BLE001
            log.warning("step %s failed: %s", task_id, exc)
            outcome = {"ok": False, "error": str(exc)}

        verified = self._verify(outcome)
        self._ms.set_step_status(
            mission_id, task_id, "completed" if verified else "failed",
            result={"output": str(outcome.get("output", ""))[:4000],
                    "error": str(outcome.get("error", ""))[:400]},
            evidence=outcome.get("evidence") or {},
            provider=str(outcome.get("provider", "")))
        return {"ok": verified, "output": outcome.get("output", ""),
                "evidence": outcome.get("evidence") or {}}

    def _act(self, mission_id: str, step_id: str, objective: str,
             context) -> Dict[str, Any]:
        """Turn one step into a real action: capabilities via the Director, else
        reasoning via the gateway. Never fabricates success."""
        ctx = CallContext(mission_id=mission_id)
        decision = None
        director = self._director()
        if director is not None and objective:
            try:
                decision = director.classify(objective, ctx)
            except Exception as exc:  # noqa: BLE001
                log.debug("director failed for step %s: %s", step_id, exc)
        tasks = list(getattr(decision, "tasks", []) or []) if decision else []
        worker = self._worker()
        idem = f"{mission_id}:{step_id}"

        if tasks and worker is not None:
            results = []
            for t in tasks:
                payload = {"type": t.type.value, "device": t.device,
                           "capability": t.capability, "target": t.target,
                           # idempotency_key lets a side-effecting tool dedupe a retry
                           "params": {**t.params, "idempotency_key": idem},
                           "mission_id": mission_id}
                try:
                    out = worker(ctx, payload) or {}
                except Exception as exc:  # noqa: BLE001
                    out = {"ok": False, "error": str(exc)}
                results.append({"capability": t.capability, **out})
            ok = bool(results) and all(r.get("ok") for r in results)
            return {"ok": ok,
                    "output": ", ".join(str(r.get("capability", "")) for r in results),
                    "evidence": {"kind": "capability", "results": results},
                    "provider": ""}

        gw = self._gateway()
        objective_class = classify_objective(objective)

        # B09: an ACTION objective may not be completed by convincing prose. It
        # must go through the canonical capability path and produce a verified
        # execution receipt, exactly like an ordinary Chat action.
        if objective_class != OBJECTIVE_REASONING and gw is not None:
            computer = self._computer()
            if computer is not None:
                try:
                    from core.tool_dialogue import run as _tool_run
                    receipt_sink: List[Dict[str, Any]] = []
                    req = ModelRequirement(capability="reasoning", min_quality="medium",
                                           data_class=DataClass.INTERNAL, budget_usd=0.25)
                    text = _tool_run(gw, computer, ctx, req,
                                     [{"role": "user", "content": objective}],
                                     _NeverCancel(), receipt_sink=receipt_sink,
                                     user_text=objective)
                    verified = [r for r in receipt_sink if r.get("verified")]
                    if verified:
                        return {"ok": True, "output": text,
                                "evidence": {"kind": "capability",
                                             "receipts": receipt_sink,
                                             "objective_class": objective_class},
                                "provider": ""}
                    return {"ok": False,
                            "error": ("Action objective produced no verified execution "
                                      "receipt; not counted as complete."),
                            "output": text,
                            "evidence": {"kind": "capability",
                                         "receipts": receipt_sink,
                                         "objective_class": objective_class}}
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": str(exc),
                            "evidence": {"kind": "capability",
                                         "objective_class": objective_class}}
            # No execution authority wired: an action objective cannot be claimed.
            return {"ok": False,
                    "error": "No execution authority is available for this action objective.",
                    "evidence": {"kind": "capability", "objective_class": objective_class}}

        if gw is not None:
            try:
                req = ModelRequirement(capability="reasoning", min_quality="medium",
                                       data_class=DataClass.INTERNAL, budget_usd=0.25)
                comp = gw.complete(ctx, req, [{"role": "user", "content": objective}],
                                   max_tokens=600)
                text = getattr(comp, "text", "") or ""
                return {"ok": bool(text.strip()), "output": text,
                        "evidence": {"kind": "reasoning", "chars": len(text),
                                     "objective_class": objective_class},
                        "provider": getattr(comp, "provider_id", "")}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "no executor available"}

    @staticmethod
    def _verify(outcome: Dict[str, Any]) -> bool:
        """A step is done only when it is VERIFIED, not merely issued.

        When the evidence names a file that must exist, its absence is a failure
        even if the capability reported ok.
        """
        if not outcome.get("ok"):
            return False
        ev = outcome.get("evidence") or {}
        # B09: a capability-backed step must carry at least one VERIFIED receipt.
        if ev.get("kind") == "capability":
            receipts = ev.get("receipts") or []
            if not any(r.get("verified") for r in receipts if isinstance(r, dict)):
                return False
        for r in (ev.get("results") or []):
            if not isinstance(r, dict):
                continue
            path = r.get("path") or (r.get("result") or {}).get("path") if isinstance(
                r.get("result"), dict) else r.get("path")
            if isinstance(path, str) and path:
                try:
                    if not Path(path).exists():
                        return False
                except Exception:
                    pass
        return True

    # ------------------------------------------------------------- finalize
    def _ensure_running(self, mission_id: str) -> None:
        m = self._ms.get(mission_id)
        if m and m.state in (MissionState.CREATED, MissionState.PLANNED,
                             MissionState.WAITING, MissionState.PAUSED,
                             MissionState.BLOCKED):
            self._set_state(mission_id, MissionState.RUNNING, "execute")

    def _set_state(self, mission_id: str, state: MissionState, reason: str = "") -> None:
        try:
            self._ms.transition(CallContext(), mission_id, state, reason)
        except Exception:
            self._ms.set_fields(mission_id, state=state.value)

    def _finalize(self, mission_id: str) -> Dict[str, Any]:
        steps = self._ms.steps(mission_id)
        failed = [s for s in steps if s["status"] == "failed"]
        done = [s for s in steps if s["status"] == "completed"]
        mission = self._ms.get(mission_id)
        schedule = self._ms.schedule_of(mission_id)
        recurring = bool((mission and mission.continuous) or
                         (mission and mission.schedule) or schedule)

        if failed:
            self._set_state(mission_id, MissionState.FAILED, "step failure")
            self._ms.record_error(mission_id, f"{len(failed)} step(s) failed")
            state = MissionState.FAILED
        elif len(done) == len(steps) and steps:
            if recurring:
                # A recurring/continuous mission is never "finished"; it waits.
                self._set_state(mission_id, MissionState.WAITING, "iteration complete")
                self._ensure_next_wake(mission_id)
                state = MissionState.WAITING
            else:
                self._set_state(mission_id, MissionState.COMPLETED, "all steps verified")
                self._ms.clear_schedule(mission_id)
                state = MissionState.COMPLETED
        else:
            # blocked on something external — surface it as needing attention
            self._set_state(mission_id, MissionState.WAITING, "awaiting next step")
            state = MissionState.WAITING

        return {"ok": not failed, "state": state.value, "steps": len(steps),
                "done": len(done), "failed": len(failed),
                "progress": self._ms.progress(mission_id)}

    def _ensure_next_wake(self, mission_id: str) -> None:
        """A continuous mission with no explicit schedule still needs a wake time,
        so it re-checks without a busy loop."""
        from core.contracts import now_ms
        if self._ms.schedule_of(mission_id):
            return
        self._ms.set_schedule(mission_id, {"kind": "interval",
                                           "interval_ms": CONTINUOUS_DEFAULT_MS},
                              now_ms() + CONTINUOUS_DEFAULT_MS)


__all__ = ["MissionRunner", "CONTINUOUS_DEFAULT_MS"]
