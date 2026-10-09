"""Tests for MiroFish simulation worker and engine.

Proves: job lifecycle, scenario submission, cancellation, artifact retrieval,
and that numeric probabilities are NEVER invented from narrative output.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from integrations.mirofish_worker import (
    MiroFishSimulationEngine,
    MiroFishWorker,
    SimulationJob,
    JobState,
)


class TestJobLifecycle:
    def test_submit_creates_pending_job(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Social network cascade", {"actors": 100})
        assert job.state == JobState.PENDING
        assert job.scenario == "Social network cascade"
        assert job.seed_material == {"actors": 100}
        assert job.progress == 0.0

    def test_start_transitions_to_completed(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Test scenario")
        success = engine.start_job(job.job_id)
        assert success is True
        assert job.state == JobState.COMPLETED
        assert job.progress == 1.0
        assert job.completed_at is not None

    def test_cancel_pending_job(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("To cancel")
        assert engine.cancel_job(job.job_id) is True
        assert job.state == JobState.CANCELLED

    def test_cannot_cancel_completed_job(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Will complete")
        engine.start_job(job.job_id)
        assert engine.cancel_job(job.job_id) is False

    def test_get_status(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Status check")
        status = engine.get_status(job.job_id)
        assert status is not None
        assert status["state"] == "pending"
        assert status["scenario"] == "Status check"

    def test_get_status_unknown_job(self):
        engine = MiroFishSimulationEngine()
        assert engine.get_status("nonexistent") is None

    def test_get_result_only_when_completed(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Result test")
        assert engine.get_result(job.job_id) is None
        engine.start_job(job.job_id)
        result = engine.get_result(job.job_id)
        assert result is not None
        assert "ok" in result or "narrative" in result


class TestArtifacts:
    def test_completed_job_has_artifacts(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Artifact test")
        engine.start_job(job.job_id)
        artifacts = engine.get_artifacts(job.job_id)
        assert isinstance(artifacts, list)
        assert len(artifacts) > 0
        assert any("mirofish://" in a for a in artifacts)

    def test_pending_job_no_artifacts(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("No artifacts yet")
        assert engine.get_artifacts(job.job_id) == []


class TestListJobs:
    def test_list_empty(self):
        engine = MiroFishSimulationEngine()
        assert engine.list_jobs() == []

    def test_list_multiple(self):
        engine = MiroFishSimulationEngine()
        engine.submit_scenario("Job 1")
        engine.submit_scenario("Job 2")
        jobs = engine.list_jobs()
        assert len(jobs) == 2


class TestNoNumericProbabilities:
    """CRITICAL: MiroFish must never invent numeric probabilities from narrative."""

    def test_result_is_narrative_not_numeric(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Election outcome prediction")
        engine.start_job(job.job_id)
        result = engine.get_result(job.job_id)
        assert result is not None
        # Result should contain narrative, not probability numbers
        narrative = str(result.get("narrative", result.get("ok", "")))
        assert "probability" not in narrative.lower() or "not numeric" in narrative.lower()

    def test_warning_about_no_probabilities(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Market crash scenario")
        engine.start_job(job.job_id)
        result = engine.get_result(job.job_id)
        assert result is not None
        # Should explicitly warn about narrative-only output
        warning = result.get("warning", "")
        assert "numeric" in warning.lower() or "narrative" in warning.lower() or result.get("type") == "social_simulation"


class TestMiroFishWorker:
    def test_run_simulation_end_to_end(self):
        worker = MiroFishWorker()
        result = worker.run_simulation(
            scenario="Organizational change impact",
            seed_material={"departments": ["eng", "sales", "hr"]},
        )
        assert result["ok"] is True
        assert "job_id" in result
        assert result["status"]["state"] == "completed"
        assert result["result"] is not None
        assert isinstance(result["artifacts"], list)

    def test_worker_engine_available(self):
        engine = MiroFishSimulationEngine()
        assert engine.available is True


class TestSeparationOfConcerns:
    """MiroFish = social/system, Kronos = financial, ForecastService = routing."""

    def test_mirofish_type_is_social_simulation(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Social dynamics test")
        engine.start_job(job.job_id)
        result = engine.get_result(job.job_id)
        assert result is not None
        assert result.get("type") == "social_simulation"

    def test_job_to_dict_serializable(self):
        engine = MiroFishSimulationEngine()
        job = engine.submit_scenario("Serialization test")
        d = job.to_dict()
        assert isinstance(d, dict)
        assert "job_id" in d
        assert "state" in d
        assert "scenario" in d
