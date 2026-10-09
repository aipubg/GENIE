"""Forecasting contract (re-audit 14.6).

The danger this guards against is a confident ``73.42%`` invented from nothing. These tests assert
the opposite behaviour: numbers only when justified, always rounded, and an honest refusal when
evidence is thin.
"""
from __future__ import annotations

import pytest

from forecast.service import (BaseRateForecastEngine, ForecastRequest, ForecastService,
                              HistoricalFrequencyEngine, SimulationForecastEngine)


# ------------------------------------------------------- no fake precision
def test_no_evidence_means_no_number():
    """The single most important behaviour."""
    svc = ForecastService()
    req = ForecastRequest(question="Will Uber launch flying cars next year?",
                          horizon="12 months")
    result = svc.forecast(req)
    assert result.estimable is False
    assert result.point_estimate is None
    assert result.interval is None
    assert "do not have enough" in result.render().lower()


def test_a_vague_question_with_only_text_evidence_is_still_refused():
    svc = ForecastService()
    req = ForecastRequest(
        question="How likely is this startup to succeed?",
        evidence=[{"source": "blog", "claim": "the team seems strong"},
                  {"source": "news", "claim": "funding is uncertain"}])
    result = svc.forecast(req)
    assert result.estimable is False, "opinions are not a base rate"
    assert result.scenarios, "but qualitative scenarios are still returned"
    assert result.what_would_change, "and it says what evidence would help"


def test_probabilities_are_rounded_not_false_precise():
    svc = ForecastService()
    req = ForecastRequest(question="Will it rain?",
                          evidence=[{"source": "met", "base_rate": 0.6237}])
    result = svc.forecast(req)
    assert result.estimable is True
    assert result.point_estimate == 0.60, f"expected 0.60, got {result.point_estimate}"
    # never more precise than the rounding step
    assert abs(result.point_estimate * 100 - round(result.point_estimate * 100)) < 1e-9


def test_no_result_ever_contains_two_decimal_precision():
    svc = ForecastService()
    for rate in (0.6237, 0.3141, 0.8765, 0.1111):
        result = svc.forecast(ForecastRequest(question="q",
                                              evidence=[{"source": "s", "base_rate": rate}]))
        assert result.estimable is True
        pct = result.point_estimate * 100
        assert abs(pct - round(pct)) < 1e-9, f"{pct} carries false precision"


# ---------------------------------------------------------------- base rate
def test_base_rate_engine_produces_a_rounded_estimate_with_interval():
    svc = ForecastService()
    result = svc.forecast(ForecastRequest(
        question="Will this acquire regulatory approval?",
        horizon="6 months",
        evidence=[{"source": "regulatory history", "base_rate": 0.65},
                  {"source": "comparable filings", "base_rate": 0.55}]))
    assert result.estimable is True
    assert 0.0 <= result.point_estimate <= 1.0
    assert result.interval[0] <= result.point_estimate <= result.interval[1]
    assert result.engines_used == ["base_rate"]
    assert "base rate" in result.method


def test_reported_interval_is_wider_when_evidence_is_thin():
    thin = ForecastService().forecast(ForecastRequest(
        question="q", evidence=[{"source": "s", "base_rate": 0.5}]))
    rich = ForecastService().forecast(ForecastRequest(
        question="q", evidence=[{"source": "a", "base_rate": 0.5},
                                {"source": "b", "base_rate": 0.5},
                                {"source": "c", "base_rate": 0.5}]))
    thin_w = thin.interval[1] - thin.interval[0]
    rich_w = rich.interval[1] - rich.interval[0]
    assert thin_w > rich_w, "fewer sources must mean a wider range"


# -------------------------------------------------------------- frequencies
def test_historical_frequency_engine_uses_observed_counts():
    svc = ForecastService()
    result = svc.forecast(ForecastRequest(
        question="Will the build pass?",
        evidence=[{"source": "ci", "occurrences": 18, "trials": 20}]))
    assert result.estimable is True
    assert result.engines_used == ["historical_frequency"]
    # 18/20 = 0.90, Laplace-smoothed to 19/22 = 0.864 -> rounds to 0.85
    assert result.point_estimate == 0.85
    assert "18/20" in " ".join(result.drivers)


def test_a_tiny_sample_is_smoothed_and_not_treated_as_certain():
    result = ForecastService().forecast(ForecastRequest(
        question="q", evidence=[{"source": "s", "occurrences": 1, "trials": 1}]))
    assert result.estimable is True
    assert result.point_estimate < 1.0, "1 of 1 must not become 100%"
    assert result.confidence == "low"


def test_invalid_frequency_evidence_is_ignored():
    result = ForecastService().forecast(ForecastRequest(
        question="q", evidence=[{"source": "s", "occurrences": 5, "trials": 0}]))
    assert result.estimable is False, "division by zero must not sneak through as evidence"


# --------------------------------------------------------------- simulation
def test_simulation_engine_declines_when_no_simulator_is_attached():
    svc = ForecastService(engines=[SimulationForecastEngine()])
    result = svc.forecast(ForecastRequest(question="q", domain="social"))
    assert result.estimable is False, "an unattached simulator must not invent a result"


def test_simulation_engine_is_used_for_social_domain_when_available():
    def fake_sim(request):
        return {"estimate": 0.71, "interval": (0.6, 0.8), "confidence": "low",
                "drivers": ["simulated adoption curve"], "method": "agent simulation"}

    engine = SimulationForecastEngine(simulator=fake_sim)
    svc = ForecastService(engines=[engine])
    result = svc.forecast(ForecastRequest(question="Will this trend spread?",
                                          domain="social"))
    assert result.estimable is True
    assert result.engines_used == ["simulation"]
    assert result.point_estimate == 0.70, "rounded from 0.71"
    assert "simulation" in result.method


def test_simulation_is_not_used_for_a_general_question():
    engine = SimulationForecastEngine(simulator=lambda r: {"estimate": 0.9})
    svc = ForecastService(engines=[engine])
    result = svc.forecast(ForecastRequest(question="q", domain="general"))
    assert result.estimable is False, "domain routing must be respected"


# ------------------------------------------------------------------ ensemble
def test_multiple_engines_are_combined_and_recorded():
    sim = SimulationForecastEngine(simulator=lambda r: {"estimate": 0.8,
                                                        "interval": (0.7, 0.9)})
    svc = ForecastService(engines=[BaseRateForecastEngine(), sim])
    result = svc.forecast(ForecastRequest(
        question="q", domain="social",
        evidence=[{"source": "s", "base_rate": 0.6}]))
    assert result.estimable is True
    assert set(result.engines_used) == {"base_rate", "simulation"}
    assert result.confidence == "moderate"
    # combined 0.6 and 0.8 -> 0.7
    assert result.point_estimate == 0.70


# ------------------------------------------------------------------ contract
def test_result_records_what_would_change_the_estimate():
    result = ForecastService().forecast(ForecastRequest(
        question="q", evidence=[{"source": "s", "base_rate": 0.5}]))
    assert result.what_would_change, "an estimate must state its own weaknesses"
    assert any("base rate" in w or "frequencies" in w or "evidence" in w
               for w in result.what_would_change)


def test_render_includes_drivers_and_counter_scenarios_when_estimable():
    result = ForecastService().forecast(ForecastRequest(
        question="Will the merger close?", horizon="6 months",
        evidence=[{"source": "filings", "base_rate": 0.7, "against": "regulator objects"}]))
    text = result.render()
    assert "Estimated probability" in text
    assert "Reasonable range" in text
    assert "regulator objects" in text


def test_render_refuses_to_show_a_number_when_not_estimable():
    text = ForecastService().forecast(ForecastRequest(question="q")).render()
    assert "Estimated probability" not in text
    assert "do not have enough" in text


def test_calibration_report_is_honest_about_missing_outcomes():
    svc = ForecastService()
    svc.forecast(ForecastRequest(question="q", evidence=[{"source": "s", "base_rate": 0.5}]))
    svc.forecast(ForecastRequest(question="q2"))
    report = svc.calibration_report()
    assert report["forecasts"] == 2 and report["numeric"] == 1 and report["declined"] == 1
    assert "cannot be scored" in report["note"]
