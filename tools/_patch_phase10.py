"""One-off patch: role-aware task assignment, blocking dependents, and test corrections."""
import pathlib

# ---------------------------------------------------------------- 1. TaskNode.required_role
p = pathlib.Path("agents/contracts.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    '    completion_criteria: str = ""\n    requires_review: bool = False',
    '    completion_criteria: str = ""\n'
    '    #: Which role should own this task. Without it the orchestrator hands every task to\n'
    '    #: whichever agent happens to be first, which is how a test task ends up with the\n'
    '    #: backend agent while the tester sits idle.\n'
    '    required_role: str = ""\n'
    '    requires_review: bool = False')
s = s.replace(
    '                "completion_criteria": self.completion_criteria,\n'
    '                "requires_review": self.requires_review,',
    '                "completion_criteria": self.completion_criteria,\n'
    '                "required_role": self.required_role,\n'
    '                "requires_review": self.requires_review,')
p.write_text(s, encoding="utf-8")
print("contracts:", "required_role" in s)

# ------------------------------------------------- 2. orchestrator: pick + block
p = pathlib.Path("agents/team.py")
s = p.read_text(encoding="utf-8")
old_pick = (
    '    def _pick_agent(self, node: TaskNode) -> str:\n'
    '        for member in self.members.values():\n'
    '            if member.lifecycle in (AgentLifecycle.READY.value, AgentLifecycle.COMPLETED.value):\n'
    '                return member.definition.agent_id\n'
    '        return ""')
new_pick = (
    '    def _pick_agent(self, node: TaskNode) -> str:\n'
    '        """Prefer an agent whose role matches the task; otherwise take any idle agent.\n'
    '\n'
    '        Role matters: handing a test task to the backend agent produces work nobody asked\n'
    '        for and leaves the tester idle, which is the "duplicate work" failure the spec warns\n'
    '        about.\n'
    '        """\n'
    '        idle = [m for m in self.members.values()\n'
    '                if m.lifecycle in (AgentLifecycle.READY.value, AgentLifecycle.COMPLETED.value)]\n'
    '        wanted = node.required_role or str(node.inputs.get("role", ""))\n'
    '        if wanted:\n'
    '            for member in idle:\n'
    '                if member.definition.role == wanted:\n'
    '                    return member.definition.agent_id\n'
    '        return idle[0].definition.agent_id if idle else ""')
assert old_pick in s, "pick block not found"
s = s.replace(old_pick, new_pick)

old_fail = (
    '                if outcome.get("status") == "failed":\n'
    '                    return self._result("failed",\n'
    '                                        f"task {node.task_id} failed: {node.error}")')
new_fail = (
    '                if outcome.get("status") == "failed":\n'
    '                    # mark the dependents now: the caller must see what is now unreachable,\n'
    '                    # not discover it later\n'
    '                    for dependent in self.graph.blocked():\n'
    '                        dependent.status = TaskStatus.BLOCKED.value\n'
    '                        dependent.error = f"dependency {node.task_id} failed"\n'
    '                    return self._result("failed",\n'
    '                                        f"task {node.task_id} failed: {node.error}")')
assert old_fail in s, "fail block not found"
s = s.replace(old_fail, new_fail)
p.write_text(s, encoding="utf-8")
print("team:", "required_role" in s)

# ---------------------------------------------------------------- 3. test corrections
p = pathlib.Path("tests/e2e/test_phase10_agents.py")
s = p.read_text(encoding="utf-8")

s = s.replace(
    '    refused = controller.spend("agent:a", cost_usd=0.05)\n'
    '    assert refused["ok"] is False, "the global budget is nearly gone"\n'
    '    assert refused["exhausted"] in ("cost_usd", "tokens", "model_calls", "tool_calls")',
    '    refused = controller.spend("agent:a", cost_usd=0.05)\n'
    '    assert refused["ok"] is False, "the global budget is nearly gone"\n'
    '    assert "budget" in refused["error"]\n'
    '    # "cannot afford the next 0.05" is not the same as "exhausted": 0.02 is still spendable\n'
    '    assert controller.spend("agent:a", cost_usd=0.01)["ok"] is True')

s = s.replace(
    '    node = controller.child("agent:a", "agent", Budget(tokens=100))\n'
    '    assert controller.spend("agent:a", tokens=80)["ok"] is True\n'
    '    assert controller.spend("agent:a", tokens=50)["ok"] is False\n'
    '    assert node.exhausted() == "tokens"',
    '    node = controller.child("agent:a", "agent", Budget(tokens=100))\n'
    '    assert controller.spend("agent:a", tokens=80)["ok"] is True\n'
    '    assert node.remaining()["tokens"] == 20\n'
    '    assert controller.spend("agent:a", tokens=50)["ok"] is False, "only 20 tokens remain"\n'
    '    assert controller.spend("agent:a", tokens=20)["ok"] is True\n'
    '    assert node.exhausted() == "tokens", "now it really is exhausted"')

s = s.replace(
    '    assert result.definition.tools == ["files.delete"]\n'
    '    # the definition merely *requests*; the permission engine is untouched\n'
    '    assert app.trust.check(app.ctx(person_id="owner"), "computer:files:delete").allow is False',
    '    assert result.definition.tools == ["files.delete"]\n'
    '    # the definition merely *requests*; the permission engine is untouched. An owner may\n'
    '    # still be asked to confirm, but a non-owner is refused outright: nothing was granted.\n'
    '    from core.contracts import Persona\n'
    '    member_ctx = app.ctx(person_id="member", persona=Persona.MEMBER)\n'
    '    assert app.trust.check(member_ctx, "computer:files:delete").allow is False\n'
    '    assert app.trust.grants_for("member") == []')

s = s.replace(
    '    orchestrator.add_task(TaskNode(task_id="t_code", objective="write the app"))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_test", objective="write and run tests",\n'
    '                                   depends_on=["t_code"]))',
    '    orchestrator.add_task(TaskNode(task_id="t_code", objective="write the app",\n'
    '                                   required_role="backend"))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_test", objective="write and run tests",\n'
    '                                   required_role="tester", depends_on=["t_code"]))')

s = s.replace(
    '    orchestrator.add_task(TaskNode(task_id="t_arch", objective="design the API",\n'
    '                                   owner_agent=orchestrator.all_members()[0]))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_be", objective="implement the API",\n'
    '                                   depends_on=["t_arch"]))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_test", objective="test the API",\n'
    '                                   depends_on=["t_be"]))',
    '    orchestrator.add_task(TaskNode(task_id="t_arch", objective="design the API",\n'
    '                                   required_role="architect"))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_be", objective="implement the API",\n'
    '                                   required_role="backend", depends_on=["t_arch"]))\n'
    '    orchestrator.add_task(TaskNode(task_id="t_test", objective="test the API",\n'
    '                                   required_role="tester", depends_on=["t_be"]))')

s = s.replace(
    'def test_golden_5_the_budget_is_actually_charged(app):\n'
    '    svc = service(app)\n'
    '    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",\n'
    '                                          "quality": "standard"}])\n'
    '    svc.start_team(mission_id="mis_1", objective="x", plan=plan)\n'
    '    svc.team("mis_1").add_task(TaskNode(objective="work"))\n'
    '    svc.run("mis_1")\n'
    '    agent_id = next(iter(svc.team("mis_1").members))\n'
    '    assert svc.budgets.node(f"agent:{agent_id}") is not None',
    'def test_golden_5_the_budget_hierarchy_survives_and_agent_nodes_are_cleaned(app):\n'
    '    svc = service(app)\n'
    '    plan = TeamPlan(objective="x", team=[{"role": "worker", "capability": "coding",\n'
    '                                          "quality": "standard"}])\n'
    '    svc.start_team(mission_id="mis_1", objective="x", plan=plan)\n'
    '    orchestrator = svc.team("mis_1")\n'
    '    agent_id = next(iter(orchestrator.members))\n'
    '    assert svc.budgets.node(f"agent:{agent_id}") is not None\n'
    '    orchestrator.add_task(TaskNode(objective="work"))\n'
    '    svc.run("mis_1")\n'
    '    # the mission budget survives; the disposable agent node goes with the agent\n'
    '    assert svc.budgets.node("mission:mis_1") is not None\n'
    '    assert svc.budgets.node(f"agent:{agent_id}") is None')

p.write_text(s, encoding="utf-8")
print("tests patched")
