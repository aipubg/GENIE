"""Phase 12 (foundation) — self-improvement: training-free group advantage over experience.

Adapted from Youtu-Agent's training-free GRPO loop, but driven by GENIE's **structured**
experience records (§10.16 — experience is never a transcript).

The rule this enforces: the fleet may only act on **evidence**. With too few attempts, or a
difference that is within noise, it returns nothing rather than guessing.
"""
from __future__ import annotations

from agents.factory import AgentFactory, ExperienceStore


def _seed(store: ExperienceStore, task_type: str, role: str, provider: str,
          *, wins: int, losses: int, strategy: str = "direct"):
    for i in range(wins):
        store.record(task_type=task_type, role=role, strategy=strategy, provider=provider,
                     tools=[], success=True, failures=[], latency_ms=100, cost_usd=0.01,
                     mission_id=f"w{i}")
    for i in range(losses):
        store.record(task_type=task_type, role=role, strategy=strategy, provider=provider,
                     tools=[], success=False, failures=["boom"], latency_ms=900,
                     cost_usd=0.09, mission_id=f"l{i}")


def test_distillation_prefers_the_configuration_that_actually_won():
    exp = ExperienceStore()
    _seed(exp, "coding", "backend", "A", wins=6, losses=0)   # reliable
    _seed(exp, "coding", "backend", "B", wins=1, losses=5)   # unreliable

    guidance = exp.distill(min_attempts=3)
    verdicts = {(g["provider"], g["verdict"]) for g in guidance}
    assert ("A", "prefer") in verdicts
    assert ("B", "avoid") in verdicts

    good = next(g for g in guidance if g["provider"] == "A")
    assert good["advantage"] > 0 and good["success_rate"] == 1.0
    assert "6/6" in good["evidence"], "guidance must carry its evidence, not just a verdict"


def test_no_guidance_without_enough_evidence():
    exp = ExperienceStore()
    _seed(exp, "coding", "backend", "A", wins=2, losses=0)
    # below min_attempts — the fleet must not act on two lucky runs
    assert exp.distill(min_attempts=3) == []
    assert exp.guidance_for("coding") == []


def test_noise_is_not_treated_as_signal():
    exp = ExperienceStore()
    # both configurations are equally good → no meaningful advantage
    _seed(exp, "coding", "backend", "A", wins=5, losses=5)
    _seed(exp, "coding", "backend", "B", wins=5, losses=5)
    assert exp.distill(min_attempts=3) == []


def test_factory_recommends_the_winning_configuration():
    exp = ExperienceStore()
    _seed(exp, "coding", "backend", "A", wins=6, losses=0)
    _seed(exp, "coding", "backend", "B", wins=1, losses=5)
    factory = AgentFactory(experience=exp)

    rec = factory.recommend_configuration("coding")
    assert rec is not None
    assert rec["provider"] == "A" and rec["advantage"] > 0
    assert rec["evidence"]


def test_factory_refuses_to_guess_on_unknown_work():
    exp = ExperienceStore()
    _seed(exp, "coding", "backend", "A", wins=6, losses=0)
    factory = AgentFactory(experience=exp)
    # a task type with no history yields no recommendation — not a default guess
    assert factory.recommend_configuration("underwater-basket-weaving") is None


def test_improvement_report_is_evidence_backed():
    exp = ExperienceStore()
    _seed(exp, "coding", "backend", "A", wins=6, losses=0)
    _seed(exp, "coding", "backend", "B", wins=1, losses=5)
    report = AgentFactory(experience=exp).improvement_report()
    assert report["count"] >= 1
    assert all(g["evidence"] for g in report["guidance"])
    assert report["experience"]["records"] == 12


def test_empty_store_produces_nothing_and_never_crashes():
    exp = ExperienceStore()
    assert exp.distill() == []
    factory = AgentFactory(experience=exp)
    assert factory.recommend_configuration("anything") is None
    assert factory.improvement_report()["count"] == 0
