"""Context builder: minimal packet, budget trimming, safety never dropped."""
from __future__ import annotations


def test_packet_contains_expected_sections(app):
    ctx = app.ctx()
    app.memory.write(ctx, type="preference", entity="theme", value="dark themes")
    packet = app.context_builder = None  # noqa: F841  (kept explicit: builder is on app)
    from context.builder import ContextBuilder
    builder = ContextBuilder(memory=app.memory)
    out = builder.build(ctx, "dark themes kya hai", environment={"device": "pc_main"})
    assert "safety" in out["sections"] and "user" in out["sections"]
    assert out["sections"]["user"] == "dark themes kya hai"
    assert out["retrieved_memory"]


def test_budget_trims_middle_but_keeps_safety_and_user(app):
    from context.builder import ContextBuilder
    builder = ContextBuilder(memory=None)
    big = {"recent": [{"role": "user", "content": "x" * 5000}]}
    out = builder.build(app.ctx(), "short question", recent_turns=big["recent"],
                        budget_tokens=200)
    assert "safety" in out["sections"]
    assert out["sections"]["user"] == "short question"


def test_no_lifetime_dump(app):
    from context.builder import ContextBuilder
    ctx = app.ctx()
    for i in range(30):
        app.memory.write(ctx, type="fact", entity=f"e{i}", value=f"value number {i}")
    builder = ContextBuilder(memory=app.memory)
    out = builder.build(ctx, "value", budget_tokens=300)
    assert len(out["retrieved_memory"]) <= 6


def test_context_pressure_preserves_mission_and_counts_retained_packet(app):
    from context.builder import ContextBuilder
    builder = ContextBuilder()
    packet = builder.build(app.ctx(), "continue", mission={
        "goal": "Finish the existing project", "state": "RUNNING", "steps": [1, 2]},
        recent_turns=[{"role": "user", "content": "x" * 1000}], budget_tokens=100)
    assert "Finish the existing project" in packet["sections"]["mission"]
    assert "recent" not in packet["sections"]
    size = sum(len(value) for value in packet["sections"].values())
    assert packet["approx_tokens"] == (size + 3) // 4
    assert packet["budget_overflow"] == (size > 400)


def test_required_checkpoint_overflow_is_explicit(app):
    from context.builder import ContextBuilder
    packet = ContextBuilder().build(app.ctx(), "continue", mission={
        "goal": "Required goal " * 50}, budget_tokens=10)
    assert packet["budget_overflow"] is True
    assert "mission" in packet["sections"]
