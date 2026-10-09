"""Experience and reusable profiles must survive a real service reconstruction."""
from agents.factory import AgentFactory, ExperienceStore


def test_experience_survives_store_restart(app):
    store = ExperienceStore(app.db)
    store.record(task_type="coding", role="backend", strategy="verify", provider="local",
                 tools=["tests"], success=True, failures=[], latency_ms=10, cost_usd=0)
    restored = ExperienceStore(app.db)
    assert restored.best_configuration("coding")["success_rate"] == 1
    assert restored.statistics()["records"] == store.statistics()["records"]


def test_restart_restores_persistent_profiles_but_not_mission_workers(app):
    factory = AgentFactory(db=app.db)
    saved = factory.create(role="backend", kind="persistent").definition
    factory.create(role="worker", kind="mission")
    restored = AgentFactory(db=app.db)
    assert {a.agent_id for a in restored.healthy_agents()} == {saved.agent_id}
    assert restored.create(role="backend").reused
    restored.retire(saved.agent_id)
    assert AgentFactory(db=app.db).healthy_agents() == []


def test_start_team_keeps_plan_tasks_and_dependency_order(app):
    from agents.service import AgentService
    from agents.contracts import TeamPlan
    service = AgentService(db=app.db)
    result = service.start_team(mission_id="plan-mission", objective="build", plan=TeamPlan(
        team=[{"role": "worker"}], tasks=[
            {"task_id": "verify", "objective": "verify output", "depends_on": ["build"]},
            {"task_id": "build", "objective": "build output"}]))
    assert result["ok"]
    graph = service.team("plan-mission").graph
    assert [node.task_id for node in graph.ready()] == ["build"]
    assert graph.get("verify").depends_on == ["build"]


def test_invalid_plan_does_not_create_workers(app):
    from agents.service import AgentService
    from agents.contracts import TeamPlan
    service = AgentService(db=app.db)
    result = service.start_team(mission_id="bad-plan", objective="build", plan=TeamPlan(
        team=[{"role": "worker"}], tasks=[{"task_id": "one", "depends_on": ["missing"]}]))
    assert not result["ok"]
    assert service.team("bad-plan") is None
    assert service.factory.healthy_agents() == []

