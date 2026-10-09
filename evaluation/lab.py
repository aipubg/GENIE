"""Evaluation / dev lab (evaluation/lab.py) — Phase 12.2.

Capability adapted from **Qwen-AgentWorld** (simulation, long-horizon evaluation, regression
environments). It belongs to the **dev-lab layer, not the production runtime** — nothing here
runs during a normal GENIE session unless the owner asks for it.

What it gives GENIE:

* **scenarios** — a named, repeatable piece of work in a domain, with deterministic checks
* **runs** — execute a suite and record per-scenario scores across several dimensions
* **baselines & regression detection** — a run is compared against the best known score for each
  scenario, so a change that makes GENIE worse is caught instead of shipping

Scoring is **multi-dimensional** (adapted from Qwen-AgentWorld's format/factuality/consistency/
realism/quality dimensions). A scenario reports individual dimensions; the lab aggregates them and
keeps the breakdown so a failure can be explained, not just counted.

The lab never fabricates a pass: a scenario with no runner, or a runner that errors, is recorded
as a failure with the reason attached.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("evaluation.lab")

#: how far below the best known score a scenario may fall before it is called a regression
DEFAULT_TOLERANCE = 0.05


@dataclass
class Scenario:
    """One repeatable evaluation: some work, and a deterministic way to judge it."""

    scenario_id: str
    domain: str
    objective: str
    #: handed to the runner so it knows what to do
    task: Dict[str, Any] = field(default_factory=dict)
    #: result -> {"passed", "score", "dimensions", "detail"}
    check: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    suite: str = "default"

    def evaluate(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Judge a runner's outcome, always returning a complete, explainable verdict."""
        if self.check is None:
            passed = bool(result.get("ok"))
            return {"passed": passed, "score": 1.0 if passed else 0.0,
                    "dimensions": {"ok": 1.0 if passed else 0.0},
                    "detail": result.get("error", "") or ("ok" if passed else "failed")}
        try:
            verdict = self.check(result) or {}
        except Exception as exc:  # a broken check is a failure, never a silent pass
            return {"passed": False, "score": 0.0, "dimensions": {},
                    "detail": f"check raised: {exc}"}

        dims = {str(k): float(v) for k, v in (verdict.get("dimensions") or {}).items()}
        score = verdict.get("score")
        score = float(score) if score is not None else (
            sum(dims.values()) / len(dims) if dims else (1.0 if verdict.get("passed") else 0.0))
        passed = bool(verdict.get("passed", score >= 0.5))
        return {"passed": passed, "score": round(max(0.0, min(1.0, score)), 4),
                "dimensions": dims, "detail": str(verdict.get("detail", ""))}


class EvaluationLab:
    """Runs scenarios, records them, and compares each run against history."""

    def __init__(self, db=None, runner: Optional[Callable[[Scenario], Dict[str, Any]]] = None):
        self.db = db
        self.runner = runner
        self._scenarios: Dict[str, Scenario] = {}

    # ------------------------------------------------------------- scenarios
    def register(self, scenario: Scenario) -> Scenario:
        self._scenarios[scenario.scenario_id] = scenario
        return scenario

    def scenario(self, scenario_id: str) -> Optional[Scenario]:
        return self._scenarios.get(scenario_id)

    def scenarios(self, suite: str = "") -> List[Scenario]:
        return [s for s in self._scenarios.values() if not suite or s.suite == suite]

    # ------------------------------------------------------------------ runs
    def run(self, *, suite: str = "", scenario_ids: Optional[List[str]] = None,
            note: str = "") -> Dict[str, Any]:
        targets = ([self._scenarios[i] for i in scenario_ids if i in self._scenarios]
                   if scenario_ids else self.scenarios(suite))
        run_id = f"eval-{uuid.uuid4().hex[:12]}"
        started = int(time.time() * 1000)
        results: List[Dict[str, Any]] = []

        for scenario in targets:
            results.append(self._execute(run_id, scenario))

        passed = len([r for r in results if r["passed"]])
        mean = (sum(r["score"] for r in results) / len(results)) if results else 0.0
        finished = int(time.time() * 1000)
        run = {"run_id": run_id, "suite": suite or "default", "started_ms": started,
               "finished_ms": finished, "scenarios": len(results), "passed": passed,
               "mean_score": round(mean, 4), "note": note, "results": results}
        self._persist(run)
        return run

    def _execute(self, run_id: str, scenario: Scenario) -> Dict[str, Any]:
        began = time.time()
        if self.runner is None:
            outcome = {"ok": False, "error": "no evaluation runner configured"}
        else:
            try:
                outcome = self.runner(scenario) or {}
            except Exception as exc:
                outcome = {"ok": False, "error": f"runner raised: {exc}"}
        verdict = scenario.evaluate(outcome)
        return {"run_id": run_id, "scenario_id": scenario.scenario_id,
                "domain": scenario.domain, "passed": verdict["passed"],
                "score": verdict["score"], "dimensions": verdict["dimensions"],
                "duration_ms": int((time.time() - began) * 1000),
                "detail": verdict["detail"] or outcome.get("error", "")}

    # ------------------------------------------------------------ persistence
    def _persist(self, run: Dict[str, Any]) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT INTO agent_eval_runs(run_id, suite, started_ms, finished_ms,"
                " scenarios, passed, mean_score, note) VALUES(?,?,?,?,?,?,?,?)",
                (run["run_id"], run["suite"], run["started_ms"], run["finished_ms"],
                 run["scenarios"], run["passed"], run["mean_score"], run["note"]))
            for r in run["results"]:
                self.db.execute(
                    "INSERT INTO agent_eval_results(run_id, scenario_id, domain, passed,"
                    " score, dimensions, duration_ms, detail) VALUES(?,?,?,?,?,?,?,?)",
                    (r["run_id"], r["scenario_id"], r["domain"], int(r["passed"]),
                     r["score"], json.dumps(r["dimensions"]), r["duration_ms"],
                     r["detail"][:500]))
        except Exception as exc:
            log.debug("eval persist failed: %s", exc)

    def history(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            return [dict(r) for r in self.db.query(
                "SELECT * FROM agent_eval_runs ORDER BY started_ms DESC LIMIT ?", (limit,))]
        except Exception as exc:
            log.debug("eval history failed: %s", exc)
            return []

    def results_for(self, run_id: str) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            return [dict(r) for r in self.db.query(
                "SELECT * FROM agent_eval_results WHERE run_id=?", (run_id,))]
        except Exception:
            return []

    # ------------------------------------------------------ baseline & regressions
    def baseline(self, scenario_id: str, *, exclude_run: str = "") -> Optional[float]:
        """Best score this scenario has achieved (optionally ignoring one run)."""
        if self.db is None:
            return None
        try:
            rows = self.db.query(
                "SELECT score FROM agent_eval_results WHERE scenario_id=? AND run_id<>?",
                (scenario_id, exclude_run or ""))
        except Exception:
            return None
        scores = [float(r["score"]) for r in rows]
        return max(scores) if scores else None

    def regressions(self, run_id: str, *, tolerance: float = DEFAULT_TOLERANCE
                    ) -> List[Dict[str, Any]]:
        """Scenarios in this run that fell below their best known score (beyond tolerance)."""
        out: List[Dict[str, Any]] = []
        for r in self.results_for(run_id):
            best = self.baseline(r["scenario_id"], exclude_run=run_id)
            if best is None:
                continue  # no history yet — nothing to regress against
            if float(r["score"]) < best - tolerance:
                out.append({"scenario_id": r["scenario_id"], "domain": r["domain"],
                            "score": float(r["score"]), "baseline": best,
                            "drop": round(best - float(r["score"]), 4),
                            "detail": r.get("detail", "")})
        return out

    def report(self, run_id: str, *, tolerance: float = DEFAULT_TOLERANCE) -> Dict[str, Any]:
        runs = [r for r in self.history(limit=100) if r["run_id"] == run_id]
        results = self.results_for(run_id)
        return {"run": runs[0] if runs else None, "results": results,
                "regressions": self.regressions(run_id, tolerance=tolerance)}
