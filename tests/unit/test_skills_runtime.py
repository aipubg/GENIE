"""Phase 5 — correction evidence, user takeover, stats and the NEDLE2 surface.

Covers §5.18 (a user fix is evidence, never a silent rewrite), §5.25 (correction learning),
the USER_TAKEOVER path, and §5.28 (NEDLE2 only ever sees a generic skill surface).
"""
from __future__ import annotations

import pytest

from core.contracts import CallContext
from skills.models import Skill, SkillStatus, SkillStep


def write_skill(tmp_path, *, path=None, verification=None, skill_id="write-note") -> Skill:
    target = path or (tmp_path / "note.txt")
    return Skill(
        skill_id=skill_id, name="write a note",
        steps=[SkillStep(capability="files.write",
                         params={"path": str(target), "text": "hello"})],
        inputs={},
        verification=verification or [{"check": "file_exists", "value": str(target)},
                                      {"check": "file_contains", "value": str(target),
                                       "text": "hello"}],
        required_capabilities=["files.write"], status=SkillStatus.ACTIVE.value)


# ------------------------------------------------- correction evidence §5.18/§5.25
def test_correction_is_recorded_as_evidence(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    outcome = app.skills.record_correction(
        skill_id="write-note", version=1, step_id=skill.steps[0].id,
        failed_step="files.write", user_action="typed the path manually",
        previous_state="save dialog open", resulting_state="file saved",
        detail="the dialog did not accept the typed path")
    assert outcome["ok"] is True
    stored = app.skills.corrections("write-note")
    assert len(stored) == 1
    assert stored[0]["failed_step"] == "files.write"
    assert stored[0]["user_action"] == "typed the path manually"


def test_correction_never_silently_rewrites_the_active_skill(app, tmp_path):
    """The active skill must be byte-identical after a correction is recorded."""
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    before = app.skills.get("write-note", 1)
    app.skills.record_correction(skill_id="write-note", version=1, step_id="s1",
                                 failed_step="files.write", user_action="fixed it")
    after = app.skills.get("write-note", 1)
    assert before == after
    assert app.skills.versions("write-note")[0]["version"] == 1


def test_corrections_are_audited(app, tmp_path):
    app.skills.record_correction(skill_id="write-note", version=1, step_id="s1",
                                 failed_step="files.write", user_action="manual fix")
    entries = [e for e in app.audit.tail(50) if e.get("action") == "skill.correction"]
    assert entries, "recording a correction must leave an audit entry"


def test_corrections_can_be_filtered_by_skill(app):
    app.skills.record_correction(skill_id="skill-a", version=1, step_id="s1",
                                 failed_step="x", user_action="fix a")
    app.skills.record_correction(skill_id="skill-b", version=1, step_id="s2",
                                 failed_step="y", user_action="fix b")
    assert [c["skill_id"] for c in app.skills.corrections("skill-a")] == ["skill-a"]
    assert len(app.skills.corrections()) == 2


# --------------------------------------------------------------- takeover §5.18
def test_user_takeover_pauses_the_skill_and_records_evidence(app, tmp_path, monkeypatch):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    # takeover is checked before every step, so the very first check already sees the owner
    monkeypatch.setattr(app.computer, "check_takeover",
                        lambda: {"reason": "owner grabbed the mouse", "ts": 1},
                        raising=False)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.status == "paused_by_takeover"
    assert result.ok is False
    assert result.corrections and result.corrections[0]["kind"] == "user_takeover"
    assert app.skills.registry.get("write-note", 1).user_takeovers == 1


def test_takeover_is_not_counted_as_a_success(app, tmp_path, monkeypatch):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    monkeypatch.setattr(app.computer, "check_takeover",
                        lambda: {"reason": "owner intervened", "ts": 1}, raising=False)
    app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    stored = app.skills.registry.get("write-note", 1)
    assert stored.user_takeovers == 1
    assert stored.success_count == 0


# ------------------------------------------------------------------ stats §5.20
def test_successful_run_updates_statistics(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.ok is True
    stored = app.skills.registry.get("write-note", 1)
    assert stored.success_count == 1
    assert stored.failure_count == 0
    assert stored.last_verified > 0


def test_failed_run_records_the_failing_step(app, tmp_path):
    skill = write_skill(tmp_path, verification=[{"check": "file_exists",
                                                 "value": str(tmp_path / "nope.txt")}])
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.ok is False
    stored = app.skills.registry.get("write-note", 1)
    assert stored.failure_count == 1
    assert stored.known_failures


# --------------------------------------------------- preconditions §5.6/§5.15
def test_missing_precondition_rejects_instead_of_blind_replay(app, tmp_path):
    skill = write_skill(tmp_path)
    skill.preconditions = [{"check": "app_installed", "value": "definitely-not-installed-xyz"}]
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.status == "rejected"
    assert "preconditions not met" in result.detail
    assert result.ok is False


def test_missing_required_input_is_rejected_before_running(app, tmp_path):
    skill = write_skill(tmp_path)
    skill.inputs = {"path": {"type": "string", "required": True}}
    skill.steps[0].params = {"path": "${path}", "text": "hello"}
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.status == "rejected"
    assert "missing required inputs" in result.detail


def test_unresolved_variable_is_rejected_before_running(app, tmp_path):
    skill = write_skill(tmp_path)
    skill.inputs = {}
    skill.steps[0].params = {"path": "${unknown_variable}", "text": "hello"}
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.status == "rejected"
    assert "unresolved variables" in result.detail


# --------------------------------------------------------- recursion guard §5.28
def test_a_skill_may_not_invoke_the_skill_runtime(app, tmp_path):
    skill = write_skill(tmp_path)
    skill.steps = [SkillStep(capability="skill.execute", params={"goal": "do something else"})]
    skill.verification = [{"check": "all_steps_verified"}]
    app.skills.registry.register(skill, activate=True)
    result = app.skills.runtime.run(skill, CallContext(person_id="owner"), {})
    assert result.ok is False
    assert "recursively" in result.detail


def test_capability_worker_blocks_recursive_skill_steps(app):
    out = app.skills._dispatch(CallContext(person_id="owner"),
                               {"capability": "skill.execute", "params": {"goal": "x"}})
    assert out["ok"] is False
    assert out["error_code"] == "skill_recursion_blocked"


# ------------------------------------------------------------ NEDLE2 surface §5.28
def test_nedle2_exposes_exactly_one_skill_tool():
    from director import tools as tool_catalog
    names = [getattr(t, "__name__", "") for t in tool_catalog.TOOLS]
    skill_tools = [n for n in names if "skill" in n]
    assert skill_tools == ["skill_execute"], "skills must not flood the router's tool list"


def test_skill_tool_maps_to_a_generic_capability():
    from director import tools as tool_catalog
    spec = tool_catalog.TOOL_TO_TASK["skill_execute"]
    assert spec["capability"] == "skill.execute"
    assert spec["type"] == "computer_action"
    assert spec["arg"] == "goal"


def test_worker_routes_skill_execute_to_the_skill_service(app):
    out = app.skills.worker(CallContext(person_id="owner"),
                            {"type": "computer_action", "capability": "skill.execute",
                             "params": {"goal": "reconcile the quarterly ledgers"}})
    assert out["capability"] == "skill.execute"
    assert out["error_code"] == "no_skill_match"


def test_worker_routes_skill_search(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    out = app.skills.worker(CallContext(person_id="owner"),
                            {"type": "computer_action", "capability": "skill.search",
                             "params": {"goal": "write a note"}})
    assert out["ok"] is True
    assert out["count"] >= 1


# ------------------------------------------------------------- facade surface
def test_service_status_reports_registry_and_teaching(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    status = app.skills.status()
    assert status["skills"]["skills"] == 1
    assert status["teaching"]["active"] is False
    assert status["selection_threshold"] == 0.45


def test_service_list_and_versions_agree_with_the_registry(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    app.skills.registry.update("write-note", {"description": "v2"})
    versions = app.skills.versions("write-note")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["active"] is True


def test_service_rollback_is_reachable_from_the_facade(app, tmp_path):
    skill = write_skill(tmp_path)
    app.skills.registry.register(skill, activate=True)
    app.skills.registry.update("write-note", {"description": "v2"})
    app.skills.registry.set_active_version("write-note", 2)
    outcome = app.skills.rollback("write-note")
    assert outcome["ok"] is True and outcome["rolled_back_to"] == 1
    assert app.skills.registry.active_version("write-note") == 1


def test_service_duplicate_report(app, tmp_path):
    skill = write_skill(tmp_path, skill_id="write-note")
    app.skills.registry.register(skill, activate=True)
    twin = write_skill(tmp_path, skill_id="write-note-copy")
    app.skills.registry.register(twin)
    report = app.skills.duplicates("write-note")
    assert report["ok"] is True
    assert any(d["skill_id"] == "write-note-copy" for d in report["duplicates"])
