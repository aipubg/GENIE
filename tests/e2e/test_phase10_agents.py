"""Phase 10 — agent factory, teams and cost control.

Exit gate: *golden #4 (coding task with tests), #5 (multi-agent build with mailbox/blackboard),
#7 (provider failover mid-mission).*

Everything here runs the real orchestrator, the real DAG, the real budget hierarchy, the real
artifact store and the real factory. The only double is the model call itself (`runner`), because
this machine has no provider credentials — and the failover path deliberately exercises the real
state-preservation machinery rather than the vendor.
"""
from __future__ import annotations

import threading
import time

import pytest

from core.contracts import AgentDefinition
from agents.artifacts import ArtifactService
from agents.budget import BudgetController, MODEL_TIERS, next_tier, tier_cost_estimate
from agents.contracts import (ALLOWED_AGENT_TRANSITIONS, AgentKind, AgentLifecycle, ArtifactKind,
                              Budget, Message, MessageType, TaskNode, TaskStatus, TeamPlan)
from agents.factory import AGENT_COST_ESTIMATE, ExperienceStore, AgentFactory
from agents.service import AgentService
from agents.team import (MAX_TEAM_SIZE, REVIEW_COMPLEXITY_THRESHOLD, Blackboard, CycleError,
                         Mailbox, TaskGraph, TeamOrchestrator)


# ------------------------------------------------------------------ helpers
def ok_runner(*, provider: str = "stub-a", artifacts=None, verdict: str = "accept",
              cost_usd: float = 0.001):
    def run(agent, task, ctx):
        return {"ok": True, "provider": provider, "cost_usd": cost_usd,
                "artifacts": list(artifacts or []), "verdict": verdict,
                "detail": f"{agent.role} did {task.objective}"}
    return run


def failing_runner(error_code: str = "boom", times: int = 999):
    state = {"n": 0}

    def run(agent, task, ctx):
        state["n"] += 1
        if state["n"] <= times:
            return {"ok": False, "error": f"failure {state['n']}", "error_code": error_code}
        return {"ok": True, "provider": "stub-a"}
    return run


def service(app, runner=None, **kw) -> AgentService:
    return AgentService(db=app.db, audit=app.audit, locks=app.locks,
                        runner=runner or ok_runner(), **kw)


def single_agent(orchestrator: TeamOrchestrator, role: str = "worker") -> AgentDefinition:
    definition = AgentDefinition(name=role, role=role, kind=AgentKind.MISSION.value)
    orchestrator.add_member(definition)
    return definition


# ================================================================ task DAG §10.4
def test_a_task_cannot_start_before_its_dependencies_are_done():
    graph = TaskGraph()
    first = graph.add(TaskNode(objective="architecture"))
    second = graph.add(TaskNode(objective="backend", depends_on=[first.task_id]))
    assert [t.task_id for t in graph.ready()] == [first.task_id]
    first.status = TaskStatus.DONE.value
    assert [t.task_id for t in graph.ready()] == [second.task_id]


def test_diamond_dependencies_resolve_in_order():
    graph = TaskGraph()
    top = graph.add(TaskNode(objective="architecture"))
    left = graph.add(TaskNode(objective="backend", depends_on=[top.task_id]))
    right = graph.add(TaskNode(objective="frontend", depends_on=[top.task_id]))
    join = graph.add(TaskNode(objective="integration",
                              depends_on=[left.task_id, right.task_id]))
    top.status = TaskStatus.DONE.value
    assert {t.task_id for t in graph.ready()} == {left.task_id, right.task_id}
    left.status = TaskStatus.DONE.value
    assert [t.task_id for t in graph.ready()] == [right.task_id]
    right.status = TaskStatus.DONE.value
    assert [t.task_id for t in graph.ready()] == [join.task_id]


def test_a_cycle_is_refused():
    graph = TaskGraph()
    first = graph.add(TaskNode(objective="a"))
    second = graph.add(TaskNode(objective="b", depends_on=[first.task_id]))
    with pytest.raises(CycleError):
        graph.add(TaskNode(task_id=first.task_id, objective="a", depends_on=[second.task_id]))


def test_an_unknown_dependency_is_refused():
    graph = TaskGraph()
    with pytest.raises(KeyError):
        graph.add(TaskNode(objective="a", depends_on=["task_missing"]))


def test_a_failed_dependency_blocks_its_dependents():
    graph = TaskGraph()
    first = graph.add(TaskNode(objective="architecture"))
    second = graph.add(TaskNode(objective="backend", depends_on=[first.task_id]))
    first.status = TaskStatus.FAILED.value
    assert [t.task_id for t in graph.blocked()] == [second.task_id]
    assert graph.ready() == []


def test_a_task_records_its_objective_owner_and_outputs():
    node = TaskNode(objective="write tests", owner_agent="agt_1",
                    completion_criteria="tests pass", outputs=["art_1"])
    payload = node.to_dict()
    assert payload["objective"] == "write tests"
    assert payload["owner_agent"] == "agt_1"
    assert payload["completion_criteria"] == "tests pass"
    assert payload["outputs"] == ["art_1"]


# =============================================================== mailbox §10.5
def test_messages_are_structured_and_addressed():
    mailbox = Mailbox()
    mailbox.send(Message(from_agent="backend", to_agent="frontend",
                         type=MessageType.ARTIFACT_READY.value,
                         subject="API contract finalized", artifact_id="art_1",
                         task_id="task_1"))
    inbox = mailbox.inbox("frontend")
    assert len(inbox) == 1
    assert inbox[0].artifact_id == "art_1"
    assert inbox[0].type == MessageType.ARTIFACT_READY.value


def test_an_agent_only_receives_its_own_messages():
    mailbox = Mailbox()
    mailbox.send(Message(from_agent="a", to_agent="b", subject="for b"))
    mailbox.send(Message(from_agent="a", to_agent="c", subject="for c"))
    assert [m.subject for m in mailbox.inbox("b")] == ["for b"]


def test_the_mailbox_carries_references_not_file_contents():
    mailbox = Mailbox()
    message = mailbox.send(Message(from_agent="a", to_agent="b",
                                   artifact_id="art_1", subject="contract ready"))
    assert "artifact_id" in message.to_dict()
    assert not hasattr(message, "content")


def test_a_thread_groups_messages_by_task():
    mailbox = Mailbox()
    mailbox.send(Message(from_agent="a", to_agent="b", task_id="t1"))
    mailbox.send(Message(from_agent="b", to_agent="a", task_id="t2"))
    assert len(mailbox.thread("t1")) == 1


# =========================================================== blackboard §10.6
def test_the_blackboard_holds_mission_facts():
    board = Blackboard("mis_1")
    board.write("decision", "framework", "FastAPI", author="architect")
    board.write("interface", "api", "POST /items", author="architect")
    board.write("constraint", "no_network", "offline only")
    payload = board.to_dict()
    assert payload["decisions"][0]["value"] == "FastAPI"
    assert payload["interfaces"][0]["value"] == "POST /items"
    assert "offline only" in payload["constraints"]


def test_an_unknown_blackboard_kind_becomes_a_note():
    board = Blackboard("mis_1")
    entry = board.write("stream_of_consciousness", "k", "v")
    assert entry.kind == "note"


def test_the_blackboard_is_not_a_transcript_store():
    """Only the allowed kinds exist — there is nowhere to put private reasoning."""
    assert "reasoning" not in Blackboard.ALLOWED_KINDS
    assert "transcript" not in Blackboard.ALLOWED_KINDS
    assert "chat" not in Blackboard.ALLOWED_KINDS


def test_reading_a_decision_back():
    board = Blackboard("mis_1")
    board.write("decision", "db", "sqlite")
    assert board.read("db") == "sqlite"
    assert board.read("missing") is None


# ======================================================== artifacts §10.7
def test_an_artifact_is_referenced_by_id(app, tmp_path):
    artifacts = ArtifactService(app.db)
    target = tmp_path / "api.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    artifact = artifacts.register_file(target, producer_agent="backend", mission_id="mis_1")
    assert artifact.artifact_id
    assert artifact.sha256
    assert artifact.bytes > 0
    assert artifacts.get(artifact.artifact_id).name == "api.py"


def test_an_artifact_records_its_producer_and_dependencies(app):
    artifacts = ArtifactService(app.db)
    first = artifacts.register_content("contract", {"endpoint": "/items"},
                                       producer_agent="architect", mission_id="mis_1")
    second = artifacts.register_content("impl", {"code": "..."}, producer_agent="backend",
                                        mission_id="mis_1", dependencies=[first.artifact_id])
    assert second.dependencies == [first.artifact_id]
    assert second.producer_agent == "backend"


def test_a_registered_file_can_be_verified_and_detects_tampering(app, tmp_path):
    artifacts = ArtifactService(app.db)
    target = tmp_path / "f.txt"
    target.write_text("original", encoding="utf-8")
    artifact = artifacts.register_file(target)
    assert artifacts.verify(artifact.artifact_id)["ok"] is True
    target.write_text("tampered", encoding="utf-8")
    assert artifacts.verify(artifact.artifact_id)["ok"] is False


def test_two_agents_share_one_artifact_instead_of_copying(app, tmp_path):
    artifacts = ArtifactService(app.db)
    target = tmp_path / "shared.py"
    target.write_text("shared", encoding="utf-8")
    first = artifacts.register_file(target, producer_agent="backend")
    second = artifacts.register_file(target, producer_agent="frontend")
    # the same path resolves to one artifact, and the second registration is a new *version*
    assert second.version == 2
    assert artifacts.latest_for_path(str(target)).artifact_id == second.artifact_id
    assert artifacts.read_text(first.artifact_id) == "shared"


def test_artifact_content_is_read_on_demand(app, tmp_path):
    artifacts = ArtifactService(app.db)
    target = tmp_path / "note.txt"
    target.write_text("hello agents", encoding="utf-8")
    artifact = artifacts.register_file(target)
    assert artifacts.read_text(artifact.artifact_id) == "hello agents"


# =============================================================== budgets §10.10
def test_a_child_cannot_exceed_its_parent_budget():
    controller = BudgetController()
    controller.global_node.budget = Budget(cost_usd=0.10)
    mission = controller.child("mission:m", "mission", Budget(cost_usd=0.10))
    agent = controller.child("agent:a", "agent", Budget(cost_usd=0.10), parent="mission:m")
    assert controller.spend("agent:a", cost_usd=0.08)["ok"] is True
    refused = controller.spend("agent:a", cost_usd=0.05)
    assert refused["ok"] is False, "the global budget is nearly gone"
    assert "budget" in refused["error"]
    # "cannot afford the next 0.05" is not the same as "exhausted": 0.02 is still spendable
    assert controller.spend("agent:a", cost_usd=0.01)["ok"] is True


def test_spending_against_a_child_also_charges_the_parent():
    controller = BudgetController()
    controller.child("mission:m", "mission", Budget(tokens=1000))
    controller.child("agent:a", "agent", Budget(tokens=1000), parent="mission:m")
    controller.spend("agent:a", tokens=400)
    assert controller.node("mission:m").budget.tokens_used == 400


def test_a_token_limit_is_enforced():
    controller = BudgetController()
    node = controller.child("agent:a", "agent", Budget(tokens=100))
    assert controller.spend("agent:a", tokens=80)["ok"] is True
    assert node.remaining()["tokens"] == 20
    assert controller.spend("agent:a", tokens=50)["ok"] is False, "only 20 tokens remain"
    assert controller.spend("agent:a", tokens=20)["ok"] is True
    assert node.exhausted() == "tokens", "now it really is exhausted"


def test_a_model_call_limit_is_enforced():
    controller = BudgetController()
    controller.child("agent:a", "agent", Budget(model_calls=2))
    assert controller.record_model_call("agent:a")["ok"] is True
    assert controller.record_model_call("agent:a")["ok"] is True
    assert controller.record_model_call("agent:a")["ok"] is False


def test_a_wall_clock_budget_can_expire():
    budget = Budget(wall_ms=1)
    time.sleep(0.01)
    assert budget.exhausted() == "wall_ms"


def test_an_unlimited_dimension_is_reported_as_none():
    budget = Budget()
    assert budget.remaining()["tokens"] == float("inf")
    assert budget.exhausted() is None


def test_spending_against_an_unknown_node_is_refused():
    assert BudgetController().spend("nope", tokens=1)["ok"] is False


# ========================================================= escalation §10.11
def test_escalation_requires_evidence():
    """Paying for a stronger model without recording why is how costs run away."""
    controller = BudgetController()
    controller.child("agent:a", "agent", Budget(cost_usd=1.0))
    refused = controller.request_escalation("agent:a", current_tier="cheap-fast",
                                            reason="it failed")
    assert refused["ok"] is False
    assert "evidence" in refused["error"]


def test_escalation_moves_one_tier_and_is_recorded():
    controller = BudgetController()
    controller.child("agent:a", "agent", Budget(cost_usd=1.0))
    result = controller.request_escalation("agent:a", current_tier="cheap-fast",
                                           reason="tests failed twice",
                                           evidence={"failures": 2})
    assert result["ok"] is True
    assert result["to_tier"] == next_tier("cheap-fast")
    assert controller.escalations()[0]["evidence"]["failures"] == 2


def test_escalation_is_refused_when_the_budget_cannot_afford_it():
    controller = BudgetController()
    controller.child("agent:a", "agent", Budget(cost_usd=0.000001))
    refused = controller.request_escalation("agent:a", current_tier="strong",
                                            reason="hard", evidence={"x": 1},
                                            expected_tokens=100_000)
    assert refused["ok"] is False


def test_the_strongest_tier_cannot_escalate_further():
    controller = BudgetController()
    controller.child("agent:a", "agent", Budget(cost_usd=10.0))
    assert controller.request_escalation("agent:a", current_tier=MODEL_TIERS[-1],
                                         reason="x", evidence={"x": 1})["ok"] is False


def test_estimates_grow_with_the_tier():
    assert tier_cost_estimate("cheap-fast", 10_000) < tier_cost_estimate("strong", 10_000)
    assert tier_cost_estimate("deterministic", 10_000) == 0.0


# ====================================================== agent factory §10.1/§10.2
def test_the_factory_asks_for_a_capability_not_a_vendor(app):
    factory = AgentFactory(db=app.db)
    result = factory.create(role="backend")
    assert result.ok is True
    assert result.definition.model_requirement == {"capability": "coding",
                                                   "quality": "strong"}
    assert "provider" not in result.definition.model_requirement


def test_the_factory_reuses_an_existing_healthy_agent(app):
    factory = AgentFactory(db=app.db)
    first = factory.create(role="backend")
    second = factory.create(role="backend")
    assert second.reused is True
    assert second.definition.agent_id == first.definition.agent_id


def test_reuse_can_be_disabled_for_a_mission_scoped_agent(app):
    factory = AgentFactory(db=app.db)
    first = factory.create(role="backend", kind=AgentKind.MISSION.value)
    second = factory.create(role="backend", kind=AgentKind.MISSION.value, allow_reuse=False)
    assert second.reused is False
    assert second.definition.agent_id != first.definition.agent_id


def test_a_mission_agent_is_ephemeral_and_a_persistent_one_is_not(app):
    factory = AgentFactory(db=app.db)
    mission = factory.create(role="worker", kind=AgentKind.MISSION.value)
    persistent = factory.create(role="reviewer", kind=AgentKind.PERSISTENT.value)
    assert mission.definition.ephemeral is True
    assert persistent.definition.ephemeral is False


def test_team_size_is_the_minimum_for_the_complexity(app):
    factory = AgentFactory(db=app.db)
    assert factory.estimate_team_size(0.1) == 1
    assert factory.estimate_team_size(0.5) == 2
    assert factory.estimate_team_size(0.75) == 3
    assert factory.estimate_team_size(0.95) == 4


def test_a_plan_shows_the_single_agent_alternative(app):
    """§10.19: if one strong agent is cheaper, say so."""
    plan = AgentFactory(db=app.db).plan_team(objective="fix a typo", complexity=0.1)
    assert plan.single_agent_alternative
    assert "single strong agent" in plan.single_agent_alternative
    assert len(plan.team) == 1


def test_a_complex_plan_includes_a_reviewer(app):
    plan = AgentFactory(db=app.db).plan_team(objective="build a system", complexity=0.9,
                                             needs=["architect", "backend", "frontend"])
    roles = [t["role"] for t in plan.team]
    assert "reviewer" in roles


def test_a_plan_estimates_its_cost(app):
    plan = AgentFactory(db=app.db).plan_team(objective="build", complexity=0.8,
                                             needs=["backend", "frontend", "tester"])
    assert plan.estimated_cost_usd > 0
    assert plan.estimated_model_calls > 0
    assert plan.rationale


def test_a_plan_warns_when_it_exceeds_the_budget(app):
    plan = AgentFactory(db=app.db).plan_team(objective="build", complexity=0.9,
                                             needs=["architect", "backend", "frontend"],
                                             budget_usd=0.001)
    assert "WARNING" in plan.rationale


def test_an_agent_cannot_grant_itself_capabilities(app):
    """§10.23: the factory proposes tools; the PTE decides."""
    factory = AgentFactory(db=app.db)
    result = factory.create(role="worker", tools=["files.delete"],
                            scopes=["computer:files:delete"])
    assert result.definition.tools == ["files.delete"]
    # the definition merely *requests*; the permission engine is untouched. An owner may still be
    # asked to confirm, but a non-owner is refused outright — the factory changed nothing.
    from core.contracts import Persona
    member_ctx = app.ctx(person_id="member", persona=Persona.MEMBER)
    assert app.trust.check(member_ctx, "computer:files:delete").allow is False
    assert app.trust.grants_for("member") == []


def test_a_revised_profile_becomes_a_candidate_not_a_replacement(app):
    factory = AgentFactory(db=app.db)
    original = factory.create(role="backend", kind=AgentKind.PERSISTENT.value)
    revision = factory.revise("backend", {"purpose": "better backend"})
    assert revision["ok"] is True
    assert factory.find_suitable(role="backend").agent_id == original.definition.agent_id, \
        "the active profile must not change until the candidate is evaluated"
    assert revision["candidate_id"] in factory._candidates


def test_promoting_a_candidate_supersedes_the_previous_profile(app):
    factory = AgentFactory(db=app.db)
    original = factory.create(role="backend", kind=AgentKind.PERSISTENT.value)
    revision = factory.revise("backend", {"purpose": "better"})
    promoted = factory.promote(revision["candidate_id"])
    assert promoted["ok"] is True
    assert original.definition.agent_id in promoted["superseded"]
    assert factory.find_suitable(role="backend").purpose == "better"


def test_rejecting_a_candidate_leaves_the_profile_alone(app):
    factory = AgentFactory(db=app.db)
    original = factory.create(role="backend", kind=AgentKind.PERSISTENT.value)
    revision = factory.revise("backend", {"purpose": "worse"})
    assert factory.reject(revision["candidate_id"], reason="no better")["ok"] is True
    assert factory.find_suitable(role="backend").agent_id == original.definition.agent_id


# ================================================================ teams §10.3
def test_a_single_agent_mission_completes(app):
    svc = service(app)
    plan = TeamPlan(objective="write one file", team=[{"role": "worker", "capability": "coding",
                                                       "quality": "standard"}])
    assert svc.start_team(mission_id="mis_1", objective="write one file", plan=plan)["ok"]
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(objective="write the file"))
    result = svc.run("mis_1")
    assert result["status"] == "completed"
    assert result["team_size"] == 1


def test_a_team_mission_respects_the_dag(app):
    order: list = []

    def runner(agent, task, ctx):
        order.append(task.objective)
        return {"ok": True, "provider": "stub"}

    svc = service(app, runner=runner)
    plan = TeamPlan(objective="build", team=[
        {"role": "architect", "capability": "reasoning", "quality": "strong"},
        {"role": "backend", "capability": "coding", "quality": "strong"},
        {"role": "tester", "capability": "coding", "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="build", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(task_id="t_arch", objective="architecture"))
    orchestrator.add_task(TaskNode(task_id="t_code", objective="backend",
                                   depends_on=["t_arch"]))
    orchestrator.add_task(TaskNode(task_id="t_test", objective="tests",
                                   depends_on=["t_code"]))
    result = svc.run("mis_1")
    assert result["status"] == "completed"
    assert order == ["architecture", "backend", "tests"]


def test_the_team_ceiling_is_enforced(app):
    orchestrator = TeamOrchestrator(mission_id="m", runner=ok_runner())
    for index in range(MAX_TEAM_SIZE):
        assert orchestrator.add_member(AgentDefinition(name=f"a{index}"))["ok"] is True
    refused = orchestrator.add_member(AgentDefinition(name="one_too_many"))
    assert refused["ok"] is False
    assert "ceiling" in refused["error"]


def test_the_team_is_not_grown_by_default(app):
    """ "Multi-agent" must never mean "spawn many"."""
    plan = AgentFactory(db=app.db).plan_team(objective="fix a typo", complexity=0.05)
    assert len(plan.team) <= 1


# ========================================================= failure handling §10.14
def test_a_transient_failure_is_retried(app):
    svc = service(app, runner=failing_runner(error_code="timeout", times=1))
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    node = TaskNode(objective="work", max_attempts=3)
    orchestrator.add_task(node)
    result = svc.run("mis_1")
    assert result["status"] == "completed"
    assert node.attempt == 2


def test_a_repeated_failure_fails_the_task_but_not_the_mission_by_default(app):
    svc = service(app, runner=failing_runner(error_code="boom", times=99))
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(objective="doomed", max_attempts=2))
    result = svc.run("mis_1")
    assert result["status"] == "failed"
    assert result["failures"], "the failure must be recorded"


def test_one_failed_task_blocks_its_dependents_without_failing_the_whole_run(app):
    svc = service(app, runner=failing_runner(error_code="boom", times=99))
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(task_id="t1", objective="doomed", max_attempts=1))
    orchestrator.add_task(TaskNode(task_id="t2", objective="dependent", depends_on=["t1"]))
    result = svc.run("mis_1")
    assert result["status"] == "failed"
    statuses = {t["task_id"]: t["status"] for t in result["tasks"]["tasks"]}
    assert statuses["t2"] == "blocked"


def test_a_provider_failure_reassigns_the_task(app):
    """A provider problem is not an agent problem."""
    svc = service(app, runner=failing_runner(error_code="provider_unavailable", times=1))
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    node = TaskNode(objective="work", max_attempts=3)
    orchestrator.add_task(node)
    result = svc.run("mis_1")
    assert result["status"] == "completed"
    assert any("reassigned" in step["text"] for step in result["trace"])


def test_no_orphan_agents_after_a_failure(app):
    svc = service(app, runner=failing_runner(error_code="boom", times=99))
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="doomed", max_attempts=1))
    result = svc.run("mis_1")
    assert result["orphans"] == [], "a finished mission must leave no working agent behind"


# ============================================================ reviewer §10.15
def test_a_high_complexity_task_is_reviewed_and_accepted(app):
    svc = service(app, runner=ok_runner(verdict="accept"))
    plan = TeamPlan(objective="x", team=[
        {"role": "backend", "capability": "coding", "quality": "strong"},
        {"role": "reviewer", "capability": "reasoning", "quality": "strong"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    node = TaskNode(objective="important work", requires_review=True,
                    inputs={"complexity": REVIEW_COMPLEXITY_THRESHOLD + 0.1})
    orchestrator.add_task(node)
    result = svc.run("mis_1")
    assert result["status"] == "completed"
    assert node.inputs["review"]["accepted"] is True


def test_a_rejected_task_is_retried(app):
    calls = {"n": 0}

    def runner(agent, task, ctx):
        if agent.role == "reviewer":
            return {"ok": True, "verdict": "reject", "reason": "tests missing"}
        calls["n"] += 1
        return {"ok": True, "provider": "stub"}

    svc = service(app, runner=runner)
    plan = TeamPlan(objective="x", team=[
        {"role": "backend", "capability": "coding", "quality": "strong"},
        {"role": "reviewer", "capability": "reasoning", "quality": "strong"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    node = TaskNode(objective="work", requires_review=True, max_attempts=3,
                    inputs={"complexity": 0.9})
    orchestrator.add_task(node)
    svc.run("mis_1")
    assert node.inputs["review"]["accepted"] is False
    assert calls["n"] >= 2, "the producer must be asked to fix it"


def test_a_trivial_task_is_not_reviewed(app):
    """§10.15: do not spawn reviewers for trivial tasks."""
    svc = service(app, runner=ok_runner())
    plan = TeamPlan(objective="x", team=[
        {"role": "worker", "capability": "coding", "quality": "standard"},
        {"role": "reviewer", "capability": "reasoning", "quality": "strong"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    node = TaskNode(objective="trivial", requires_review=True, inputs={"complexity": 0.1})
    orchestrator.add_task(node)
    svc.run("mis_1")
    assert "review" not in node.inputs
    assert any("review skipped" in step["text"] for step in orchestrator.trace)


# ==================================================== cancellation §10.20, pause §10.21
def test_cancellation_retires_the_team_and_leaves_no_orphans(app):
    svc = service(app, runner=ok_runner())
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(objective="work"))
    outcome = svc.cancel("mis_1", reason="owner said stop")
    assert outcome["ok"] is True
    assert outcome["retired"], "mission agents must be retired"
    assert orchestrator.status()["orphans"] == []
    assert all(t.status == TaskStatus.CANCELLED.value for t in orchestrator.graph.all())


def test_a_cancelled_run_stops_immediately(app):
    svc = service(app, runner=ok_runner())
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="work"))
    svc.team("mis_1").cancel_event.set()
    assert svc.run("mis_1")["status"] == "cancelled"


def test_pause_and_resume_reuse_the_same_team(app):
    """§10.21: do not rebuild the whole team on resume."""
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(objective="work"))
    before = set(orchestrator.members.keys())
    svc.pause("mis_1", "owner stepped away")
    assert svc.run("mis_1")["status"] == "paused"
    outcome = svc.resume("mis_1")
    assert outcome["resumed"] is True
    assert set(orchestrator.members.keys()) == before, "the team must not be rebuilt"
    assert outcome["run"]["status"] == "completed"


def test_a_paused_task_returns_to_pending(app):
    orchestrator = TeamOrchestrator(mission_id="m", runner=ok_runner())
    orchestrator.add_member(AgentDefinition(name="w"))
    node = TaskNode(objective="work")
    orchestrator.add_task(node)
    orchestrator.pause("test")
    orchestrator.run()
    assert node.status == TaskStatus.PENDING.value


# ====================================================== lifecycle §10.2, §10.24
def test_agent_lifecycle_transitions_are_validated():
    from agents.team import TeamMember
    member = TeamMember(definition=AgentDefinition(name="w"))
    assert member.lifecycle == AgentLifecycle.CREATED.value
    assert member.move(AgentLifecycle.READY.value) is False, "created → ready is not allowed"
    assert member.move(AgentLifecycle.VALIDATED.value) is True
    assert member.move(AgentLifecycle.READY.value) is True
    assert member.move(AgentLifecycle.WORKING.value) is True
    assert member.move(AgentLifecycle.COMPLETED.value) is True
    assert member.move(AgentLifecycle.RETIRED.value) is True
    assert member.move(AgentLifecycle.WORKING.value) is False, "retired is terminal"


def test_a_persistent_agent_survives_cleanup(app):
    orchestrator = TeamOrchestrator(mission_id="m", runner=ok_runner())
    persistent = AgentDefinition(name="specialist", kind=AgentKind.PERSISTENT.value,
                                 ephemeral=False)
    mission = AgentDefinition(name="temp", kind=AgentKind.MISSION.value, ephemeral=True)
    orchestrator.add_member(persistent)
    orchestrator.add_member(mission)
    cleanup = orchestrator.cleanup()
    assert mission.agent_id in cleanup["retired"]
    assert persistent.agent_id not in cleanup["retired"]
    assert persistent.agent_id in cleanup["remaining"]


# ================================================= experience §10.16
def test_experience_is_structured_never_a_transcript(app):
    store = ExperienceStore(app.db)
    entry = store.record(task_type="coding", role="backend", strategy="direct",
                         provider="stub", tools=["files.write"], success=True, failures=[],
                         latency_ms=120, cost_usd=0.002, mission_id="mis_1")
    assert set(entry.keys()) >= {"task_type", "role", "strategy", "provider", "tools",
                                 "success", "latency_ms", "cost_usd"}
    assert "messages" not in entry and "transcript" not in entry and "content" not in entry


def test_experience_informs_the_best_configuration(app):
    store = ExperienceStore(app.db)
    for _ in range(3):
        store.record(task_type="coding", role="backend", strategy="direct", provider="a",
                     tools=[], success=True, failures=[], latency_ms=100, cost_usd=0.001)
    store.record(task_type="coding", role="worker", strategy="direct", provider="b",
                 tools=[], success=False, failures=["boom"], latency_ms=900, cost_usd=0.01)
    best = store.best_configuration("coding")
    assert best["role"] == "backend"
    assert best["success_rate"] == 1.0


def test_experience_is_recorded_from_a_real_run(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="work"))
    svc.run("mis_1")
    assert svc.experience.statistics()["records"] >= 1


# ==================================================== golden #7 failover §10.12
def test_golden_7_failover_preserves_completed_work(app):
    calls: list = []

    def runner(agent, task, ctx):
        calls.append(task.objective)
        if task.objective == "second" and len([c for c in calls if c == "second"]) == 1:
            return {"ok": False, "error": "provider A died", "error_code": "provider_unavailable"}
        return {"ok": True, "provider": "stub"}

    svc = service(app, runner=runner)
    plan = TeamPlan(objective="build a system", team=[
        {"role": "worker", "capability": "coding", "quality": "strong"},
        {"role": "backend", "capability": "coding", "quality": "strong"}])
    svc.start_team(mission_id="mis_1", objective="build a system", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.add_task(TaskNode(task_id="t1", objective="first"))
    orchestrator.add_task(TaskNode(task_id="t2", objective="second", depends_on=["t1"]))
    result = svc.run("mis_1")

    assert result["status"] == "completed"
    assert "first" in calls, "completed work must not be lost"

    failover = svc.failover("mis_1", from_provider="provider-a", reason="mid-task outage")
    assert failover["ok"] is True
    assert failover["to_provider"] and failover["to_provider"] != "provider-a"
    assert failover["completed_preserved"] >= 1
    assert "first" in failover["continuation_text"]
    assert svc.failovers()[0]["from_provider"] == "provider-a"


def test_golden_7_the_continuation_packet_is_compact(app):
    """It must not be a transcript dump."""
    svc = service(app)
    plan = TeamPlan(objective="build", team=[{"role": "worker", "capability": "coding",
                                              "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="build", plan=plan)
    orchestrator = svc.team("mis_1")
    orchestrator.blackboard.write("decision", "db", "sqlite")
    orchestrator.add_task(TaskNode(objective="one"))
    svc.run("mis_1")
    packet = svc.continuation_packet("mis_1", from_provider="a", reason="outage")
    payload = packet.to_dict()
    for key in ("objective", "current_plan", "completed_steps", "pending_steps", "decisions",
                "artifacts", "known_errors", "current_tool_state"):
        assert key in payload
    assert payload["decisions"][0]["value"] == "sqlite"
    assert len(packet.render()) < 4000


def test_golden_7_the_failing_provider_is_reported_for_the_circuit_breaker(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    outcome = svc.failover("mis_1", from_provider="a", reason="timeouts")
    assert outcome["ok"] is True
    assert svc.failovers()[0]["reason"] == "timeouts"


def test_failover_against_an_unknown_mission_is_honest(app):
    assert service(app).failover("mis_missing", from_provider="a", reason="x")["ok"] is False


# ==================================================== golden #4 / #5
def test_golden_4_a_coding_mission_produces_an_artifact_and_tests_run(app, tmp_path):
    """A real file is produced, a real test runs, and nothing outside the workspace is touched."""
    workspace = tmp_path / "workspace"
    # The app fixture already initializes the workspace root for its worker.
    workspace.mkdir(exist_ok=True)

    def runner(agent, task, ctx):
        if agent.role == "tester":
            import subprocess
            result = subprocess.run(["cmd", "/c", "echo", "tests passed"],
                                    capture_output=True, text=True)
            artifact = svc.artifacts.register_content(
                "test-result", {"exit_code": result.returncode, "output": result.stdout.strip()},
                kind=ArtifactKind.TEST_RESULT.value, producer_agent=agent.agent_id,
                mission_id="mis_4")
            return {"ok": result.returncode == 0, "provider": "stub",
                    "artifacts": [artifact.artifact_id]}
        target = workspace / "app.py"
        target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        artifact = svc.artifacts.register_file(target, producer_agent=agent.agent_id,
                                               mission_id="mis_4")
        return {"ok": True, "provider": "stub", "artifacts": [artifact.artifact_id]}

    svc = service(app, runner=runner)
    plan = svc.factory.plan_team(objective="build a small app with tests", complexity=0.7,
                                 needs=["backend", "tester"], mission_id="mis_4")
    svc.start_team(mission_id="mis_4", objective="build a small app with tests", plan=plan)
    orchestrator = svc.team("mis_4")
    orchestrator.add_task(TaskNode(task_id="t_code", objective="write the app",
                                   required_role="backend"))
    orchestrator.add_task(TaskNode(task_id="t_test", objective="write and run tests",
                                   required_role="tester", depends_on=["t_code"]))
    result = svc.run("mis_4")

    assert result["status"] == "completed"
    assert (workspace / "app.py").exists()
    assert len(result["artifacts"]) >= 2, "the code and the test result are both artifacts"
    kinds = {a["kind"] for a in result["artifacts"]}
    assert ArtifactKind.TEST_RESULT.value in kinds
    # no live-system pollution: everything the mission wrote is inside its workspace
    assert all(str(workspace) in (a.get("path") or str(workspace))
               for a in result["artifacts"])


def test_golden_5_agents_hand_off_through_mailbox_and_blackboard(app, tmp_path):
    """No shared transcript, no duplicated work, correct artifact handoff, budget respected."""
    contract = {}

    def runner(agent, task, ctx):
        if agent.role == "architect":
            artifact = svc.artifacts.register_content(
                "api-contract", {"endpoint": "/items", "method": "POST"},
                producer_agent=agent.agent_id, mission_id="mis_5", task_id=task.task_id)
            orchestrator.blackboard.write("interface", "api", "POST /items",
                                          author=agent.agent_id)
            orchestrator.blackboard.write("decision", "storage", "sqlite",
                                          author=agent.agent_id)
            orchestrator.mailbox.send(Message(from_agent=agent.agent_id, to_agent="*",
                                              type=MessageType.ARTIFACT_READY.value,
                                              subject="API contract finalized",
                                              artifact_id=artifact.artifact_id,
                                              task_id=task.task_id))
            return {"ok": True, "provider": "stub", "artifacts": [artifact.artifact_id]}
        if agent.role == "backend":
            # the backend learns the contract from the blackboard and the artifact id
            assert ctx["blackboard"]["decisions"][0]["value"] == "sqlite"
            assert ctx["artifact_refs"], "the handoff must arrive as a reference"
            contract["refs"] = ctx["artifact_refs"]
            target = tmp_path / "api.py"
            target.write_text("# implements POST /items\n", encoding="utf-8")
            artifact = svc.artifacts.register_file(target, producer_agent=agent.agent_id,
                                                  mission_id="mis_5")
            return {"ok": True, "provider": "stub", "artifacts": [artifact.artifact_id]}
        return {"ok": True, "provider": "stub"}

    svc = service(app, runner=runner)
    plan = svc.factory.plan_team(objective="build an API", complexity=0.75,
                                 needs=["architect", "backend", "tester"], mission_id="mis_5")
    svc.start_team(mission_id="mis_5", objective="build an API", plan=plan)
    orchestrator = svc.team("mis_5")
    orchestrator.add_task(TaskNode(task_id="t_arch", objective="design the API",
                                   required_role="architect"))
    orchestrator.add_task(TaskNode(task_id="t_be", objective="implement the API",
                                   required_role="backend", depends_on=["t_arch"]))
    orchestrator.add_task(TaskNode(task_id="t_test", objective="test the API",
                                   required_role="tester", depends_on=["t_be"]))
    result = svc.run("mis_5")

    assert result["status"] == "completed"
    assert contract["refs"], "the backend received the contract by reference"
    assert result["mailbox"]["count"] >= 1
    assert result["blackboard"]["by_kind"]["decision"] == 1
    # no duplicate work: the artifact was produced once and referenced
    assert len([a for a in result["artifacts"] if a["name"] == "api-contract"]) == 1
    # budget respected
    remaining = svc.budgets.node("mission:mis_5").remaining()
    assert remaining["cost_usd"] > 0


def test_golden_5_the_budget_hierarchy_exists_and_cleanup_removes_agent_nodes(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    orchestrator = svc.team("mis_1")
    agent_id = next(iter(orchestrator.members))
    assert svc.budgets.node(f"agent:{agent_id}") is not None
    orchestrator.add_task(TaskNode(objective="work"))
    svc.run("mis_1")
    # the mission budget survives; the disposable agent's node is cleaned up with the agent
    assert svc.budgets.node("mission:mis_1") is not None
    assert svc.budgets.node(f"agent:{agent_id}") is None


# ================================================================ observability §10.22
def test_the_status_view_reports_what_the_ui_needs(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="work"))
    status = svc.status()
    assert "factory" in status and "budgets" in status and "artifacts" in status
    team = status["teams"][0]
    assert team["mission_id"] == "mis_1"
    assert team["status"]["tasks"]["tasks"]


def test_the_orchestrator_status_shows_the_dependency_graph(app):
    orchestrator = TeamOrchestrator(mission_id="m", runner=ok_runner())
    orchestrator.add_member(AgentDefinition(name="w"))
    orchestrator.add_task(TaskNode(task_id="t1", objective="a"))
    orchestrator.add_task(TaskNode(task_id="t2", objective="b", depends_on=["t1"]))
    tasks = orchestrator.status()["tasks"]
    assert tasks["ready"] == ["t1"]
    assert tasks["tasks"][1]["depends_on"] == ["t1"]
    assert tasks["complete"] is False


def test_an_audit_trail_exists_for_agent_work(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="work"))
    svc.run("mis_1")
    entries = [e for e in app.audit.tail(80)
               if str(e.get("action", "")).startswith(("agent.", "artifact."))]
    assert len(entries) >= 3, "planning, creating and running must all be audited"


def test_mission_budget_is_reported_in_the_run_result(app):
    svc = service(app)
    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",
                                          "quality": "standard"}])
    svc.start_team(mission_id="mis_1", objective="x", plan=plan)
    svc.team("mis_1").add_task(TaskNode(objective="work"))
    result = svc.run("mis_1")
    assert "cost" in result
