"""P5 Spec Kit gap: requirements -> plan -> tasks -> implement -> validate.

Deterministic. Gating is mechanical: an uncovered requirement or an incomplete
task blocks progress rather than being assumed to pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db import Database               # noqa: E402
from specs.pipeline import PHASES, SpecPipeline  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def pipe(db):
    return SpecPipeline(db)


@pytest.fixture()
def spec(pipe):
    s = pipe.create("Add dark mode", mission_id="mis_1")
    r1 = pipe.add_requirement(s["spec_id"], "User can toggle dark mode")
    r2 = pipe.add_requirement(s["spec_id"], "Choice persists across restarts")
    s["r1"] = r1["requirement_id"]
    s["r2"] = r2["requirement_id"]
    return s


# ------------------------------------------------------------------ creation
def test_spec_starts_in_specify_phase(pipe, spec):
    assert pipe.get(spec["spec_id"])["phase"] == "specify"


def test_requirements_are_recorded(pipe, spec):
    reqs = pipe.requirements(spec["spec_id"])
    assert len(reqs) == 2
    assert "dark mode" in reqs[0]["text"]


def test_phase_order_is_stable():
    assert PHASES == ("specify", "plan", "tasks", "implement", "validate")


# -------------------------------------------------------------------- gating
def test_cannot_skip_to_tasks_without_requirements(pipe):
    s = pipe.create("empty")
    gate = pipe.can_enter(s["spec_id"], "tasks")
    assert gate["ok"] is False
    assert "no requirements" in gate["reason"]


def test_uncovered_requirement_blocks_tasks_phase(pipe, spec):
    pipe.add_task(spec["spec_id"], "build toggle",
                  requirement_ids=[spec["r1"]])
    gate = pipe.can_enter(spec["spec_id"], "tasks")
    assert gate["ok"] is False
    assert "no task" in gate["reason"]
    assert gate["uncovered"] == [spec["r2"]]


def test_all_requirements_covered_allows_tasks_phase(pipe, spec):
    pipe.add_task(spec["spec_id"], "build toggle", requirement_ids=[spec["r1"]])
    pipe.add_task(spec["spec_id"], "persist choice", requirement_ids=[spec["r2"]])
    assert pipe.can_enter(spec["spec_id"], "tasks")["ok"] is True


def test_incomplete_tasks_block_validate(pipe, spec):
    t = pipe.add_task(spec["spec_id"], "build toggle",
                      requirement_ids=[spec["r1"]])
    pipe.add_task(spec["spec_id"], "persist", requirement_ids=[spec["r2"]])
    pipe.advance(spec["spec_id"], "tasks")
    gate = pipe.can_enter(spec["spec_id"], "validate")
    assert gate["ok"] is False
    assert "not complete" in gate["reason"]


def test_unknown_phase_is_rejected(pipe, spec):
    assert pipe.can_enter(spec["spec_id"], "wing-it")["ok"] is False


def test_cannot_advance_gate_blocks(pipe, spec):
    out = pipe.advance(spec["spec_id"], "tasks")
    assert out["ok"] is False
    assert pipe.get(spec["spec_id"])["phase"] == "specify", \
        "a blocked gate must not move the pipeline"


# ---------------------------------------------------------------- advancing
def test_advance_moves_phase(pipe, spec):
    pipe.add_task(spec["spec_id"], "a", requirement_ids=[spec["r1"]])
    pipe.add_task(spec["spec_id"], "b", requirement_ids=[spec["r2"]])
    assert pipe.advance(spec["spec_id"], "tasks")["ok"] is True
    assert pipe.get(spec["spec_id"])["phase"] == "tasks"


# ----------------------------------------------------------------- artifacts
def test_artifacts_are_stored_per_phase(pipe, spec):
    pipe.set_artifact(spec["spec_id"], "specify", "# spec\n...")
    pipe.set_artifact(spec["spec_id"], "plan", "step 1, step 2")
    arts = pipe.artifacts(spec["spec_id"])
    assert [a["phase"] for a in arts] == ["specify", "plan"]


def test_artifact_phase_must_be_valid(pipe, spec):
    with pytest.raises(ValueError):
        pipe.set_artifact(spec["spec_id"], "nonsense", "x")


# --------------------------------------------------------------- validation
def test_validate_passes_when_every_requirement_is_satisfied(pipe, spec):
    t1 = pipe.add_task(spec["spec_id"], "build toggle",
                       requirement_ids=[spec["r1"]])
    t2 = pipe.add_task(spec["spec_id"], "persist", requirement_ids=[spec["r2"]])
    pipe.complete_task(t1["task_id"], outcome="ok", evidence="unit test")
    pipe.complete_task(t2["task_id"], outcome="ok", evidence="restart test")
    res = pipe.validate(spec["spec_id"])
    assert res["ok"] is True
    assert res["satisfied"] == res["total"] == 2


def test_validate_fails_when_a_task_failed(pipe, spec):
    t1 = pipe.add_task(spec["spec_id"], "build toggle",
                       requirement_ids=[spec["r1"]])
    t2 = pipe.add_task(spec["spec_id"], "persist", requirement_ids=[spec["r2"]])
    pipe.complete_task(t1["task_id"], outcome="ok")
    pipe.complete_task(t2["task_id"], outcome="failed")
    res = pipe.validate(spec["spec_id"])
    assert res["ok"] is False
    assert res["satisfied"] == 1


def test_validate_fails_on_uncovered_requirement(pipe, spec):
    t = pipe.add_task(spec["spec_id"], "build toggle",
                      requirement_ids=[spec["r1"]])
    pipe.complete_task(t["task_id"], outcome="ok")
    res = pipe.validate(spec["spec_id"])
    assert res["ok"] is False
    assert any(c["reason"] == "no task" for c in res["coverage"])


def test_validate_without_requirements_fails(pipe):
    s = pipe.create("bare")
    assert pipe.validate(s["spec_id"])["ok"] is False


def test_validation_history_is_appended_not_overwritten(pipe, spec):
    t1 = pipe.add_task(spec["spec_id"], "a", requirement_ids=[spec["r1"]])
    t2 = pipe.add_task(spec["spec_id"], "b", requirement_ids=[spec["r2"]])
    pipe.validate(spec["spec_id"])                      # fails
    pipe.complete_task(t1["task_id"], outcome="ok")
    pipe.complete_task(t2["task_id"], outcome="ok")
    pipe.validate(spec["spec_id"])                      # passes
    hist = pipe.validations(spec["spec_id"])
    assert len(hist) == 2
    assert hist[0]["ok"] == 0 and hist[1]["ok"] == 1
