"""Presence engine (perception/presence.py) — master spec §6.10.

Answers one question: *who is likely where, and how sure are we?*

    Presence Engine: `owner likely in workshop — confidence 0.94`

Design points:

* **Signals decay.** A camera that saw someone ten minutes ago is not evidence that they are still
  there. Each signal contributes with a time decay, so confidence falls on its own instead of
  requiring an explicit "left" event that a sensor may never send.
* **Confidence is combined, not maximised.** Independent weak signals (motion + a device that just
  woke + an unlocked session) should raise confidence together. The combination is noisy-OR, which
  is the standard way to fuse independent evidence and, importantly, never exceeds 1.0.
* **Absence is not evidence of absence.** No signal means "unknown", not "nobody is there".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("perception.presence")

#: How long a signal stays meaningful. After this it contributes nothing.
SIGNAL_TTL_S = 900
#: Half-life of a signal's weight, in seconds.
SIGNAL_HALF_LIFE_S = 180.0

#: How much each sensor kind is worth on its own. A camera seeing a person is stronger evidence
#: than a mouse moving, which is stronger than "the calendar says you should be here".
KIND_WEIGHTS = {
    "camera": 0.95,
    "audio": 0.75,
    "device": 0.60,
    "screen": 0.55,
    "calendar": 0.35,
    "time": 0.20,
}


@dataclass
class PresenceSignal:
    zone_id: str
    kind: str = "device"
    confidence: float = 0.5
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def weight(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        age = max(0.0, now - self.ts)
        if age > SIGNAL_TTL_S:
            return 0.0
        decay = 0.5 ** (age / SIGNAL_HALF_LIFE_S)
        return max(0.0, min(1.0, self.confidence)) * KIND_WEIGHTS.get(self.kind, 0.5) * decay

    def to_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        return {"zone_id": self.zone_id, "kind": self.kind, "confidence": self.confidence,
                "detail": self.detail, "age_s": round((now or time.time()) - self.ts, 1),
                "weight": round(self.weight(now), 3)}


@dataclass
class PresenceEstimate:
    zone_id: str
    present: bool = False
    confidence: float = 0.0
    signals: List[Dict[str, Any]] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"zone_id": self.zone_id, "present": self.present,
                "confidence": round(self.confidence, 3), "signals": self.signals,
                "ts": self.ts,
                "summary": (f"owner likely in {self.zone_id} — confidence {self.confidence:.2f}"
                            if self.present else f"no evidence of anyone in {self.zone_id}")}


class PresenceEngine:
    def __init__(self, *, present_threshold: float = 0.45, keep: int = 200):
        self.present_threshold = float(present_threshold)
        self.keep = int(keep)
        self._signals: List[PresenceSignal] = []

    # ------------------------------------------------------------------- input
    def observe(self, zone_id: str, kind: str = "device", confidence: float = 0.5,
                detail: str = "") -> PresenceSignal:
        signal = PresenceSignal(zone_id=zone_id, kind=kind, confidence=confidence,
                                detail=detail)
        self._signals.append(signal)
        if len(self._signals) > self.keep:
            self._signals = self._signals[-self.keep:]
        return signal

    def clear_zone(self, zone_id: str) -> int:
        before = len(self._signals)
        self._signals = [s for s in self._signals if s.zone_id != zone_id]
        return before - len(self._signals)

    # ------------------------------------------------------------------ output
    def estimate(self, zone_id: str, now: Optional[float] = None) -> PresenceEstimate:
        now = now if now is not None else time.time()
        relevant = [s for s in self._signals if s.zone_id == zone_id]
        # noisy-OR over the independent signals: 1 - Π(1 - w)
        combined = 0.0
        product = 1.0
        for signal in relevant:
            weight = signal.weight(now)
            if weight <= 0:
                continue
            product *= (1.0 - weight)
        combined = 1.0 - product
        return PresenceEstimate(zone_id=zone_id, present=combined >= self.present_threshold,
                                confidence=combined,
                                signals=[s.to_dict(now) for s in relevant[-10:]])

    def occupied_zones(self, now: Optional[float] = None) -> List[PresenceEstimate]:
        zones = {s.zone_id for s in self._signals}
        estimates = [self.estimate(zone, now) for zone in zones]
        return sorted([e for e in estimates if e.present], key=lambda e: -e.confidence)

    def best_guess(self, now: Optional[float] = None) -> Optional[PresenceEstimate]:
        """Where is the owner most likely to be?"""
        occupied = self.occupied_zones(now)
        return occupied[0] if occupied else None

    def status(self) -> Dict[str, Any]:
        return {"signals": len(self._signals), "present_threshold": self.present_threshold,
                "occupied": [e.to_dict() for e in self.occupied_zones()]}
