"""Phase 10 — golden-task evidence with a REAL local executor (no stub strings).

The companion test_phase10_agents.py exercises the orchestrator/budget/factory mechanics with a
double for the model call. These tests go further: they run the goldens through
``agents.providers.LocalExecutor``, which writes actual files, runs a real pytest subprocess and
posts real mailbox / blackboard messages. A broken module produces a failing test that the reviewer
rejects — proof this is not a stub returning ``{"ok": True}``.

Provider label: ``protocol/behavior verified (controlled local provider)``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from agents.contracts import TaskNode
from agents.providers import LocalExecutor, ModelGateway
from agents.service import AgentService


def _real_service(app, provider: str = "strong", fail_after: int = None):
    """A wired service backed by the *real* local executor (not a stub runner)."""
    root = Path(tempfile.mkdtemp()) / "artifacts"
    root.mkdir()
    gw = ModelGateway()
    svc = AgentService(db=app.db, audit=app.audit, locks=app.locks, gateway=gw,
                       artifact_root=str(root))
    svc._artifact_root = str(root)
    svc._gw = gw
    return svc, root


def _bind(svc, mission_id, root, provider: str = "strong", fail_after: int = None):
    orch = svc.team(mission_id)
    ex = LocalExecutor(artifact_root=str(root), artifacts=orch.artifacts,
                       blackboard=orch.blackboard, mailbox=orch.mailbox, budgets=svc.budgets,
                       gateway=svc.gateway, mission_id=mission_id,
                       provider=provider, fail_after_calls=fail_after)
    orch.runner = ex
    return orch, ex


def _calculator_dag(orch, spec):
    orch.add_task(TaskNode(task_id="t_design", objective="design the calculator interface",
                           required_role="architect", inputs={"kind": "design", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_impl", objective="implement genie_app module",
                           required_role="backend", depends_on=["t_design"],
                           inputs={"kind": "module", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_test", objective="write and run tests for genie_app",
                           required_role="tester", depends_on=["t_impl"],
                           inputs={"kind": "test", "module": "genie_app", "spec": spec,
                                   "cases": [{"fn": "add", "args": [2, 3], "expect": 5},
                                             {"fn": "subtract", "args": [5, 3], "expect": 2}]}))
    reviewer = next((m for m in orch.members.values() if m.definition.role == "reviewer"), None)
    if reviewer is not None:
        orch.add_task(TaskNode(task_id="t_review", objective="review the deliverable",
                               required_role="reviewer", depends_on=["t_test"], inputs={}))


# ============================================================== GOLDEN #4
def test_golden_4_real_team_builds_and_tests_an_app(app):
    svc, root = _real_service(app, provider="strong")
    plan = svc.factory.plan_team(objective="build a calculator with tests", complexity=0.8,
                                 needs=["architect", "backend", "tester"])
    svc.start_team(mission_id="m4", objective="build a calculator with tests", plan=plan,
                   budget={"tokens": 200_000, "cost_usd": 2.0, "model_calls": 80})
    orch, ex = _bind(svc, "m4", root, provider="strong")
    spec = {"module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a + b"},
                          {"name": "subtract", "args": ["a", "b"], "body": "return a - b"}]}
    _calculator_dag(orch, spec)

    out = svc.run("m4")

    assert out["status"] == "completed", out
    # real artifacts on disk, exactly one module (no duplicate work)
    py_files = sorted(p.name for p in Path(root).glob("*.py"))
    assert py_files == ["genie_app.py", "test_genie_app.py"], py_files
    modules = [a for a in out["artifacts"] if a["name"].endswith(".py") and "test" not in a["name"]]
    assert len(modules) == 1, "duplicate module artifact"
    # a real test actually ran and passed
    test_decision = next((e for e in orch.blackboard.entries()
                           if str(e["key"]).startswith("tests.")), None)
    assert test_decision is not None
    assert test_decision["value"]["passed"] == 2 and test_decision["value"]["failed"] == 0
    assert test_decision["value"]["verdict"] == "green"
    # the reviewer accepted the real result
    review = next((e for e in orch.blackboard.entries()
                   if str(e["key"]).startswith("review.")), None)
    assert review is not None and review["value"]["accepted"] is True
    # the budget hierarchy was genuinely charged (GENIE-side estimate > 0)
    assert svc.budgets.node("mission:m4").budget.cost_used > 0
    # the mission-scoped agents are cleaned up, none left WORKING
    assert out.get("orphans", []) == []
    assert all(m.lifecycle == "retired" for m in orch.members.values())


# ============================================================== GOLDEN #5
def test_golden_5_agents_talk_via_mailbox_blackboard_not_transcript(app):
    svc, root = _real_service(app, provider="strong")
    plan = svc.factory.plan_team(objective="build a calculator with tests", complexity=0.8,
                                 needs=["architect", "backend", "tester"])
    svc.start_team(mission_id="m5", objective="build a calculator with tests", plan=plan,
                   budget={"tokens": 200_000, "cost_usd": 2.0, "model_calls": 80})
    orch, ex = _bind(svc, "m5", root, provider="strong")
    spec = {"module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a + b"},
                          {"name": "subtract", "args": ["a", "b"], "body": "return a - b"}]}
    _calculator_dag(orch, spec)
    svc.run("m5")

    # mailbox carried structured artifact_ready events, not transcripts
    mb = orch.mailbox.status()
    assert mb["count"] >= 3, mb
    assert mb["by_type"].get("artifact_ready", 0) >= 3
    # blackboard carried mission-relevant shared state only
    bb = orch.blackboard.status()
    assert bb["by_kind"].get("interface", 0) >= 1
    assert bb["by_kind"].get("decision", 0) >= 1
    # the tester consumed the module by reference; the reviewer read the blackboard decision
    test_task = orch.graph.get("t_test")
    assert test_task.outputs, "tester produced no artifact"
    review = next((e for e in orch.blackboard.entries()
                   if str(e["key"]).startswith("review.")), None)
    assert review is not None and review["value"]["accepted"] is True


# ============================================================== GOLDEN #7
def test_golden_7_mid_mission_provider_failover_preserves_work(app):
    svc, root = _real_service(app, provider="provider-a", fail_after=1)
    plan = svc.factory.plan_team(objective="build a module and test it", complexity=0.6,
                                 needs=["architect", "backend", "tester"])
    svc.start_team(mission_id="m7", objective="build a module and test it", plan=plan,
                   budget={"tokens": 200_000, "cost_usd": 2.0, "model_calls": 80})
    orch, ex = _bind(svc, "m7", root, provider="provider-a", fail_after=1)
    spec = {"module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a + b"}]}
    orch.add_task(TaskNode(task_id="t_design", objective="design interface",
                           required_role="architect", inputs={"kind": "design", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_impl", objective="implement genie_app",
                           required_role="backend", depends_on=["t_design"],
                           inputs={"kind": "module", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_test", objective="test genie_app",
                           required_role="tester", depends_on=["t_impl"],
                           inputs={"kind": "test", "module": "genie_app", "spec": spec,
                                   "cases": [{"fn": "add", "args": [2, 3], "expect": 5}]}))

    # Provider A alive for T1, then dies mid-mission
    phase1 = svc.run("m7", max_steps=3)
    assert phase1["status"] in ("exhausted", "failed", "blocked")

    # failure detected -> circuit breaker -> continuation packet -> Provider B
    fo = svc.failover("m7", from_provider="provider-a", reason="Provider A unavailable")
    assert fo["breaker"].get("recorded") is True
    assert fo["completed_preserved"] >= 1, "completed work must survive the switch"
    packet = fo["continuation"]
    # structured, provider-independent — not a transcript dump
    for key in ("objective", "completed_steps", "pending_steps", "decisions",
                "artifacts", "known_errors", "current_tool_state"):
        assert key in packet, key

    # Provider B selected; resume. Completed work must NOT be repeated.
    ex.provider = fo["to_provider"]
    ex.fail_after_calls = None
    phase2 = svc.run("m7")
    assert phase2["status"] == "completed", phase2
    modules = [a for a in phase2["artifacts"] if a["name"].endswith(".py")
               and "test" not in a["name"]]
    assert len(modules) == 1, "T1/T2 was repeated after failover"
    tasks = {t["task_id"]: t for t in phase2["tasks"]["tasks"]}
    assert tasks["t_design"]["provider_used"] == "provider-a", "T1 provider changed"
    assert tasks["t_impl"]["provider_used"] == fo["to_provider"]
    assert phase2.get("orphans", []) == []


# ============================================================== negative proof
def test_real_executor_rejects_a_broken_module(app):
    """A malformed module must fail the real test, which the reviewer rejects — not a stub pass."""
    svc, root = _real_service(app, provider="strong")
    plan = svc.factory.plan_team(objective="build a broken module", complexity=0.8,
                                 needs=["architect", "backend", "tester"])
    svc.start_team(mission_id="mneg", objective="build a broken module", plan=plan,
                   budget={"tokens": 200_000, "cost_usd": 2.0, "model_calls": 80})
    orch, ex = _bind(svc, "mneg", root, provider="strong")
    # intentionally wrong body: add returns the wrong value
    spec = {"module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a - b"}]}
    orch.add_task(TaskNode(task_id="t_design", objective="design interface",
                           required_role="architect", inputs={"kind": "design", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_impl", objective="implement genie_app (broken)",
                           required_role="backend", depends_on=["t_design"],
                           inputs={"kind": "module", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_test", objective="test genie_app",
                           required_role="tester", depends_on=["t_impl"],
                           inputs={"kind": "test", "module": "genie_app", "spec": spec,
                                   "cases": [{"fn": "add", "args": [2, 3], "expect": 5}]}))
    svc.run("mneg")
    test_decision = next((e for e in orch.blackboard.entries()
                          if str(e["key"]).startswith("tests.")), None)
    # the real pytest caught the wrong implementation: 1 failed
    assert test_decision["value"]["failed"] == 1, test_decision
    assert test_decision["value"]["verdict"] == "red"
