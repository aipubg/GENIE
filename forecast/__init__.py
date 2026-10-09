"""GENIE forecasting / scenario intelligence (re-audit 14.6).

A forecast contract that refuses to be a fake oracle: probabilities are produced only when an
engine has a defensible method and real evidence, are rounded to avoid false precision, and are
declined outright when evidence is insufficient.
"""
from .service import (BaseRateForecastEngine, ForecastEngine, ForecastRequest, ForecastResult,
                      ForecastService, HistoricalFrequencyEngine, Scenario,
                      SimulationForecastEngine)

__all__ = ["ForecastService", "ForecastRequest", "ForecastResult", "Scenario",
           "ForecastEngine", "BaseRateForecastEngine", "HistoricalFrequencyEngine",
           "SimulationForecastEngine"]
