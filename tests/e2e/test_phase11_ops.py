"""Phase 11D — Operator Console / observability surface (supporting infrastructure).

The console reads real state and drives real control primitives; every action lands in the
tamper-evident audit log. These tests use the **real** local executor (writes files, runs a real
pytest subprocess), so the console is proven against a mission that genuinely completes.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agents.contracts import TaskNode
from agents.providers import LocalExecutor, ModelGateway
from agents.service import AgentService


def _service(app):
    """A wired service backed by the real local executor, with audit enabled."""
    root = Path(tempfile.mkdtemp()) / "artifacts"
    root.mkdir()
    return AgentService(db=app.db, audit=app.audit, locks=app.locks,
                        gateway=ModelGateway(), artifact_root=str(root)), root


def _mission(svc, root, mission_id="m-ops"):
    plan = svc.factory.plan_team(objective="build a calculator", complexity=0.8,
                                 needs=["architect", "backend", "tester"])
    svc.start_team(mission_id=mission_id, objective="build a calculator", plan=plan,
                   budget={"tokens": 200_000, "cost_usd": 2.0, "model_calls": 80})
    orch = svc.team(mission_id)
    orch.runner = LocalExecutor(
        artifact_root=str(root), artifacts=orch.artifacts, blackboard=orch.blackboard,
        mailbox=orch.mailbox, budgets=svc.budgets, gateway=svc.gateway,
        mission_id=mission_id, provider="strong")
    spec = {"module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a + b"},
                          {"name": "subtract", "args": ["a", "b"], "body": "return a - b"}]}
    orch.add_task(TaskNode(task_id="t_design", objective="design", required_role="architect",
                           inputs={"kind": "design", "spec": spec}))
    orch.add_task(TaskNode(task_id="t_impl", objective="implement", required_role="backend",
                           depends_on=["t_design"], inputs={"kind": "module", "spec": spec}))
    return orch, spec


# ------------------------------------------------------------------ dashboard
def test_dashboard_reflects_a_real_completed_mission(app):
    svc, root = _service(app)
    orch, _ = _mission(svc, root, "m-dash")
    out = svc.run("m-dash")
    assert out["status"] == "completed", out

    dash = svc.ops_dashboard()
    assert dash["global"]["teams"] == 1
    team = next(t for t in dash["teams"] if t["mission_id"] == "m-dash")
    assert team["tasks"]["total"] == 2
    assert team["tasks"]["done"] == 2, team["tasks"]
    assert team["orphans"] == []
    assert team["team_size"] >= 3
    # budget was genuinely charged and is genuinely reported
    assert team["budget_remaining"]["cost_usd"] < 2.0
    assert "experience" in dash["global"]


# --------------------------------------------------------------------- control
def test_control_actions_mutate_real_state(app):
    svc, root = _service(app)
    orch, _ = _mission(svc, root, "m-ctl")
    svc.run("m-ctl")

    # throttle actually lowers the concurrency cap on the live orchestrator
    r = svc.ops_control("throttle", mission_id="m-ctl", max_concurrency=2)
    assert r["ok"] is True and orch.max_concurrency == 2

    # set_budget_cap actually changes what is left to spend
    before = svc.budgets.node("mission:m-ctl").to_dict()["remaining"]["cost_usd"]
    cap = svc.ops_control("set_budget_cap", mission_id="m-ctl", cost_usd=0.5)
    assert cap["ok"] is True
    after = svc.budgets.node("mission:m-ctl").to_dict()["remaining"]["cost_usd"]
    assert after == 0.5 and after != before

    # pause/resume flip the real flag
    assert svc.ops_control("pause", mission_id="m-ctl")["ok"] is True
    assert orch.paused is True
    assert svc.ops_control("resume", mission_id="m-ctl")["ok"] is True
    assert orch.paused is False


def test_every_control_action_is_written_to_the_audit_log(app):
    svc, root = _service(app)
    _mission(svc, root, "m-aud")
    svc.run("m-aud")

    svc.ops_control("throttle", mission_id="m-aud", max_concurrency=3, actor="owner")
    actions = [a["action"] for a in svc.ops_audit(limit=10)]
    assert "ops.throttle" in actions
    entry = next(a for a in svc.ops_audit(limit=10) if a["action"] == "ops.throttle")
    assert entry["who"] == "owner" and entry["result"] == "ok"


def test_unknown_action_is_refused_not_swallowed(app):
    svc, root = _service(app)
    _mission(svc, root, "m-bad")
    r = svc.ops_control("teleport", mission_id="m-bad")
    assert r["ok"] is False and "unknown" in r["error"]
    assert "pause" in r["known"], "the refusal should list valid actions"


def test_retire_orphans_removes_only_stuck_workers(app):
    svc, root = _service(app)
    orch, _ = _mission(svc, root, "m-orph")
    members = list(orch.members.values())
    stuck = members[0]
    healthy = members[1]
    stuck.lifecycle = "working"           # an agent left behind by a crash
    healthy.lifecycle = "ready"

    r = svc.ops_control("retire_orphans", mission_id="m-orph")
    assert r["ok"] is True
    assert stuck.definition.agent_id in r["result"]["retired_orphans"]
    assert stuck.lifecycle == "retired"
    assert healthy.lifecycle == "ready", "retire_orphans must not touch healthy agents"


# --------------------------------------------------------- outcome + improvement
def test_outcome_is_recorded_and_surfaces_improvement_findings(app):
    svc, root = _service(app)
    orch, _ = _mission(svc, root, "m-out")
    svc.run("m-out")

    rec = svc.ops_record_outcome("m-out")
    assert rec["ok"] is True
    assert rec["final_status"] == "completed"
    assert rec["tasks_done"] == 2

    stored = svc.ops_outcomes(limit=5)
    assert any(o["mission_id"] == "m-out" for o in stored)

    # with a clean completed mission there is nothing to complain about
    assert isinstance(svc.ops_improve(), list)


def test_orphan_leak_produces_a_real_improvement_finding(app):
    svc, root = _service(app)
    orch, _ = _mission(svc, root, "m-leak")
    svc.run("m-leak")
    # leave an agent stuck so the outcome records an orphan leak
    list(orch.members.values())[0].lifecycle = "working"
    svc.ops_record_outcome("m-leak")

    findings = svc.ops_improve()
    assert any(f["id"] == "orphan-leak" for f in findings), findings
    leak = next(f for f in findings if f["id"] == "orphan-leak")
    assert leak["severity"] == "high"
    assert "m-leak" in leak["evidence"]


def test_console_needs_a_real_team(app):
    svc, _ = _service(app)
    assert svc.ops_record_outcome("does-not-exist")["ok"] is False
    assert svc.ops_control("pause", mission_id="does-not-exist")["ok"] is False
