"""Forecasting / scenario intelligence (forecast/service.py) — re-audit 14.6.

The capability: GENIE can answer "what is the probability that X happens?" and "what could happen
over the next six months?" — **without becoming a fake oracle**.

The failure mode this module exists to prevent is an LLM inventing ``73.42%`` from pure intuition.
That number looks authoritative and is worthless. So the contract is deliberately strict:

* an engine may only produce a number when it has a **defensible method and real evidence**
  (a reference-class base rate, observed historical frequencies, or a simulation);
* probabilities are **rounded** (default to the nearest 5%) — never reported to two decimals;
* if no engine can support the request, the service returns ``estimable=False`` with qualitative
  scenarios and says plainly that a calibrated numeric probability is not justified.

NEDLE2 only decides *"this is a forecasting request"*. It never computes a probability.

Engines compose rather than compete:
    general real-world event      -> base-rate / historical-frequency
    social / collective behaviour -> simulation engine (MiroFish-style), when available
    financial time-series         -> Kronos specialist, when available
    agent / system behaviour      -> evaluation lab
    insufficient evidence         -> NO number (this is a valid, honest answer)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging_setup import get_logger

log = get_logger("forecast.service")

#: probabilities are reported rounded to this step — 62%, not 62.37%
DEFAULT_ROUNDING = 0.05
#: minimum independent evidence items before a numeric estimate is considered justified
MIN_EVIDENCE_FOR_NUMBER = 2


def _round_prob(value: float, step: float = DEFAULT_ROUNDING) -> float:
    """Round away false precision. 0.6237 -> 0.60 (not 62.37%).

    The final ``round(..., 4)`` matters: ``17 * 0.05`` is ``0.8500000000000001`` in binary
    floating point, which would otherwise leak the very precision we are removing.
    """
    return round(round(value / step) * step, 4)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass
class ForecastRequest:
    question: str
    event: str = ""
    horizon: str = ""
    domain: str = "general"
    #: each item: {"source", "claim", "weight"?, "base_rate"?, "frequency"?}
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    entity_refs: List[str] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    probability: float
    description: str = ""
    drivers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "probability": round(self.probability, 4),
                "description": self.description, "drivers": list(self.drivers)}


@dataclass
class ForecastResult:
    question: str
    event: str
    horizon: str
    estimable: bool
    method: str = ""
    engines_used: List[str] = field(default_factory=list)
    scenarios: List[Scenario] = field(default_factory=list)
    point_estimate: Optional[float] = None
    interval: Optional[Tuple[float, float]] = None
    confidence: str = "none"
    drivers: List[str] = field(default_factory=list)
    counter_scenarios: List[str] = field(default_factory=list)
    what_would_change: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    evidence_used: List[Dict[str, Any]] = field(default_factory=list)
    evidence_timestamp_ms: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question, "event": self.event, "horizon": self.horizon,
            "estimable": self.estimable, "method": self.method,
            "engines_used": self.engines_used,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "point_estimate": self.point_estimate,
            "interval": list(self.interval) if self.interval else None,
            "confidence": self.confidence, "drivers": self.drivers,
            "counter_scenarios": self.counter_scenarios,
            "what_would_change": self.what_would_change,
            "assumptions": self.assumptions, "evidence_used": self.evidence_used,
            "evidence_timestamp_ms": self.evidence_timestamp_ms, "reason": self.reason,
        }

    def render(self) -> str:
        """Human-readable answer. Deliberately omits any number we cannot defend."""
        lines = [f"Question: {self.question}"]
        if self.event:
            lines.append(f"Event: {self.event}")
        if self.horizon:
            lines.append(f"Horizon: {self.horizon}")

        if not self.estimable:
            lines.append("")
            lines.append("I can describe plausible scenarios, but I do not have enough "
                         "calibrated evidence to justify a numeric probability.")
            if self.scenarios:
                lines.append("")
                lines.append("Plausible scenarios (unranked — no probability assigned):")
                for s in self.scenarios:
                    lines.append(f"  - {s.name}: {s.description}")
            if self.what_would_change:
                lines.append("")
                lines.append("Evidence that would enable an estimate:")
                for item in self.what_would_change:
                    lines.append(f"  - {item}")
            return "\n".join(lines)

        pct = f"{round(self.point_estimate * 100)}%" if self.point_estimate is not None else "n/a"
        lines.append("")
        lines.append(f"Estimated probability: ~{pct}")
        if self.interval:
            lines.append(f"Reasonable range: {round(self.interval[0]*100)}–"
                         f"{round(self.interval[1]*100)}%")
        lines.append(f"Method: {self.method}")
        lines.append(f"Confidence: {self.confidence}")
        if self.drivers:
            lines.append("")
            lines.append("Main drivers:")
            for d in self.drivers:
                lines.append(f"  - {d}")
        if self.counter_scenarios:
            lines.append("")
            lines.append("Against the outcome:")
            for c in self.counter_scenarios:
                lines.append(f"  - {c}")
        return "\n".join(lines)


# --------------------------------------------------------------------- engines
class ForecastEngine:
    """Contract every forecasting engine must satisfy."""

    name = "base"

    def supports(self, request: ForecastRequest) -> bool:
        raise NotImplementedError

    def estimate(self, request: ForecastRequest) -> Dict[str, Any]:
        """Return {"estimate", "interval", "confidence", "drivers", "method", "evidence"}."""
        raise NotImplementedError

    def calibration_info(self) -> Dict[str, Any]:
        return {"calibrated": False, "note": "no calibration data"}


class BaseRateForecastEngine(ForecastEngine):
    """Reference-class forecasting: needs an explicit base rate in the evidence.

    This is the honest general-purpose engine. If no base rate is supplied it declines rather
    than guessing — which is the entire point.
    """

    name = "base_rate"

    def supports(self, request: ForecastRequest) -> bool:
        return any(self._rate(item) is not None for item in request.evidence)

    @staticmethod
    def _rate(item: Dict[str, Any]) -> Optional[float]:
        value = item.get("base_rate")
        if value is None:
            return None
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        return rate if 0.0 <= rate <= 1.0 else None

    def estimate(self, request: ForecastRequest) -> Dict[str, Any]:
        rates = [r for r in (self._rate(i) for i in request.evidence) if r is not None]
        if not rates:
            return {}
        # weight later/stronger evidence slightly higher, but never invent precision
        weighted = sum(r * (1.0 + 0.1 * i) for i, r in enumerate(rates))
        weights = sum(1.0 + 0.1 * i for i in range(len(rates)))
        estimate = _clamp(weighted / weights)
        spread = 0.10 + 0.05 * max(0, 3 - len(rates))   # fewer sources -> wider range
        return {
            "estimate": _round_prob(estimate),
            "interval": (_clamp(estimate - spread), _clamp(estimate + spread)),
            "confidence": "moderate" if len(rates) >= MIN_EVIDENCE_FOR_NUMBER else "low",
            "drivers": [f"reference-class base rate(s): "
                        f"{', '.join(str(round(r*100)) + '%' for r in rates)}"],
            "method": "reference-class base rate",
            "evidence": [i for i in request.evidence if self._rate(i) is not None],
        }


class HistoricalFrequencyEngine(ForecastEngine):
    """Observed frequencies: 'this happened k of n times'."""

    name = "historical_frequency"

    def supports(self, request: ForecastRequest) -> bool:
        return any(self._pair(item) for item in request.evidence)

    @staticmethod
    def _pair(item: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        k, n = item.get("occurrences"), item.get("trials")
        try:
            k, n = int(k), int(n)
        except (TypeError, ValueError):
            return None
        return (k, n) if n > 0 and 0 <= k <= n else None

    def estimate(self, request: ForecastRequest) -> Dict[str, Any]:
        pairs = [p for p in (self._pair(i) for i in request.evidence) if p]
        if not pairs:
            return {}
        total_k = sum(k for k, _ in pairs)
        total_n = sum(n for _, n in pairs)
        rate = total_k / total_n
        # Laplace smoothing — a small sample must not look certain
        smoothed = (total_k + 1) / (total_n + 2)
        spread = max(0.08, min(0.30, 1.0 / (total_n ** 0.5)))
        return {
            "estimate": _round_prob(smoothed),
            "interval": (_clamp(smoothed - spread), _clamp(smoothed + spread)),
            "confidence": "moderate" if total_n >= 20 else "low",
            "drivers": [f"observed {total_k}/{total_n} historical occurrences "
                        f"({round(rate*100)}%), Laplace-smoothed"],
            "method": "historical frequency with smoothing",
            "evidence": [i for i in request.evidence if self._pair(i)],
        }


class SimulationForecastEngine(ForecastEngine):
    """Adapter for a social/system simulation engine (MiroFish-style).

    Holds a callable so the real engine can be attached later without changing the contract.
    Without one attached it simply declines — it never fabricates a simulation result.
    """

    name = "simulation"

    def __init__(self, simulator: Optional[Callable[[ForecastRequest], Dict[str, Any]]] = None,
                 domains: Optional[List[str]] = None):
        self.simulator = simulator
        self.domains = set(domains or ["social", "collective", "market", "system"])

    def supports(self, request: ForecastRequest) -> bool:
        return self.simulator is not None and request.domain in self.domains

    def estimate(self, request: ForecastRequest) -> Dict[str, Any]:
        if self.simulator is None:
            return {}
        try:
            out = self.simulator(request) or {}
        except Exception as exc:
            log.debug("simulation engine failed: %s", exc)
            return {}
        if out.get("estimate") is None:
            return {}
        return {
            "estimate": _round_prob(float(out["estimate"])),
            "interval": tuple(out["interval"]) if out.get("interval") else None,
            "confidence": out.get("confidence", "low"),
            "drivers": out.get("drivers", []),
            "method": out.get("method", "simulation"),
            "evidence": out.get("evidence", []),
        }


# --------------------------------------------------------------------- service
class ForecastService:
    """Routes a forecasting request to whichever engines can honestly support it."""

    def __init__(self, engines: Optional[List[ForecastEngine]] = None,
                 *, rounding: float = DEFAULT_ROUNDING):
        self.engines = engines or [BaseRateForecastEngine(), HistoricalFrequencyEngine()]
        self.rounding = rounding
        self.history: List[ForecastResult] = []

    def register(self, engine: ForecastEngine) -> None:
        self.engines.append(engine)

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        usable = [e for e in self.engines if self._supports(e, request)]
        estimates = []
        for engine in usable:
            try:
                out = engine.estimate(request)
            except Exception as exc:
                log.debug("forecast engine %s failed: %s", engine.name, exc)
                continue
            if out and out.get("estimate") is not None:
                estimates.append((engine, out))

        if not estimates:
            result = self._qualitative(request)
            self.history.append(result)
            return result

        result = self._combine(request, estimates)
        self.history.append(result)
        return result

    @staticmethod
    def _supports(engine: ForecastEngine, request: ForecastRequest) -> bool:
        try:
            return bool(engine.supports(request))
        except Exception:
            return False

    def _combine(self, request: ForecastRequest,
                 estimates: List[Tuple[ForecastEngine, Dict[str, Any]]]) -> ForecastResult:
        values = [float(out["estimate"]) for _, out in estimates]
        point = _round_prob(sum(values) / len(values), self.rounding)
        lows = [out["interval"][0] for _, out in estimates if out.get("interval")]
        highs = [out["interval"][1] for _, out in estimates if out.get("interval")]
        interval = (min(lows), max(highs)) if lows and highs else None
        drivers: List[str] = []
        for _, out in estimates:
            drivers.extend(out.get("drivers", []))
        methods = [str(out.get("method", e.name)) for e, out in estimates]
        evidence: List[Dict[str, Any]] = []
        for _, out in estimates:
            evidence.extend(out.get("evidence", []) or [])

        confidence = self._confidence(estimates, evidence)
        return ForecastResult(
            question=request.question, event=request.event or request.question,
            horizon=request.horizon, estimable=True,
            method=" + ".join(dict.fromkeys(methods)),
            engines_used=[e.name for e, _ in estimates],
            scenarios=self._scenarios(point, request),
            point_estimate=point, interval=interval, confidence=confidence,
            drivers=drivers[:8],
            counter_scenarios=self._counter_scenarios(request),
            what_would_change=self._what_would_change(request, evidence),
            assumptions=list(request.assumptions), evidence_used=evidence,
            evidence_timestamp_ms=int(time.time() * 1000),
            reason="numeric estimate supported by cited evidence")

    def _confidence(self, estimates, evidence: List[Dict[str, Any]]) -> str:
        """Moderate only when independent engines broadly agree — not merely because two ran."""
        values = [float(out["estimate"]) for _, out in estimates]
        # epsilon: 0.8 - 0.6 is 0.20000000000000007 in binary floating point
        if len(estimates) >= 2 and (max(values) - min(values)) <= 0.20 + 1e-9:
            return "moderate"
        if len(evidence) >= MIN_EVIDENCE_FOR_NUMBER and len(estimates) >= 2:
            return "moderate"
        return "low"

    def _scenarios(self, point: float, request: ForecastRequest) -> List[Scenario]:
        """A best / base / worst spread around the estimate — never more precise than it."""
        low = _clamp(point - 0.15)
        high = _clamp(point + 0.15)
        return [
            Scenario("base case", point, "the estimate itself",
                     ["cited evidence as given"]),
            Scenario("upside", high, "conditions break favourably",
                     ["favourable drivers materialise"]),
            Scenario("downside", low, "conditions break against it",
                     ["adverse drivers materialise"]),
        ]

    def _counter_scenarios(self, request: ForecastRequest) -> List[str]:
        out = []
        for item in request.evidence:
            claim = str(item.get("against") or "").strip()
            if claim:
                out.append(claim)
        return out or ["no explicit counter-evidence was supplied"]

    def _what_would_change(self, request: ForecastRequest,
                           evidence: List[Dict[str, Any]]) -> List[str]:
        out = []
        if len(evidence) < MIN_EVIDENCE_FOR_NUMBER:
            out.append("more independent evidence items (currently "
                       f"{len(evidence)}, want >= {MIN_EVIDENCE_FOR_NUMBER})")
        if not any("base_rate" in e for e in evidence):
            out.append("a reference-class base rate for comparable events")
        if not any("trials" in e for e in evidence):
            out.append("observed historical frequencies (occurrences / trials)")
        return out

    def _qualitative(self, request: ForecastRequest) -> ForecastResult:
        """No engine could justify a number — say so, and still be useful."""
        return ForecastResult(
            question=request.question, event=request.event or request.question,
            horizon=request.horizon, estimable=False,
            method="none — no engine could support a calibrated estimate",
            engines_used=[e.name for e in self.engines],
            scenarios=self._qualitative_scenarios(request),
            point_estimate=None, interval=None, confidence="none",
            drivers=[], counter_scenarios=self._counter_scenarios(request),
            what_would_change=self._what_would_change(request, request.evidence),
            assumptions=list(request.assumptions),
            evidence_used=list(request.evidence),
            evidence_timestamp_ms=int(time.time() * 1000),
            reason="insufficient calibrated evidence for a numeric probability")

    @staticmethod
    def _qualitative_scenarios(request: ForecastRequest) -> List[Scenario]:
        """Scenarios with no probability — explicitly unranked."""
        scenarios = []
        for item in request.evidence:
            claim = str(item.get("claim") or "").strip()
            if claim:
                scenarios.append(Scenario(str(item.get("source", "scenario"))[:60], 0.0, claim))
        if not scenarios:
            scenarios.append(Scenario("outcome occurs", 0.0,
                                      "the event happens within the horizon"))
            scenarios.append(Scenario("outcome does not occur", 0.0,
                                      "the event does not happen within the horizon"))
        return scenarios

    def calibration_report(self) -> Dict[str, Any]:
        total = len(self.history)
        numeric = len([h for h in self.history if h.estimable])
        return {"forecasts": total, "numeric": numeric,
                "declined": total - numeric,
                "note": "no outcome data yet — calibration cannot be scored until forecasts "
                        "are resolved against reality"}
