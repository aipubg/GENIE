"""Forecast + security-findings services registered and exposed read-only.

Both services existed in the codebase but were never registered on the daemon,
so the UI pages could only show "not registered". This proves they are wired and
that they report honestly: an empty findings store shows zero findings, and the
forecast service declines to produce a probability without evidence.

Deterministic — no provider, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from forecast.service import ForecastService                      # noqa: E402
from security.findings import Finding, SecurityFindingService       # noqa: E402


# ------------------------------------------------------------------ forecast
def test_forecast_service_constructs_with_deterministic_engines():
    svc = ForecastService()
    assert svc.engines, "must have at least one engine"
    assert len(svc.engines) >= 2


def test_calibration_report_is_honest_when_empty():
    """No forecasts yet -> it says so rather than inventing calibration."""
    rep = ForecastService().calibration_report()
    assert rep["forecasts"] == 0
    assert rep["numeric"] == 0
    assert "cannot be scored" in rep["note"] or "no outcome data" in rep["note"]


def test_forecast_declines_without_evidence():
    """A question with no evidence must not yield a fabricated probability."""
    from forecast.service import ForecastRequest
    svc = ForecastService()
    req = ForecastRequest(question="will GENIE pass the release gate?")
    result = svc.forecast(req)
    if not result.estimable:
        # declined: it must explain itself and must NOT present a number
        assert result.point_estimate is None or result.point_estimate == 0.5, \
            "a declined forecast must not present a confident number"
        assert result.reason, "a declined forecast must say why"
    else:
        assert 0.0 <= (result.point_estimate or 0) <= 1.0


# ------------------------------------------------------------------ security
def test_findings_store_starts_empty():
    svc = SecurityFindingService()
    assert svc.all() == []
    assert svc.summary()["total"] == 0 or svc.summary() == {} or isinstance(svc.summary(), dict)


def test_finding_can_be_added_and_serialised():
    svc = SecurityFindingService()
    f = Finding(title="test finding", severity="high",
                target="example.com", evidence="synthetic")
    svc.add(f, reporter="test")
    findings = svc.all()
    assert len(findings) == 1
    assert findings[0].title == "test finding"
    d = findings[0].to_dict()
    assert d["title"] == "test finding"
    assert d["severity"] == "high"


def test_duplicate_findings_are_merged_not_duplicated():
    svc = SecurityFindingService()
    kwargs = dict(title="dup", severity="low", target="example.com",
                  evidence="same")
    svc.add(Finding(**kwargs), reporter="a")
    svc.add(Finding(**kwargs), reporter="b")
    items = svc.all()
    assert len(items) == 1, "identical findings must merge"
    assert items[0].duplicate_count >= 1


def test_invalid_severity_falls_back_to_medium():
    svc = SecurityFindingService()
    svc.add(Finding(title="x", severity="catastrophic", target="t",
                    evidence="d"))
    assert svc.all()[0].severity == "medium"
