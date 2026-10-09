"""Phase 5 — skill model + registry (§5.1-§5.4, §5.8, §5.19-§5.26, §5.29).

These tests are deterministic and never touch the network or the real desktop.
"""
from __future__ import annotations

import time

import pytest

from core.contracts import CallContext
from skills.models import (ALLOWED_TRANSITIONS, Skill, SkillScope, SkillStatus,
                           SkillStep, validate_skill)
from skills.registry import MIN_SELECTION_SCORE, SkillRegistry


def make_skill(skill_id: str = "demo-note", version: int = 1, *,
               status: str = SkillStatus.ACTIVE.value, name: str = "demo note",
               steps=None, capabilities=None, tags=None, scope: str = SkillScope.USER.value,
               inputs=None, verification=None) -> Skill:
    if steps is None:
        steps = [SkillStep(capability="files.write",
                           params={"path": "${note_path}", "content": "${text}"})]
    if verification is None:
        verification = [{"check": "file_exists", "value": "${note_path}"}]
    if inputs is None:
        inputs = {"note_path": {"type": "string", "required": True},
                  "text": {"type": "string", "required": True}}
    return Skill(
        skill_id=skill_id, name=name, version=version, status=status,
        scope=scope, tags=tags or [], steps=steps, inputs=inputs, verification=verification,
        required_capabilities=capabilities or ["files.write"],
    )


# ------------------------------------------------------------------ schema §5.1
def test_skill_schema_roundtrip_preserves_every_field():
    skill = make_skill(tags=["notepad", "note"])
    skill.environment = {"platform": "windows"}
    skill.examples = [{"goal": "write a dated note"}]
    restored = Skill.from_dict(skill.to_dict())
    assert restored.to_dict() == skill.to_dict()


def test_skill_rejects_invalid_id_and_missing_verification():
    bad_id = make_skill(skill_id="Bad_ID")
    assert any("skill_id" in p for p in validate_skill(bad_id))
    no_verification = make_skill()
    no_verification.verification = []
    assert any("verification" in p for p in validate_skill(no_verification))


def test_skill_requires_at_least_one_step():
    empty = make_skill(steps=[])
    assert any("step" in p for p in validate_skill(empty))


def test_skill_rejects_unknown_failure_policy():
    step = SkillStep(capability="files.write", on_failure="explode")
    assert any("on_failure" in p for p in validate_skill(make_skill(steps=[step])))


# -------------------------------------------------------------- variables §5.17
def test_variables_are_substituted_from_inputs():
    skill = make_skill()
    variables = skill.all_variables({"note_path": r"C:\tmp\n.txt", "text": "hello"})
    substituted = skill.substitute(skill.steps[0].params, variables)
    assert substituted["path"] == r"C:\tmp\n.txt"
    assert substituted["content"] == "hello"


def test_unresolved_placeholders_are_detected():
    skill = make_skill()
    variables = skill.all_variables({"note_path": r"C:\tmp\n.txt"})
    assert "text" in skill.unresolved_placeholders(variables)


def test_missing_required_inputs_are_reported():
    skill = make_skill()
    assert skill.missing_required_inputs({}) == ["note_path", "text"]
    assert skill.missing_required_inputs({"note_path": "x", "text": "y"}) == []


def test_input_defaults_satisfy_required_inputs():
    skill = make_skill(inputs={"note_path": {"type": "string", "required": True},
                               "text": {"type": "string", "default": "untitled"}})
    assert skill.missing_required_inputs({"note_path": "x"}) == []


# ------------------------------------------------------------- registry §5.2
def test_registry_register_get_and_list(app):
    registry = SkillRegistry(app.db, audit=app.audit)
    outcome = registry.register(make_skill())
    assert outcome["ok"] is True
    assert registry.get("demo-note").name == "demo note"
    # a fresh candidate is not active yet, so the default listing (active only) hides it
    assert registry.list() == []
    assert [s.skill_id for s in registry.list(active_only=False)] == ["demo-note"]
    # once activated it appears in the default listing
    registry.set_active_version("demo-note", 1)
    assert [s.skill_id for s in registry.list()] == ["demo-note"]


def test_registry_refuses_duplicate_key(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill())
    again = registry.register(make_skill())
    assert again["ok"] is False and "already exists" in again["error"]


def test_registry_refuses_invalid_skill(app):
    registry = SkillRegistry(app.db)
    bad = make_skill()
    bad.verification = []
    outcome = registry.register(bad)
    assert outcome["ok"] is False and outcome["problems"]


# ------------------------------------------------------------- versioning §5.19
def test_update_creates_a_new_version_by_default(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    outcome = registry.update("demo-note", {"description": "second revision"})
    assert outcome["ok"] is True and outcome["new_version"] is True
    assert registry.next_version("demo-note") == 3
    assert registry.get("demo-note", 2).description == "second revision"
    # the previous version is untouched — no silent overwrite
    assert registry.get("demo-note", 1).description == ""


def test_update_in_place_only_when_explicitly_requested(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1))
    outcome = registry.update("demo-note", {"description": "in place"}, as_new_version=False)
    assert outcome["ok"] is True and outcome["new_version"] is False
    assert registry.get("demo-note", 1).description == "in place"


def test_new_version_records_derived_from_provenance(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1))
    registry.update("demo-note", {"description": "v2"})
    assert registry.get("demo-note", 2).provenance["derived_from"] == "demo-note@1"


def test_rollback_moves_active_to_previous_version(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    registry.update("demo-note", {"description": "v2"})
    registry.set_active_version("demo-note", 2)
    assert registry.active_version("demo-note") == 2
    outcome = registry.rollback("demo-note")
    assert outcome["ok"] is True and outcome["rolled_back_to"] == 1
    assert registry.active_version("demo-note") == 1


def test_rollback_to_explicit_version(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    registry.update("demo-note", {"description": "v2"})
    registry.update("demo-note", {"description": "v3"})
    registry.set_active_version("demo-note", 3)
    outcome = registry.rollback("demo-note", to_version=1)
    assert outcome["rolled_back_to"] == 1


def test_rollback_without_previous_version_is_refused(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    assert registry.rollback("demo-note")["ok"] is False


def test_new_version_resets_statistics(app):
    registry = SkillRegistry(app.db)
    skill = make_skill(version=1)
    skill.success_count = 7
    skill.failure_count = 2
    registry.register(skill, activate=True)
    registry.update("demo-note", {"description": "v2"})
    fresh = registry.get("demo-note", 2)
    assert (fresh.success_count, fresh.failure_count) == (0, 0)


# ----------------------------------------------------- lifecycle machine §5.19
def test_status_transition_machine_blocks_illegal_moves(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(status=SkillStatus.CANDIDATE.value))
    # candidate -> validating is legal
    assert registry.set_status("demo-note", SkillStatus.VALIDATING.value)["ok"] is True
    assert registry.set_status("demo-note", SkillStatus.ACTIVE.value)["ok"] is True
    # archived is terminal
    assert registry.set_status("demo-note", SkillStatus.ARCHIVED.value)["ok"] is True
    blocked = registry.set_status("demo-note", SkillStatus.ACTIVE.value)
    assert blocked["ok"] is False and "illegal transition" in blocked["error"]


def test_archiving_the_active_version_clears_the_active_flag(app):
    """A-052: a withdrawn version must never keep the active flag."""
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    registry.register(make_skill(version=2), activate=True)
    assert registry.active_version("demo-note") == 2
    registry.set_status("demo-note", SkillStatus.ARCHIVED.value, version=2)
    assert registry.active_version("demo-note") == 1
    assert registry.get("demo-note", 2).status == SkillStatus.ARCHIVED.value


def test_deprecated_version_can_be_reactivated_explicitly(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1), activate=True)
    registry.set_status("demo-note", SkillStatus.DEPRECATED.value)
    assert registry.active_version("demo-note") is None
    registry.set_active_version("demo-note", 1)
    assert registry.active_version("demo-note") == 1


def test_delete_archives_by_default_and_hard_deletes_on_request(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(version=1))
    assert registry.delete("demo-note")["ok"] is True
    assert registry.get("demo-note", 1).status == SkillStatus.ARCHIVED.value
    assert registry.delete("demo-note", archive_only=False)["deleted"] is True
    assert registry.get("demo-note", 1) is None


# --------------------------------------------------------------- selection §5.29
def test_search_ranks_a_matching_skill_above_the_threshold(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["notepad", "note", "write"]), activate=True)
    matches = registry.search("write a dated note in notepad",
                              context={"capabilities": ["files.write"]})
    assert matches and matches[0].skill.skill_id == "notepad-dated-note"
    assert matches[0].score >= MIN_SELECTION_SCORE


def test_selection_refuses_a_skill_with_no_intent_overlap(app):
    """A-051: baseline points alone must never clear the threshold."""
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["notepad", "note"]), activate=True)
    matches = registry.search("play some jazz music on spotify",
                              context={"capabilities": ["files.write"]})
    assert matches
    assert matches[0].score < MIN_SELECTION_SCORE
    assert registry.select("play some jazz music on spotify",
                           context={"capabilities": ["files.write"]}) is None


def test_selection_rejects_an_incompatible_skill(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["note"], capabilities=["files.write"]), activate=True)
    match = registry.select("write a note", context={"capabilities": ["system.volume.set"]})
    assert match is None


def test_selection_ignores_candidates_and_archived_versions(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["note"], status=SkillStatus.CANDIDATE.value))
    assert registry.select("write a note", context={"capabilities": ["files.write"]}) is None


def test_selection_prefers_the_proven_skill(app):
    registry = SkillRegistry(app.db)
    good = make_skill(skill_id="notepad-dated-note", name="notepad dated note", tags=["note"])
    good.success_count, good.failure_count = 40, 1
    flaky = make_skill(skill_id="notepad-note-flaky", name="notepad note flaky", tags=["note"])
    flaky.success_count, flaky.failure_count = 2, 12
    registry.register(good, activate=True)
    registry.register(flaky, activate=True)
    match = registry.select("write a note", context={"capabilities": ["files.write"]})
    assert match is not None and match.skill.skill_id == "notepad-dated-note"


# ------------------------------------------------------------- duplicates §5.21
def test_duplicate_detection_finds_a_near_identical_skill(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["note", "notepad"]))
    twin = make_skill(skill_id="notepad-dated-note-copy", name="notepad dated note",
                      tags=["note", "notepad"])
    duplicates = registry.find_duplicates(twin)
    assert duplicates and duplicates[0]["skill_id"] == "notepad-dated-note"


def test_duplicate_detection_ignores_unrelated_skills(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="notepad-dated-note", name="notepad dated note",
                                 tags=["note"]))
    other = make_skill(skill_id="volume-lower", name="lower the volume",
                       tags=["volume", "audio"],
                       steps=[SkillStep(capability="system.volume.down", params={})],
                       capabilities=["system.volume.down"],
                       inputs={}, verification=[{"check": "step_verified"}])
    assert registry.find_duplicates(other) == []


# --------------------------------------------------------------- statistics §5.20
def test_statistics_only_count_verified_outcomes(app):
    registry = SkillRegistry(app.db)
    skill = make_skill()
    registry.register(skill, activate=True)
    registry.record_run(skill, success=True, verified=True, latency_ms=120)
    registry.record_run(skill, success=False, verified=False, latency_ms=80,
                        failure={"step": "files.write", "error": "denied"})
    stored = registry.get("demo-note", 1)
    assert stored.success_count == 1
    assert stored.failure_count == 1
    assert stored.verification_failures == 1
    assert stored.success_rate == 0.5
    assert stored.average_latency_ms == 100


def test_takeover_is_recorded_as_statistics_not_a_success(app):
    registry = SkillRegistry(app.db)
    skill = make_skill()
    registry.register(skill, activate=True)
    updated = registry.record_takeover(skill, "owner finished the save dialog")
    assert updated.user_takeovers == 1
    assert updated.success_count == 0


def test_known_failures_are_bounded(app):
    registry = SkillRegistry(app.db)
    skill = make_skill()
    registry.register(skill, activate=True)
    for index in range(30):
        registry.record_run(skill, success=False, verified=False,
                            failure={"step": str(index)})
    stored = registry.get("demo-note", 1)
    assert len(stored.known_failures) <= 20


def test_registry_stats_report_counts_by_status(app):
    registry = SkillRegistry(app.db)
    registry.register(make_skill(skill_id="one-note", name="one", version=1),
                      activate=True)
    registry.register(make_skill(skill_id="two-note", name="two", version=1,
                                 status=SkillStatus.CANDIDATE.value))
    stats = registry.stats()
    assert stats["skills"] == 2
    assert stats["by_status"].get(SkillStatus.ACTIVE.value) == 1
    assert stats["by_status"].get(SkillStatus.CANDIDATE.value) == 1


# ------------------------------------------------------------------ scope §5.22
def test_scope_affinity_prefers_the_matching_project_skill(app):
    registry = SkillRegistry(app.db)
    project = make_skill(skill_id="build-project-note", name="build project note",
                         tags=["build"], scope=SkillScope.PROJECT.value)
    project.scope_ref = "genie"
    global_skill = make_skill(skill_id="build-generic-note", name="build generic note",
                              tags=["build"], scope=SkillScope.GLOBAL.value)
    registry.register(project, activate=True)
    registry.register(global_skill, activate=True)
    match = registry.select("build", context={"capabilities": ["files.write"],
                                              "project": "genie"})
    assert match is not None and match.skill.skill_id == "build-project-note"


def test_compatibility_lists_missing_requirements(app):
    registry = SkillRegistry(app.db)
    skill = make_skill(capabilities=["files.write"], tags=["note"])
    skill.required_plugins = ["vscode"]
    report = registry.compatibility(skill, capabilities=["files.write"], plugins=[])
    assert report["compatible"] is False
    assert report["missing_plugins"] == ["vscode"]
