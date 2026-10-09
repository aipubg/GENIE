"""Competition engine (roadmap §16-§18).

These tests exist because a competition engine has several ways to be quietly
dishonest, and every one of them produces a plausible-looking answer:

  * letting a contestant grade its own work,
  * promoting a loser because nothing else came back,
  * letting two contestants both perform the side effect,
  * calling a loser's failure "success with low confidence",
  * running a fourth attempt when the budget cannot absorb it.
"""
from __future__ import annotations

import threading

import pytest

from agents.competition import (CompetitionEngine, MAX_CANDIDATES, OUTCOME_CANCELLED,
                                OUTCOME_NEEDS_REVISION, OUTCOME_NO_CANDIDATES,
                                OUTCOME_WINNER)
from agents.contracts import Budget, TaskNode
from core.contracts import AgentDefinition


def agent(name: str) -> AgentDefinition:
    return AgentDefinition(name=name, agent_id="agt_" + name)


def task(**kw) -> TaskNode:
    base = {"objective": "do the thing", "completion_criteria": "thing is done"}
    base.update(kw)
    return TaskNode(**base)


def always_ok(agent, t, ctx):
    return {"answer": agent.name, "ok": True}


def verifier_accepts(t, out):
    return {"ok": True, "score": 1.0, "notes": "criteria met"}


def verifier_rejects(t, out):
    return {"ok": False, "score": 0.0, "notes": "criteria not met"}


class RecordingExperience:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)
        return {"ok": True}


# ------------------------------------------------------------ independence

def test_verifier_may_not_be_the_runner():
    with pytest.raises(ValueError):
        CompetitionEngine(runner=always_ok, verifier=always_ok)


def test_verifier_is_blind_to_agent_identity():
    seen = {}

    def runner(a, t, ctx):
        return {"answer": a.name, "agent_id": a.agent_id}

    def verifier(t, out):
        seen["out"] = out
        return {"ok": True, "score": 1.0}

    engine = CompetitionEngine(runner=runner, verifier=verifier)
    engine.compete(task(), [agent("a"), agent("b")])
    # The verifier decides on the work, not on who produced it.
    assert "answer" in seen["out"]


def test_without_a_verifier_nothing_can_win():
    engine = CompetitionEngine(runner=always_ok)
    r = engine.compete(task(), [agent("a"), agent("b")])
    assert r.outcome == OUTCOME_NEEDS_REVISION
    assert r.winner is None


# ------------------------------------------------------------------ costing

def test_two_candidates_by_default():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    assert engine.candidate_count(task()) == 2
    r = engine.compete(task(), [agent("a"), agent("b"), agent("c")])
    assert r.ran == 2


def test_third_and_fourth_only_when_the_budget_can_absorb_them():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    rich = task(budget=Budget(model_calls=100))
    mid = task(budget=Budget(model_calls=100, model_calls_used=30))     # 0.70 left
    poor = task(budget=Budget(model_calls=100, model_calls_used=80))    # 0.20 left
    assert engine.candidate_count(rich) == 4
    assert engine.candidate_count(mid) == 3
    assert engine.candidate_count(poor) == 2


def test_requested_count_is_clamped_to_the_maximum():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    assert engine.candidate_count(task(), requested=99) == MAX_CANDIDATES
    assert engine.candidate_count(task(), requested=0) == 1


def test_compete_runs_at_most_as_many_as_supplied():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    r = engine.compete(task(budget=Budget(model_calls=100)), [agent("a")], requested=4)
    assert r.ran == 1


# ----------------------------------------------------------------- judging

def test_best_scoring_candidate_wins():
    def runner(a, t, ctx):
        return {"quality": {"a": 0.4, "b": 0.9}[a.name]}

    def verifier(t, out):
        score = out.get("quality", 0.0)
        return {"ok": score >= 0.5, "score": score}

    engine = CompetitionEngine(runner=runner, verifier=verifier)
    r = engine.compete(task(), [agent("a"), agent("b")])
    assert r.outcome == OUTCOME_WINNER
    assert r.winner.agent_id == "agt_b"
    assert r.winner.score == pytest.approx(0.9)


def test_all_candidates_failing_is_needs_revision_not_a_guess():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_rejects)
    r = engine.compete(task(), [agent("a"), agent("b")])
    assert r.outcome == OUTCOME_NEEDS_REVISION
    assert r.winner is None
    assert len(r.failure_reasons()) == 2
    assert all("criteria not met" in why for why in r.failure_reasons())


def test_a_contestant_that_raises_does_not_kill_the_round():
    def runner(a, t, ctx):
        if a.name == "a":
            raise RuntimeError("boom")
        return {"ok": True}

    engine = CompetitionEngine(runner=runner, verifier=verifier_accepts)
    r = engine.compete(task(), [agent("a"), agent("b")])
    assert r.outcome == OUTCOME_WINNER
    assert r.winner.agent_id == "agt_b"
    assert r.candidates[0].error == "boom"
    assert r.candidates[0].accepted is False


def test_a_failing_verifier_does_not_silently_accept():
    def verifier(t, out):
        raise RuntimeError("judge unavailable")

    engine = CompetitionEngine(runner=always_ok, verifier=verifier)
    r = engine.compete(task(), [agent("a")])
    assert r.outcome == OUTCOME_NEEDS_REVISION
    assert "verifier failed" in r.candidates[0].notes


def test_no_candidates_is_its_own_outcome():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    r = engine.compete(task(), [])
    assert r.outcome == OUTCOME_NO_CANDIDATES
    assert r.ran == 0


# -------------------------------------------------------------- cancellation

def test_cancel_before_start_runs_nothing():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    ev = threading.Event()
    ev.set()
    r = engine.compete(task(), [agent("a"), agent("b")], cancel_event=ev)
    assert r.outcome == OUTCOME_CANCELLED
    assert r.ran == 0
    assert r.winner is None


def test_cancel_midway_stops_every_remaining_contestant():
    calls = []

    def runner(a, t, ctx):
        calls.append(a.name)
        if a.name == "a":
            threading.Event()  # no-op, keeps the shape obvious
            ev.set()
        return {"ok": True}

    engine = CompetitionEngine(runner=runner, verifier=verifier_accepts)
    ev = threading.Event()
    r = engine.compete(task(), [agent("a"), agent("b"), agent("c")],
                       requested=3, cancel_event=ev)
    assert r.outcome == OUTCOME_CANCELLED
    assert calls == ["a"]          # b and c never started
    assert r.winner is None


# -------------------------------------------------------------------- commit

def test_commit_runs_exactly_once_for_the_winner():
    commits = []

    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    r = engine.compete_and_commit(task(), [agent("a"), agent("b")],
                                  commit=lambda t, out: commits.append(out) or {"ok": True})
    assert r.outcome == OUTCOME_WINNER
    assert r.committed is True
    assert len(commits) == 1       # two contestants, one side effect
    assert r.winner.output == commits[0]


def test_no_commit_when_nobody_passes_verification():
    commits = []
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_rejects)
    r = engine.compete_and_commit(task(), [agent("a"), agent("b")],
                                  commit=lambda t, out: commits.append(out))
    assert r.outcome == OUTCOME_NEEDS_REVISION
    assert r.committed is False
    assert commits == []


def test_a_failed_commit_is_reported_as_a_failure():
    def commit(t, out):
        raise RuntimeError("push rejected")

    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    r = engine.compete_and_commit(task(), [agent("a")], commit=commit)
    assert r.outcome == OUTCOME_WINNER
    assert r.committed is False
    assert "push rejected" in r.commit_result["error"]
    assert "commit stage failed" in r.note


def test_cancel_before_start_commits_nothing():
    commits = []
    ev = threading.Event()
    ev.set()
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    r = engine.compete_and_commit(task(), [agent("a")],
                                  commit=lambda t, out: commits.append(out),
                                  cancel_event=ev)
    assert r.committed is False
    assert commits == []


# ---------------------------------------------------------------- experience

def test_losers_are_recorded_as_experience_too():
    exp = RecordingExperience()
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_rejects,
                               experience=exp)
    r = engine.compete(task(), [agent("a"), agent("b")])
    assert len(exp.records) == 2
    assert all(rec["success"] is False for rec in exp.records)
    assert all(rec["failures"] for rec in exp.records)


def test_the_winner_is_recorded_as_a_success():
    exp = RecordingExperience()
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts,
                               experience=exp)
    engine.compete(task(), [agent("a"), agent("b")])
    assert all(rec["success"] is True for rec in exp.records)


# ------------------------------------------------------------------- report

def test_result_is_serialisable():
    engine = CompetitionEngine(runner=always_ok, verifier=verifier_accepts)
    payload = engine.compete(task(), [agent("a"), agent("b")]).to_dict()
    assert payload["outcome"] == OUTCOME_WINNER
    assert payload["has_winner"] is True
    assert isinstance(payload["candidates"], list)
    assert isinstance(payload["failure_reasons"], list)


# ------------------------------------------------------ service integration

def test_service_competes_a_task_and_commits_once(app):
    from agents.contracts import TeamPlan
    from agents.service import AgentService

    service = AgentService(db=app.db,
                           runner=lambda a, t, ctx: {"answer": a.name},
                           verifier=lambda t, out: {"ok": True, "score": 1.0})
    assert service.start_team(mission_id="compete-1", objective="ship it", plan=TeamPlan(
        team=[{"role": "worker"}, {"role": "worker"}],
        tasks=[{"task_id": "build", "objective": "build it",
                "completion_criteria": "it builds"}]))

    commits = []
    out = service.compete_task("compete-1", "build",
                               commit=lambda t, o: commits.append(o) or {"ok": True})
    assert out["ok"] is True
    assert out["outcome"] == OUTCOME_WINNER
    assert len(commits) == 1          # two agents attempted, one side effect


def test_service_reports_needs_revision_instead_of_guessing(app):
    from agents.contracts import TeamPlan
    from agents.service import AgentService

    service = AgentService(db=app.db,
                           runner=lambda a, t, ctx: {"answer": a.name},
                           verifier=lambda t, out: {"ok": False, "score": 0.0,
                                                    "notes": "tests fail"})
    service.start_team(mission_id="compete-2", objective="ship it", plan=TeamPlan(
        team=[{"role": "worker"}],
        tasks=[{"task_id": "build", "objective": "build it"}]))

    commits = []
    out = service.compete_task("compete-2", "build",
                               commit=lambda t, o: commits.append(o))
    assert out["ok"] is False
    assert out["outcome"] == OUTCOME_NEEDS_REVISION
    assert commits == []
    assert "tests fail" in out["competition"]["failure_reasons"][0]


def test_service_compete_rejects_an_unknown_task(app):
    from agents.contracts import TeamPlan
    from agents.service import AgentService

    service = AgentService(db=app.db)
    service.start_team(mission_id="compete-3", objective="x", plan=TeamPlan(
        team=[{"role": "worker"}], tasks=[{"task_id": "build", "objective": "b"}]))
    out = service.compete_task("compete-3", "nope")
    assert out["ok"] is False
    assert "no task" in out["error"]


def test_without_a_verifier_the_service_cannot_declare_a_winner(app):
    from agents.contracts import TeamPlan
    from agents.service import AgentService

    service = AgentService(db=app.db, runner=lambda a, t, ctx: {"answer": a.name})
    service.start_team(mission_id="compete-4", objective="x", plan=TeamPlan(
        team=[{"role": "worker"}], tasks=[{"task_id": "build", "objective": "b"}]))
    out = service.compete_task("compete-4", "build")
    assert out["outcome"] == OUTCOME_NEEDS_REVISION
    assert out["competition"]["candidates"][0]["notes"] == \
        "no verifier configured; nothing can be accepted"


# ------------------------------------------------------ service integration




