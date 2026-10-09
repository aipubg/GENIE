"""Perception fusion (perception/fusion.py) — master spec §6.11.

Combines what each sensor knows into one `EnvironmentState` for the Context Engine:

    audio (who/where) · screen (what) · camera (environment) · device signals · calendar/time

The rule that matters: **fusion must degrade gracefully.** If any sensor is unavailable the rest
must still produce a usable state, and the state must *say* what was missing. A fusion service
that silently drops a missing sensor produces confident nonsense, which is worse than admitting
ignorance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from perception.contracts import EnvironmentState, Signal
from perception.presence import PresenceEngine

log = get_logger("perception.fusion")


class ScreenSensor:
    """Screen context from cheap signals first (§6.9).

    Priority is OS events + window state + accessibility tree + plugin state, with a screenshot
    only on ambiguity — continuous screenshots are both expensive and unnecessary. This class
    reports what is cheaply observable and never captures anything itself.
    """

    name = "screen"

    def __init__(self, computer=None):
        self.computer = computer

    def sample(self, ctx: Any = None) -> Signal:
        if self.computer is None:
            return Signal(kind="screen", name=self.name, available=False, confidence=0.0,
                          detail="no computer service available")
        try:
            state = self.computer.state()
            foreground = (state or {}).get("foreground") or {}
            title = str(foreground.get("title") or "")
            process = str(foreground.get("process") or "")
            if not title and not process:
                return Signal(kind="screen", name=self.name, available=True, confidence=0.3,
                              value={}, detail="no foreground window observed")
            return Signal(kind="screen", name=self.name, available=True, confidence=0.7,
                          value={"foreground_title": title, "foreground_process": process,
                                 "windows": len((state or {}).get("windows") or [])},
                          detail=f"foreground={process or title}")
        except Exception as exc:
            return Signal(kind="screen", name=self.name, available=False, confidence=0.0,
                          detail=f"screen sampling failed: {exc}")


class CalendarSensor:
    """Calendar/time context.

    Time is always available; a real calendar provider is a later integration, so this reports the
    time signal and says plainly that no calendar source is connected rather than inventing events.
    """

    name = "calendar"

    def __init__(self, provider=None):
        self.provider = provider

    def sample(self, ctx: Any = None) -> Signal:
        now = time.localtime()
        value = {"local_time": time.strftime("%H:%M", now),
                 "weekday": time.strftime("%A", now),
                 "hour": now.tm_hour}
        if self.provider is None:
            return Signal(kind="time", name=self.name, available=True, confidence=0.3,
                          value=value, detail="time only (no calendar source connected)")
        try:
            events = self.provider.upcoming() or []
        except Exception as exc:
            return Signal(kind="calendar", name=self.name, available=False, confidence=0.0,
                          detail=f"calendar unavailable: {exc}")
        return Signal(kind="calendar", name=self.name, available=True, confidence=0.5,
                      value={**value, "upcoming": events[:3]},
                      detail=f"{len(events)} upcoming event(s)")


class DeviceSensor:
    """Which devices are reachable — a real presence hint (an unlocked PC means someone is there)."""

    name = "devices"

    def __init__(self, devices=None):
        self.devices = devices

    def sample(self, ctx: Any = None) -> Signal:
        if self.devices is None:
            return Signal(kind="device", name=self.name, available=False, confidence=0.0,
                          detail="no device mesh available")
        try:
            online = [r.device_id for r in self.devices.registry.online()]
            return Signal(kind="device", name=self.name, available=True,
                          confidence=0.6 if online else 0.2,
                          value={"online": online}, detail=f"{len(online)} device(s) online")
        except Exception as exc:
            return Signal(kind="device", name=self.name, available=False, confidence=0.0,
                          detail=f"device sampling failed: {exc}")


class CameraSensor:
    """Camera-derived context. Only ever produces a signal from an *event*, never a stream."""

    name = "camera"

    def __init__(self, perception=None):
        self.perception = perception

    def sample(self, ctx: Any = None) -> Signal:
        if self.perception is None:
            return Signal(kind="camera", name=self.name, available=False, confidence=0.0,
                          detail="no camera backend available")
        status = self.perception.camera_status()
        if not status.get("available"):
            return Signal(kind="camera", name=self.name, available=False, confidence=0.0,
                          detail=status.get("reason", "camera unavailable"))
        recent = self.perception.recent_events(limit=5, source="camera")
        if not recent:
            return Signal(kind="camera", name=self.name, available=True, confidence=0.2,
                          value={"recent_events": 0}, detail="no recent camera event")
        latest = recent[0]
        return Signal(kind="camera", name=self.name, available=True,
                      confidence=float(latest.get("confidence", 0.5)),
                      value={"latest": latest.get("type"), "zone_id": latest.get("zone_id")},
                      detail=f"latest camera event: {latest.get('type')}")


class FusionService:
    """Turns signals into one `EnvironmentState`, and never hides a missing sensor."""

    def __init__(self, *, sensors: Optional[List[Any]] = None,
                 presence: Optional[PresenceEngine] = None):
        self.sensors = sensors if sensors is not None else []
        self.presence = presence or PresenceEngine()

    def add_sensor(self, sensor: Any) -> None:
        self.sensors.append(sensor)

    def signals(self, ctx: Any = None) -> List[Signal]:
        out: List[Signal] = []
        for sensor in self.sensors:
            try:
                signal = sensor.sample(ctx)
            except Exception as exc:                    # one bad sensor must not break fusion
                out.append(Signal(kind="device", name=getattr(sensor, "name", "unknown"),
                                  available=False, confidence=0.0, detail=str(exc)))
                continue
            if signal is not None:
                out.append(signal)
        return out

    def fuse(self, *, zone_id: str = "", ctx: Any = None,
             activity: str = "") -> EnvironmentState:
        signals = self.signals(ctx)
        unavailable = [s.name for s in signals if not s.available]
        state = EnvironmentState(zone_id=zone_id, signals=[s.to_dict() for s in signals],
                                 unavailable=unavailable, degraded=bool(unavailable),
                                 activity=activity)
        estimate = self.presence.estimate(zone_id) if zone_id else self.presence.best_guess()
        if estimate is not None:
            state.zone_id = zone_id or estimate.zone_id
            state.presence = estimate.present
            state.presence_confidence = estimate.confidence
        # a sensor that just reported a live observation is itself presence evidence
        for signal in signals:
            if signal.available and signal.kind in ("camera", "screen", "device") \
                    and signal.confidence >= 0.5 and state.zone_id:
                self.presence.observe(state.zone_id, signal.kind, signal.confidence,
                                      signal.detail)
        return state

    def status(self) -> Dict[str, Any]:
        return {"sensors": [getattr(s, "name", "unknown") for s in self.sensors],
                "presence": self.presence.status()}
