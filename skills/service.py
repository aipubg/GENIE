"""Skill Service (skills/service.py).

The daemon-side facade that turns the skill modules into one coherent capability surface.

It is deliberately the *only* thing the rest of GENIE talks to. NEDLE2 never sees individual
skills as tools (that would flood a 14 MB router model); it sees one generic capability:

    skill.execute(goal)  ->  the registry picks the compatible skill, or nothing happens

Responsibilities:
  * selection   — registry owns the truth; the service never guesses
  * execution   — every step goes through the shared capability worker (PTE, plugins,
                  computer engine, verification, audit all still apply)
  * learning    — a *successful mission* may become a candidate skill (§5.10)
  * teaching    — a demonstration is recorded semantically, generalised, validated and only
                  then saved. A demonstration is NOT a skill (§5.11-§5.14)
  * lifecycle   — versions, rollback, status transitions, duplicates, corrections, stats

Hard rules enforced here:
  * a skill may never invoke the skill runtime recursively (no `skill.execute` steps)
  * a candidate is never ACTIVE without passing the sandbox validator
  * a user correction never silently rewrites an active skill — it becomes evidence
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, MissionState, new_id
from core.events import get_bus
from core.logging_setup import get_logger
from skills.learning import CandidateDetector, Generalizer, SandboxValidator
from skills.models import (Skill, SkillScope, SkillStatus, validate_skill)
from skills.registry import MIN_SELECTION_SCORE, SkillMatch, SkillRegistry
from skills.runtime import SkillRuntime
from teaching.recorder import TeachingRecorder

log = get_logger("skills.service")

# A skill step must never be one of these — otherwise a learned skill could re-enter the
# runtime and bypass the selection threshold or loop forever.
FORBIDDEN_STEP_CAPABILITIES = ("skill.execute", "skill.search")

TEACHING_STATES = ("CREATED", "RECORDING", "STOPPED", "ANALYZING",
                   "CANDIDATE_CREATED", "VALIDATING", "SAVED", "REJECTED")


@dataclass
class TeachingSession:
    """Explicit teaching state machine (§5.13)."""
    session_id: str = field(default_factory=lambda: new_id("teach"))
    state: str = "CREATED"
    goal: str = ""
    application: str = ""
    scope: str = SkillScope.USER.value
    candidate_id: str = ""
    candidate_version: int = 0
    validation: Dict[str, Any] = field(default_factory=dict)
    saved: bool = False
    created_at: float = field(default_factory=time.time)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def move(self, state: str, detail: str = "") -> None:
        self.state = state
        self.history.append({"state": state, "detail": detail, "ts": time.time()})

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "state": self.state, "goal": self.goal,
                "application": self.application, "scope": self.scope,
                "candidate_id": self.candidate_id, "candidate_version": self.candidate_version,
                "validation": self.validation, "saved": self.saved,
                "created_at": self.created_at, "history": self.history}


class SkillService:
    def __init__(self, db, *, worker=None, audit=None, missions=None, trust=None,
                 registry: Optional[SkillRegistry] = None, gateway=None, director=None,
                 computer=None, guard=None, locks=None,
                 capability_provider=None, plugin_provider=None):
        self.db = db
        self.audit = audit
        self.missions = missions
        self.trust = trust
        self.worker = worker
        self.computer = computer
        self.guard = guard
        self.locks = locks

        self.registry = registry or SkillRegistry(db, audit=audit)
        self.detector = CandidateDetector()
        self.generalizer = Generalizer(gateway=gateway, director=director)
        # The runtime dispatches through a wrapper so a skill can never re-enter itself.
        self.runtime = SkillRuntime(
            self.registry, self._dispatch,
            computer=computer, audit=audit, guard=guard, locks=locks,
            capability_provider=capability_provider or (lambda: []),
            plugin_provider=plugin_provider or (lambda: []),
        )
        self.validator = SandboxValidator(self.runtime, self.registry)

        self.recorder = TeachingRecorder(audit=audit)
        self._sessions: Dict[str, TeachingSession] = {}
        self._active_teaching: str = ""
        self._depth = threading.local()
        self._bus = get_bus()

    # --------------------------------------------------------------- dispatch
    def _dispatch(self, ctx: CallContext, task: Dict[str, Any]) -> Dict[str, Any]:
        """The runtime's step runner. Blocks recursive skill execution, then delegates."""
        capability = str(task.get("capability", ""))
        if capability in FORBIDDEN_STEP_CAPABILITIES:
            return {"ok": False, "verified": False, "capability": capability,
                    "error": "a skill may not invoke the skill runtime recursively",
                    "error_code": "skill_recursion_blocked"}
        depth = getattr(self._depth, "value", 0)
        if depth > 4:
            return {"ok": False, "verified": False, "capability": capability,
                    "error": "skill nesting too deep", "error_code": "skill_depth_exceeded"}
        if self.worker is None:
            return {"ok": False, "verified": False, "capability": capability,
                    "error": "capability worker unavailable", "error_code": "no_worker"}
        self._depth.value = depth + 1
        try:
            return self.worker(ctx, task) or {}
        finally:
            self._depth.value = depth

    # ---------------------------------------------------------------- selection
    def search(self, ctx: CallContext, goal: str, *, limit: int = 5) -> Dict[str, Any]:
        context = self._selection_context()
        matches = self.registry.search(goal, context=context, limit=limit)
        return {"ok": True, "goal": goal, "count": len(matches),
                "matches": [m.to_dict() for m in matches]}

    def _selection_context(self) -> Dict[str, Any]:
        return {
            "capabilities": list(self.runtime.capability_provider()),
            "plugins": list(self.runtime.plugin_provider()),
            "platform": "windows",
        }

    def select(self, goal: str, **kwargs) -> Optional[SkillMatch]:
        return self.registry.select(goal, context=self._selection_context(), **kwargs)

    # ---------------------------------------------------------------- execution
    def execute(self, ctx: CallContext, goal: str, params: Optional[Dict[str, Any]] = None,
                *, dry_run: bool = False, min_score: float = MIN_SELECTION_SCORE,
                on_step=None) -> Dict[str, Any]:
        """Run the best compatible learned skill for `goal`.

        This is what NEDLE2 reaches through `skill.execute`. If nothing scores above the
        threshold GENIE simply does nothing here — the caller then falls back to normal
        reasoning/planning. A low-confidence skill is never run "just because".
        """
        params = dict(params or {})
        match = self.select(goal, min_score=min_score)
        if match is None:
            return {"ok": False, "verified": False, "capability": "skill.execute",
                    "detail": "no compatible learned skill above the selection threshold",
                    "error_code": "no_skill_match", "goal": goal,
                    "min_score": min_score}
        skill = match.skill
        inputs = dict(params)
        # let the caller pass inputs directly (variables) as well as a flat goal
        for name in skill.inputs:
            if name in params:
                inputs[name] = params[name]
        result = self.runtime.run(skill, ctx, inputs, dry_run=dry_run, on_step=on_step)
        payload = result.to_dict()
        payload.update({"capability": "skill.execute", "skill_id": skill.skill_id,
                        "version": skill.version, "score": round(match.score, 4),
                        "match_reasons": match.reasons})
        return payload

    # --------------------------------------------------- learning from missions
    def learn_from_mission(self, ctx: CallContext, mission_id: str, *,
                           auto_save: bool = False, activate: bool = False) -> Dict[str, Any]:
        """§5.10 — a successful mission may become a candidate skill.

        The detector decides *whether* the trace is worth learning; the generalizer turns it
        into a parameterised skill; the validator decides whether it may be saved.
        """
        trace = self._trace_from_mission(mission_id)
        if trace is None:
            return {"ok": False, "error": f"unknown mission {mission_id}"}
        decision = self.detector.should_create(trace)
        if not decision.ok:
            return {"ok": False, "learned": False,
                    "reason": "; ".join(decision.blockers) or "trace not worth learning",
                    "reasons": decision.reasons,
                    "score": round(decision.score, 3), "blockers": decision.blockers}
        skill = self.generalizer.from_trace(trace, name=trace.get("goal", "")[:80])
        problems = validate_skill(skill)
        if problems:
            return {"ok": False, "learned": False, "error": "generalised skill invalid",
                    "problems": problems}
        saved = self._save_candidate(ctx, skill, auto_save=auto_save, activate=activate)
        return {"ok": True, "learned": True, "decision": decision.to_dict(),
                "skill_id": skill.skill_id, "version": skill.version, **saved}

    def _trace_from_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        if self.missions is None:
            return None
        mission = self.missions.get(mission_id)
        if mission is None:
            return None
        steps: List[Dict[str, Any]] = []
        for step in mission.steps:
            if step.status != "done":
                continue
            steps.append({"capability": step.capability, "params": dict(step.params or {}),
                          "description": step.capability, "status": step.status})
        succeeded = mission.state == MissionState.COMPLETED
        # Only a *verified* success may seed a skill (§5.10). The mission's own step results
        # carry the verification verdict, so honour it rather than trusting the state alone.
        verified = succeeded and all(bool((s.result or {}).get("verified", True))
                                     for s in mission.steps if s.status == "done")
        return {"goal": mission.goal, "mission_id": mission_id, "steps": steps,
                "source": "mission", "scope": SkillScope.USER.value,
                "succeeded": succeeded, "verified": verified,
                "environment": {"platform": "windows"}}

    def _save_candidate(self, ctx: CallContext, skill: Skill, *,
                        auto_save: bool, activate: bool) -> Dict[str, Any]:
        duplicates = self.registry.find_duplicates(skill)
        if duplicates:
            return {"saved": False, "duplicates": duplicates,
                    "reason": "a very similar skill already exists"}
        if not auto_save:
            return {"saved": False, "reason": "candidate created (validation not requested)"}
        skill.status = SkillStatus.VALIDATING.value
        validation = self.validator.validate(skill, ctx, self._example_inputs(skill))
        if not validation.get("passed"):
            skill.status = SkillStatus.REJECTED.value
            return {"saved": False, "validation": validation,
                    "reason": "candidate failed validation — not saved"}
        skill.status = SkillStatus.ACTIVE.value
        skill.last_verified = int(time.time())
        outcome = self.registry.register(skill, activate=activate)
        return {"saved": bool(outcome.get("ok")), "validation": validation, **outcome}

    @staticmethod
    def _example_inputs(skill: Skill) -> Dict[str, Any]:
        inputs: Dict[str, Any] = {}
        for name, spec in skill.inputs.items():
            if isinstance(spec, dict) and spec.get("default") is not None:
                inputs[name] = spec["default"]
        return inputs

    # ------------------------------------------------------------- teaching mode
    def start_teaching(self, *, goal: str = "", application: str = "",
                       scope: str = SkillScope.USER.value) -> Dict[str, Any]:
        if self.recorder.recording:
            return {"ok": False, "error": "already recording a demonstration"}
        session = TeachingSession(goal=goal, application=application, scope=scope)
        self._sessions[session.session_id] = session
        started = self.recorder.start(goal=goal, application=application)
        session.application = self.recorder.application
        session.move("RECORDING", f"app={session.application}")
        self._active_teaching = session.session_id
        self._bus.publish("TEACHING_STARTED", {"session_id": session.session_id,
                                               "application": session.application,
                                               "goal": goal})
        return {"ok": True, "session": session.to_dict(), **started}

    def stop_teaching(self) -> Dict[str, Any]:
        session = self._current_session()
        if session is None or not self.recorder.recording:
            return {"ok": False, "error": "no active demonstration"}
        stopped = self.recorder.stop()
        session.move("STOPPED", f"{stopped.get('events', 0)} events")
        self._bus.publish("TEACHING_STOPPED", {"session_id": session.session_id,
                                               "events": stopped.get("events", 0)})
        return {"ok": True, "session": session.to_dict(), **stopped}

    def learn_from_demonstration(self, *, name: str = "", auto_save: bool = True,
                                 activate: bool = False) -> Dict[str, Any]:
        """Demonstration -> candidate -> validate -> (only if it works) save.

        This is the hard rule of §5.12: a demonstration is NOT a skill. Nothing is saved
        unless the candidate passes the sandbox validator against a real run.
        """
        session = self._current_session()
        if session is None:
            return {"ok": False, "error": "no teaching session"}
        if self.recorder.recording:
            self.stop_teaching()
        session.move("ANALYZING", "generalising demonstration")
        payload = self.recorder.to_dict()
        payload["goal"] = session.goal or payload.get("goal", "")
        payload["scope"] = session.scope
        skill = self.generalizer.from_demonstration(payload, name=name or session.goal[:80])
        problems = validate_skill(skill)
        if problems:
            session.move("REJECTED", "generalised skill invalid")
            return {"ok": False, "learned": False, "problems": problems,
                    "session": session.to_dict()}
        if not skill.steps:
            session.move("REJECTED", "no semantic steps recorded")
            return {"ok": False, "learned": False,
                    "reason": "the demonstration produced no semantic steps "
                              "(a raw coordinate recording is not a skill)",
                    "session": session.to_dict()}
        session.candidate_id = skill.skill_id
        session.candidate_version = skill.version
        session.move("CANDIDATE_CREATED", f"{len(skill.steps)} steps")

        duplicates = self.registry.find_duplicates(skill)
        if duplicates:
            session.move("REJECTED", "duplicate of an existing skill")
            return {"ok": False, "learned": False, "duplicates": duplicates,
                    "session": session.to_dict()}

        if not auto_save:
            return {"ok": True, "learned": False, "candidate": skill.to_dict(),
                    "session": session.to_dict(),
                    "reason": "candidate created — awaiting owner confirmation"}

        session.move("VALIDATING", "sandbox validation")
        ctx = CallContext(person_id="owner", dry_run=False)
        validation = self.validator.validate(skill, ctx, self._example_inputs(skill))
        session.validation = validation
        if not validation.get("passed"):
            session.move("REJECTED", f"validation failed at {validation.get('stage')}")
            self._bus.publish("SKILL_CANDIDATE_REJECTED",
                              {"session_id": session.session_id,
                               "skill_id": skill.skill_id, "stage": validation.get("stage")})
            return {"ok": False, "learned": False, "validation": validation,
                    "session": session.to_dict()}
        skill.status = SkillStatus.ACTIVE.value
        skill.last_verified = int(time.time())
        outcome = self.registry.register(skill, activate=activate)
        if not outcome.get("ok"):
            session.move("REJECTED", outcome.get("error", "registration failed"))
            return {"ok": False, "learned": False, **outcome, "session": session.to_dict()}
        session.saved = True
        session.move("SAVED", f"{skill.key} active")
        self._bus.publish("SKILL_LEARNED", {"skill_id": skill.skill_id,
                                            "version": skill.version,
                                            "source": "teaching",
                                            "session_id": session.session_id})
        return {"ok": True, "learned": True, "skill_id": skill.skill_id,
                "version": skill.version, "validation": validation,
                "session": session.to_dict()}

    def discard_teaching(self) -> Dict[str, Any]:
        session = self._current_session()
        if session is None:
            return {"ok": False, "error": "no teaching session"}
        if self.recorder.recording:
            self.recorder.stop()
        session.move("REJECTED", "discarded by owner")
        self._sessions.pop(session.session_id, None)
        self._active_teaching = ""
        return {"ok": True, "discarded": session.to_dict()}

    def _current_session(self) -> Optional[TeachingSession]:
        if self._active_teaching and self._active_teaching in self._sessions:
            return self._sessions[self._active_teaching]
        if self._sessions:
            return list(self._sessions.values())[-1]
        return None

    def teaching_status(self) -> Dict[str, Any]:
        session = self._current_session()
        return {"active": bool(session), "recording": self.recorder.recording,
                "recorder": self.recorder.summary(),
                "session": session.to_dict() if session else None}

    # ------------------------------------------------- correction evidence §5.18
    def record_correction(self, *, skill_id: str, version: int, step_id: str,
                          failed_step: str, user_action: str = "",
                          previous_state: str = "", resulting_state: str = "",
                          detail: str = "") -> Dict[str, Any]:
        """A user fix is evidence, not a silent rewrite of the active skill."""
        self.db.execute(
            "INSERT INTO skill_corrections(skill_id, version, step_id, failed_step,"
            " user_action, previous_state, resulting_state, detail, ts)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (skill_id, int(version), step_id, failed_step, user_action,
             previous_state, resulting_state, detail, int(time.time())))
        if self.audit:
            self.audit.record(who="owner", action="skill.correction",
                              why=f"{skill_id}@{version} step={step_id}",
                              result=user_action[:120])
        self._bus.publish("SKILL_CORRECTION", {"skill_id": skill_id, "version": version,
                                               "step_id": step_id, "action": user_action})
        return {"ok": True, "skill_id": skill_id, "version": version, "step_id": step_id}

    def corrections(self, skill_id: str = "", *, limit: int = 100) -> List[Dict[str, Any]]:
        if skill_id:
            rows = self.db.query("SELECT * FROM skill_corrections WHERE skill_id=?"
                                 " ORDER BY ts DESC LIMIT ?", (skill_id, limit))
        else:
            rows = self.db.query("SELECT * FROM skill_corrections ORDER BY ts DESC LIMIT ?",
                                 (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- lifecycle API
    def list(self, *, status: Optional[str] = None, scope: Optional[str] = None,
             limit: int = 100) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.registry.list(status=status, scope=scope, limit=limit)]

    def get(self, skill_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        skill = self.registry.get(skill_id, version)
        return skill.to_dict() if skill else None

    def versions(self, skill_id: str) -> List[Dict[str, Any]]:
        return [{"skill_id": s.skill_id, "version": s.version, "status": s.status,
                 "active": s.version == self.registry.active_version(skill_id),
                 "success_count": s.success_count, "failure_count": s.failure_count,
                 "created_at": s.created_at}
                for s in sorted(self.registry.versions(skill_id), key=lambda x: x.version)]

    def rollback(self, skill_id: str, to_version: Optional[int] = None) -> Dict[str, Any]:
        return self.registry.rollback(skill_id, to_version)

    def set_status(self, skill_id: str, status: str, *, reason: str = "") -> Dict[str, Any]:
        return self.registry.set_status(skill_id, status, reason=reason)

    def delete(self, skill_id: str, version: Optional[int] = None, *,
               archive: bool = True) -> Dict[str, Any]:
        return self.registry.delete(skill_id, version, archive=archive)

    def duplicates(self, skill_id: str, *, threshold: float = 0.75) -> Dict[str, Any]:
        skill = self.registry.get(skill_id)
        if skill is None:
            return {"ok": False, "error": f"unknown skill {skill_id}"}
        return {"ok": True, "skill_id": skill_id,
                "duplicates": self.registry.find_duplicates(skill, threshold=threshold)}

    def stats(self) -> Dict[str, Any]:
        return self.registry.stats()

    def status(self) -> Dict[str, Any]:
        return {"skills": self.registry.stats(), "teaching": self.teaching_status(),
                "selection_threshold": MIN_SELECTION_SCORE}

    # ------------------------------------------------------------ teaching capture
    def capture(self, kind: str, **data: Any) -> Optional[Dict[str, Any]]:
        """Bridge used by the computer/browser/plugin layers to feed the recorder."""
        if not self.recorder.recording:
            return None
        return self.recorder.record(kind, **data)

    @property
    def recorder_proxy(self) -> TeachingRecorder:
        return self.recorder
