"""Phase 5 — candidate detection, generalisation, validation (§5.9-§5.14).

The hard rule under test: **a demonstration is not a skill**. Nothing becomes ACTIVE without
passing the sandbox validator against a real run.
"""
from __future__ import annotations

import pytest

from core.contracts import CallContext, MissionState, TaskType
from skills.learning import (CONFIG_HINTS, MIN_STEPS_FOR_CANDIDATE, CandidateDetector,
                             Generalizer, SandboxValidator)
from skills.models import Skill, SkillScope, SkillStatus, SkillStep, validate_skill
from skills.registry import SkillRegistry


def trace(steps=None, *, succeeded=True, verified=True, goal="write a dated note",
          user_requested=False, recurring_hint=False) -> dict:
    return {"goal": goal, "mission_id": "mis_test", "source": "mission",
            "succeeded": succeeded, "verified": verified,
            "user_requested": user_requested, "recurring_hint": recurring_hint,
            "steps": steps if steps is not None else [
                {"capability": "application.open", "params": {"target": "notepad"}},
                {"capability": "input.type_text", "params": {"text": "note for 2026-09-17"}},
                {"capability": "files.write", "params": {"path": r"C:\notes\2026-09-17.txt",
                                                         "content": "note"}},
            ]}


# --------------------------------------------------------- candidate detector §5.10
def test_detector_accepts_a_real_multi_step_successful_workflow():
    decision = CandidateDetector().should_create(trace())
    assert decision.ok is True
    assert decision.score >= 0.5
    assert decision.blockers == []


def test_detector_rejects_a_trivial_workflow():
    decision = CandidateDetector().should_create(trace(steps=[
        {"capability": "files.write", "params": {"path": r"C:\a.txt", "content": "x"}}]))
    assert decision.ok is False
    assert any("too trivial" in b for b in decision.blockers)


def test_detector_rejects_a_failed_mission():
    decision = CandidateDetector().should_create(trace(succeeded=False))
    assert decision.ok is False
    assert any("did not succeed" in b for b in decision.blockers)


def test_detector_rejects_an_unverified_mission():
    decision = CandidateDetector().should_create(trace(verified=False))
    assert decision.ok is False
    assert any("not verified" in b for b in decision.blockers)


def test_detector_rejects_a_read_only_workflow():
    decision = CandidateDetector().should_create(trace(steps=[
        {"capability": "window.list", "params": {}},
        {"capability": "files.read", "params": {"path": r"C:\a.txt"}},
        {"capability": "processes.list", "params": {}},
    ]))
    assert decision.ok is False
    assert any("read-only" in b for b in decision.blockers)


def test_detector_accepts_a_trivial_workflow_when_the_user_asked():
    decision = CandidateDetector().should_create(trace(steps=[
        {"capability": "files.write", "params": {"path": r"C:\a.txt", "content": "x"}}],
        user_requested=True))
    assert decision.ok is True


def test_detector_rejects_steps_with_no_generalizable_value():
    decision = CandidateDetector().should_create(trace(steps=[
        {"capability": "system.volume.mute", "params": {}},
        {"capability": "system.volume.up", "params": {}},
        {"capability": "system.volume.down", "params": {}},
    ]))
    assert decision.ok is False
    assert any("generalizable" in b for b in decision.blockers)


def test_detector_accepts_a_volume_workflow_that_carries_a_level():
    """A level is a genuine variable, so this is a legitimate (if simple) skill."""
    decision = CandidateDetector().should_create(trace(steps=[
        {"capability": "system.volume.set", "params": {"level": 30}},
        {"capability": "system.volume.up", "params": {}},
        {"capability": "system.volume.down", "params": {}},
    ]))
    assert decision.ok is True


def test_detector_reports_its_reasoning():
    decision = CandidateDetector().should_create(trace())
    payload = decision.to_dict()
    assert payload["reasons"] and isinstance(payload["score"], float)


# ----------------------------------------------------------- generalizer §5.8/§5.12
def test_generalizer_turns_a_trace_into_a_parameterised_skill():
    skill = Generalizer().from_trace(trace())
    assert skill.status == SkillStatus.CANDIDATE.value
    assert validate_skill(skill) == []
    assert len(skill.steps) == 3
    # the concrete path became a variable — no environment value is baked in
    joined = str(skill.to_dict())
    assert r"C:\notes\2026-09-17.txt" not in joined
    assert "${" in joined


def test_generalizer_extracts_inputs_and_preconditions():
    skill = Generalizer().from_trace(trace())
    assert skill.inputs, "the generalizer must declare inputs for the extracted variables"
    assert skill.required_capabilities == ["application.open", "files.write",
                                           "input.type_text"]


def test_generalizer_derives_verification_from_the_steps():
    skill = Generalizer().from_trace(trace())
    assert skill.verification, "a generalised skill must define how success is decided"
    assert all("check" in c for c in skill.verification)


def test_generalizer_keeps_configuration_values_literal():
    skill = Generalizer().from_trace(trace(steps=[
        {"capability": "application.open", "params": {"target": "notepad"}},
        {"capability": "image.convert", "params": {"format": "png", "path": r"C:\a\b.png"}},
        {"capability": "files.write", "params": {"path": r"C:\a\out.txt", "content": "x"}},
    ]))
    formats = [s.params.get("format") for s in skill.steps if "format" in s.params]
    assert formats == ["png"], "configuration values must stay literal, not become variables"


def test_generalizer_provenance_records_the_mission():
    skill = Generalizer().from_trace(trace())
    assert skill.provenance["source"] == "mission"
    assert skill.provenance["mission_id"] == "mis_test"


def test_generalizer_from_demonstration_uses_semantic_events():
    session = {
        "session_id": "teach_1", "goal": "write a dated note", "application": "notepad.exe",
        "events": [
            {"kind": "app_launch", "target": "notepad", "ts": 1.0},
            {"kind": "typing", "text": "note for 2026-09-17", "target_field": "edit", "ts": 2.0},
            {"kind": "file_save", "path": r"C:\notes\2026-09-17.txt", "content": "note", "ts": 3.0},
        ],
    }
    skill = Generalizer().from_demonstration(session)
    assert skill.provenance["source"] == "teaching"
    assert skill.provenance["session_id"] == "teach_1"
    assert skill.steps
    assert validate_skill(skill) == []


def test_generalizer_from_demonstration_of_pure_mouse_events_yields_no_steps():
    """A raw coordinate recording is NOT a skill."""
    session = {"session_id": "teach_2", "goal": "click around", "application": "x.exe",
               "events": [{"kind": "mouse", "x": 844, "y": 512, "ts": 1.0},
                          {"kind": "mouse", "x": 900, "y": 400, "ts": 2.0}]}
    skill = Generalizer().from_demonstration(session)
    assert skill.steps == []


def test_generalizer_refine_without_a_model_is_an_honest_failure():
    outcome = Generalizer().refine_with_model(Generalizer().from_trace(trace()))
    assert outcome["ok"] is False
    assert "no model gateway" in outcome["error"]


# ----------------------------------------------------------- sandbox validator §5.9
def _write_skill(tmp_path) -> Skill:
    target = tmp_path / "note.txt"
    return Skill(
        skill_id="write-note", name="write a note",
        steps=[SkillStep(capability="files.write",
                         params={"path": str(target), "text": "hello"})],
        inputs={},
        verification=[{"check": "file_exists", "value": str(target)},
                      {"check": "file_contains", "value": str(target), "text": "hello"}],
        required_capabilities=["files.write"], status=SkillStatus.CANDIDATE.value)


def test_validator_runs_the_skill_and_verifies_the_side_effect(app, tmp_path):
    registry = SkillRegistry(app.db)
    validator = SandboxValidator(app.skills.runtime, registry)
    skill = _write_skill(tmp_path)
    outcome = validator.validate(skill, app.ctx(person_id="owner"), {})
    assert outcome["passed"] is True
    assert outcome["stage"] == "execution"
    assert outcome["verified"] is True
    assert (tmp_path / "note.txt").read_text() == "hello"


def test_validator_fails_a_structurally_invalid_skill(app, tmp_path):
    registry = SkillRegistry(app.db)
    validator = SandboxValidator(app.skills.runtime, registry)
    skill = _write_skill(tmp_path)
    skill.verification = []
    outcome = validator.validate(skill, app.ctx(person_id="owner"), {})
    assert outcome["passed"] is False and outcome["stage"] == "structure"


def test_validator_fails_when_the_verification_does_not_hold(app, tmp_path):
    """A step that runs but does not produce the promised result must NOT pass."""
    registry = SkillRegistry(app.db)
    validator = SandboxValidator(app.skills.runtime, registry)
    skill = _write_skill(tmp_path)
    skill.verification = [{"check": "file_exists",
                           "value": str(tmp_path / "never-created.txt")}]
    outcome = validator.validate(skill, app.ctx(person_id="owner"), {})
    assert outcome["passed"] is False and outcome["stage"] == "execution"


def test_validator_can_stop_after_the_dry_run(app, tmp_path):
    registry = SkillRegistry(app.db)
    validator = SandboxValidator(app.skills.runtime, registry)
    skill = _write_skill(tmp_path)
    outcome = validator.validate(skill, app.ctx(person_id="owner"), {}, execute=False)
    assert outcome["passed"] is True and outcome["stage"] == "dry_run"


# ------------------------------------------------- demonstration is not a skill §5.12
def test_demonstration_alone_never_registers_an_active_skill(app):
    app.skills.start_teaching(goal="write a dated note", application="notepad.exe")
    app.skills.recorder.note_app_launch("notepad")
    app.skills.recorder.note_typing("note for 2026-09-17")
    app.skills.recorder.note_file_save(r"C:\notes\2026-09-17.txt", "note")
    app.skills.stop_teaching()
    # recording alone must not have created anything
    assert app.skills.list() == []


def test_candidate_is_created_but_not_saved_when_confirmation_is_required(app):
    app.skills.start_teaching(goal="write a dated note", application="notepad.exe")
    app.skills.recorder.note_app_launch("notepad")
    app.skills.recorder.note_typing("note for 2026-09-17")
    app.skills.recorder.note_file_save(r"C:\notes\2026-09-17.txt", "note")
    outcome = app.skills.learn_from_demonstration(auto_save=False)
    assert outcome["ok"] is True and outcome["learned"] is False
    assert outcome["candidate"]["status"] == SkillStatus.CANDIDATE.value
    assert app.skills.list() == [], "an unconfirmed candidate must not be registered"


def test_teaching_session_state_machine_is_traceable(app):
    started = app.skills.start_teaching(goal="write a note", application="notepad.exe")
    session_id = started["session"]["session_id"]
    assert started["session"]["state"] == "RECORDING"
    app.skills.recorder.note_app_launch("notepad")
    app.skills.recorder.note_typing("hello")
    app.skills.recorder.note_file_save(r"C:\notes\x.txt", "hello")
    app.skills.learn_from_demonstration(auto_save=False)
    status = app.skills.teaching_status()
    states = [h["state"] for h in status["session"]["history"]]
    assert states[0] == "RECORDING"
    assert "STOPPED" in states and "ANALYZING" in states and "CANDIDATE_CREATED" in states


def test_discarding_a_teaching_session_leaves_nothing_behind(app):
    app.skills.start_teaching(goal="write a note")
    app.skills.recorder.note_typing("hello")
    outcome = app.skills.discard_teaching()
    assert outcome["ok"] is True
    assert app.skills.list() == []


def test_learning_from_a_mission_requires_a_verified_success(app):
    ctx = app.ctx(person_id="owner")
    mission = app.missions.create(ctx, "write a dated note")
    steps = [
        (TaskType.FILE_ACTION, "files.write", {"path": r"C:\notes\2026-09-17.txt",
                                               "content": "note"}),
        (TaskType.APPLICATION_ACTION, "application.open", {"target": "notepad"}),
        (TaskType.COMPUTER_ACTION, "system.volume.set", {"level": 30}),
    ]
    app.missions.plan(ctx, mission.mission_id, [
        __import__("core.contracts", fromlist=["MissionStep"]).MissionStep(
            type=t, capability=c, params=p) for t, c, p in steps])
    app.missions.transition(ctx, mission.mission_id, MissionState.RUNNING, "test")
    for step in app.missions.get(mission.mission_id).steps:
        app.missions.update_step(mission.mission_id, step.step_id, "done", {"verified": True})
    app.missions.transition(ctx, mission.mission_id, MissionState.COMPLETED, "test")
    outcome = app.skills.learn_from_mission(ctx, mission.mission_id)
    assert outcome["ok"] is True and outcome["learned"] is True
    assert outcome["saved"] is False, "a candidate is not saved without validation"


def test_learning_from_a_failed_mission_is_refused(app):
    ctx = app.ctx(person_id="owner")
    mission = app.missions.create(ctx, "write a dated note")
    from core.contracts import MissionStep
    app.missions.plan(ctx, mission.mission_id, [
        MissionStep(type=TaskType.FILE_ACTION, capability="files.write",
                    params={"path": r"C:\n.txt", "content": "x"}),
        MissionStep(type=TaskType.APPLICATION_ACTION, capability="application.open",
                    params={"target": "notepad"}),
        MissionStep(type=TaskType.COMPUTER_ACTION, capability="system.volume.set",
                    params={"level": 30}),
    ])
    outcome = app.skills.learn_from_mission(ctx, mission.mission_id)
    assert outcome["ok"] is False and outcome["learned"] is False
    assert outcome["blockers"]
