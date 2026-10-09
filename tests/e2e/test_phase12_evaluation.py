"""Phase 12.2 — evaluation / dev lab.

The lab must judge real work. These scenarios genuinely build a Python module through the
`LocalExecutor` (a real file is written, then imported and executed), and the regression path is
exercised with a scenario that really gets worse between runs.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.providers import LocalExecutor, ModelGateway
from evaluation.lab import EvaluationLab, Scenario


@pytest.fixture()
def root():
    # the executor writes real files — the directory must exist
    path = Path(tempfile.mkdtemp()) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_runner(root: Path):
    """A runner that does real work: ask the local executor to write a module."""
    gw = ModelGateway()

    def runner(scenario: Scenario) -> dict:
        ex = LocalExecutor(artifact_root=str(root), gateway=gw, mission_id="eval",
                           provider="strong")
        agent = SimpleNamespace(agent_id="eval-agent", role="backend", tools=[])
        task = SimpleNamespace(task_id="t1", objective=scenario.objective,
                               inputs=dict(scenario.task))
        return ex(agent, task, {})

    return runner


def _module_check(root: Path):
    """Multi-dimensional check: the module must exist, import, and compute correctly."""
    def check(result: dict) -> dict:
        path = root / "genie_app.py"
        if not path.exists():
            return {"passed": False, "score": 0.0,
                    "dimensions": {"built": 0.0, "add": 0.0, "subtract": 0.0},
                    "detail": "module was not produced"}
        namespace: dict = {}
        try:
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
        except Exception as exc:
            return {"passed": False, "score": 0.0,
                    "dimensions": {"built": 1.0, "add": 0.0, "subtract": 0.0},
                    "detail": f"module does not import: {exc}"}
        dims = {"built": 1.0,
                "add": 1.0 if namespace.get("add") and namespace["add"](2, 3) == 5 else 0.0,
                "subtract": 1.0 if namespace.get("subtract")
                            and namespace["subtract"](5, 3) == 2 else 0.0}
        score = sum(dims.values()) / len(dims)
        return {"passed": score == 1.0, "score": score, "dimensions": dims,
                "detail": f"scores {dims}"}

    return check


def _coding_scenario(root: Path) -> Scenario:
    return Scenario(
        scenario_id="coding.build_calculator",
        domain="coding",
        objective="build the genie_app calculator module",
        task={"kind": "module", "spec": {
            "module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"], "body": "return a + b"},
                          {"name": "subtract", "args": ["a", "b"], "body": "return a - b"}]}},
        check=_module_check(root))


# ------------------------------------------------------------------ real run
def test_lab_runs_real_work_and_scores_dimensions(app, root):
    lab = EvaluationLab(db=app.db, runner=_build_runner(root))
    lab.register(_coding_scenario(root))

    run = lab.run(note="baseline")
    assert run["scenarios"] == 1
    assert run["passed"] == 1, run["results"]
    result = run["results"][0]
    # multi-dimensional, not a single opaque boolean
    assert result["dimensions"]["built"] == 1.0
    assert result["dimensions"]["add"] == 1.0
    assert result["dimensions"]["subtract"] == 1.0
    assert result["score"] == 1.0
    # the module really was written to disk
    assert (root / "genie_app.py").exists()


def test_history_is_persisted_and_baseline_is_the_best_score(app, root):
    lab = EvaluationLab(db=app.db, runner=_build_runner(root))
    lab.register(_coding_scenario(root))
    first = lab.run()
    lab.run()  # second run

    assert len(lab.history(limit=5)) == 2
    assert lab.baseline("coding.build_calculator") == 1.0
    # excluding a specific run still finds the other one
    assert lab.baseline("coding.build_calculator", exclude_run=first["run_id"]) == 1.0


# ------------------------------------------------------------- regressions
def test_regression_is_detected_when_a_scenario_actually_gets_worse(app):
    """A scenario that really degrades between runs must be flagged."""
    calls = {"n": 0}

    def flaky_runner(scenario: Scenario) -> dict:
        calls["n"] += 1
        # first run: correct. later runs: wrong answers.
        return {"ok": True, "value": 5 if calls["n"] == 1 else 99}

    lab = EvaluationLab(db=app.db, runner=flaky_runner)
    lab.register(Scenario(
        scenario_id="unit.answer", domain="unit", objective="answer correctly",
        task={},
        check=lambda r: {"passed": r.get("value") == 5,
                         "score": 1.0 if r.get("value") == 5 else 0.0,
                         "dimensions": {"correct": 1.0 if r.get("value") == 5 else 0.0},
                         "detail": f"value={r.get('value')}"}))

    good = lab.run()
    assert good["passed"] == 1
    assert lab.regressions(good["run_id"]) == [], "no history yet — nothing to regress against"

    bad = lab.run()
    assert bad["passed"] == 0
    regressions = lab.regressions(bad["run_id"])
    assert len(regressions) == 1
    assert regressions[0]["scenario_id"] == "unit.answer"
    assert regressions[0]["baseline"] == 1.0 and regressions[0]["score"] == 0.0
    assert regressions[0]["drop"] >= 0.95


def test_small_drops_within_tolerance_are_not_regressions(app):
    lab = EvaluationLab(db=app.db, runner=lambda s: {"ok": True})
    lab.register(Scenario(scenario_id="unit.stable", domain="unit", objective="stable",
                          task={},
                          check=lambda r: {"passed": True, "score": 1.0,
                                           "dimensions": {"a": 1.0}}))
    lab.run()
    lab.register(Scenario(scenario_id="unit.stable", domain="unit", objective="stable",
                          task={},
                          check=lambda r: {"passed": True, "score": 0.98,
                                           "dimensions": {"a": 0.98}}))
    second = lab.run()
    assert lab.regressions(second["run_id"], tolerance=0.05) == []


# ------------------------------------------------------------------ honesty
def test_scenario_without_a_runner_is_recorded_as_failure(app):
    lab = EvaluationLab(db=app.db)  # no runner
    lab.register(Scenario(scenario_id="unit.noop", domain="unit", objective="nothing", task={}))
    run = lab.run()
    assert run["passed"] == 0
    assert "no evaluation runner" in run["results"][0]["detail"]


def test_a_raised_runner_is_a_failure_not_a_crash(app):
    def boom(scenario):
        raise RuntimeError("runner exploded")

    lab = EvaluationLab(db=app.db, runner=boom)
    lab.register(Scenario(scenario_id="unit.boom", domain="unit", objective="boom", task={}))
    run = lab.run()
    assert run["passed"] == 0
    assert "runner raised" in run["results"][0]["detail"]


def test_a_broken_check_is_a_failure_not_a_silent_pass(app):
    def bad_check(result):
        raise ValueError("check is broken")

    lab = EvaluationLab(db=app.db, runner=lambda s: {"ok": True})
    lab.register(Scenario(scenario_id="unit.badcheck", domain="unit", objective="x", task={},
                          check=bad_check))
    run = lab.run()
    assert run["passed"] == 0
    assert "check raised" in run["results"][0]["detail"]


def test_report_aggregates_run_results_and_regressions(app, root):
    lab = EvaluationLab(db=app.db, runner=_build_runner(root))
    lab.register(_coding_scenario(root))
    run = lab.run()
    report = lab.report(run["run_id"])
    assert report["run"]["run_id"] == run["run_id"]
    assert len(report["results"]) == 1
    assert report["regressions"] == []
