"""High-level mission planner contracts (spec section 9).

The planner must produce real decomposition with acceptance criteria - not a
generic one-step echo of the objective.
"""
from __future__ import annotations

from missions.planner import (
    MissionPlanner, classify_objective,
    KIND_CODING, KIND_RESEARCH, KIND_CONTENT, KIND_COMPUTER, KIND_GENERAL,
)


def test_objective_kinds_are_classified():
    assert classify_objective("Fix the failing pytest in the repo") == KIND_CODING
    assert classify_objective("Research which model is cheapest") == KIND_RESEARCH
    assert classify_objective("Draft a blog post about it") == KIND_CONTENT
    assert classify_objective("Open Chrome and screenshot the page") == KIND_COMPUTER
    assert classify_objective("") == KIND_GENERAL


def test_writing_a_test_is_coding_not_content():
    # "write" appears in both templates; coding must win to avoid a plan that
    # treats a unit test as a piece of prose.
    assert classify_objective("write a test for the login endpoint") == KIND_CODING


def test_coding_plan_has_ordered_dependency_chain():
    plan = MissionPlanner().plan("Fix the bug in the parser")
    assert plan.kind == KIND_CODING
    assert len(plan.tasks) >= 4, "a coding plan needs more than 'do the thing'"

    ids = [t.task_id for t in plan.tasks]
    assert len(ids) == len(set(ids)), "task ids must be unique"

    # strictly ordered: each task depends on the one before it
    for i, task in enumerate(plan.tasks):
        expected = [] if i == 0 else [plan.tasks[i - 1].task_id]
        assert task.depends_on == expected, f"task {task.task_id} deps wrong"

    # implementation must precede verification. The chain is linear, so this
    # holds transitively (verify -> test -> implement), not as a direct edge.
    order = [t.task_id for t in plan.tasks]
    impl_i = next(i for i, tid in enumerate(order) if "implement" in tid)
    test_i = next(i for i, tid in enumerate(order) if "test" in tid)
    verify_i = next(i for i, tid in enumerate(order) if "verify" in tid)
    assert impl_i < test_i < verify_i


def test_every_task_has_acceptance_criteria():
    for objective in ("Refactor the module", "Research the options",
                      "Write a newsletter", "Automate the desktop task",
                      "Something vague"):
        plan = MissionPlanner().plan(objective)
        for task in plan.tasks:
            assert task.completion_criteria.strip(), \
                f"{objective}/{task.task_id} has no completion criteria"
            assert len(task.completion_criteria) > 20, \
                f"{objective}/{task.task_id} criteria too vague"


def test_plans_declare_deliverables():
    for objective in ("Fix the bug", "Research X", "Write a post",
                      "Open the app", "Do a thing"):
        plan = MissionPlanner().plan(objective)
        assert plan.deliverables, f"{objective} has no deliverables"


def test_research_plan_requires_citations():
    plan = MissionPlanner().plan("Research the best approach")
    criteria = " ".join(t.completion_criteria for t in plan.tasks).lower()
    assert "source" in criteria, "research must end with source references"


def test_computer_plan_dry_runs_before_executing():
    plan = MissionPlanner().plan("Click through the setup wizard")
    ids = [t.task_id for t in plan.tasks]
    dry = next(i for i, t in enumerate(plan.tasks) if "dry" in t.task_id)
    ex = next(i for i, t in enumerate(plan.tasks) if "execute" in t.task_id)
    assert dry < ex, "dry run must precede real execution"


def test_high_complexity_marks_review():
    plan = MissionPlanner().plan("Refactor this module", complexity=0.9)
    assert any(t.requires_review for t in plan.tasks)


def test_plan_is_serialisable():
    import json
    d = MissionPlanner().plan("Fix the bug").to_dict()
    json.dumps(d)
    assert set(d) >= {"objective", "kind", "deliverables", "tasks"}
    for t in d["tasks"]:
        assert set(t) >= {"task_id", "objective", "completion_criteria",
                          "depends_on", "required_role", "requires_review"}


def test_kind_can_be_forced():
    plan = MissionPlanner().plan("anything", kind=KIND_RESEARCH)
    assert plan.kind == KIND_RESEARCH


def test_empty_objective_still_yields_a_usable_plan():
    plan = MissionPlanner().plan("")
    assert plan.tasks and all(t.task_id for t in plan.tasks)
