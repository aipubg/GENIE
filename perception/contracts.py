"""Perception contracts (perception/contracts.py) — master spec §6.8–§6.12, §1.9.

Shapes only: what a zone is, what may be sensed there, and what a perception event / environment
state looks like. Nothing here opens a camera or a socket.

Two rules are encoded in the *types* rather than left to callers:

  * **A zone carries its own sensing policy.** Camera/microphone permission is a property of the
    room, not of the request, so a bedroom cannot be sensed just because someone asked.
  * **Every perception event carries confidence, zone and evidence.** GENIE must be able to say
    *why* it believes something and how sure it is — never "a person is definitely here".
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import new_id


class SensorKind(str, enum.Enum):
    SCREEN = "screen"
    CAMERA = "camera"
    AUDIO = "audio"
    DEVICE = "device"
    CALENDAR = "calendar"
    TIME = "time"


class ZoneKind(str, enum.Enum):
    OFFICE = "office"
    WORKSHOP = "workshop"
    LIVING = "living"
    KITCHEN = "kitchen"
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    HALLWAY = "hallway"
    OTHER = "other"


#: Zones where sensing is OFF unless the owner turns it on, whatever the room is called.
#: Bathroom is included for the same reason as bedroom: the spec names the bedroom, but the
#: principle is "private space", and applying it only to the literal word would be a loophole.
PRIVATE_ZONE_KINDS = {ZoneKind.BEDROOM.value, ZoneKind.BATHROOM.value}


class MicPolicy(str, enum.Enum):
    OFF = "off"
    WAKE_ONLY = "wake_only"       # only after an explicit wake word / push-to-talk
    ON = "on"


@dataclass
class SensingPolicy:
    """What may be sensed in one zone. Private by default."""

    camera: bool = False
    microphone: str = MicPolicy.WAKE_ONLY.value
    screen: bool = False
    motion: bool = True                    # cheap, local, no content leaves the zone
    retention_s: int = 300                 # how long derived events are kept
    #: Whether the owner has explicitly enabled sensing here (private zones start False).
    owner_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"camera": self.camera, "microphone": self.microphone,
                "screen": self.screen, "motion": self.motion,
                "retention_s": self.retention_s, "owner_enabled": self.owner_enabled}


def default_policy(zone_kind: str) -> SensingPolicy:
    """Private zones default to nothing; shared rooms allow cheap local sensing only.

    A camera is never on by default anywhere — the spec requires a per-room policy and a
    mandatory indicator, so an implicit "on" would violate both.
    """
    if zone_kind in PRIVATE_ZONE_KINDS:
        return SensingPolicy(camera=False, microphone=MicPolicy.WAKE_ONLY.value,
                             screen=False, motion=False, retention_s=60,
                             owner_enabled=False)
    return SensingPolicy(camera=False, microphone=MicPolicy.WAKE_ONLY.value,
                         screen=False, motion=True, retention_s=300, owner_enabled=False)


@dataclass
class Zone:
    zone_id: str
    name: str = ""
    kind: str = ZoneKind.OTHER.value
    devices: List[str] = field(default_factory=list)
    policy: SensingPolicy = field(default_factory=lambda: default_policy(ZoneKind.OTHER.value))

    @property
    def private(self) -> bool:
        return self.kind in PRIVATE_ZONE_KINDS

    def to_dict(self) -> Dict[str, Any]:
        return {"zone_id": self.zone_id, "name": self.name or self.zone_id, "kind": self.kind,
                "devices": list(self.devices), "private": self.private,
                "policy": self.policy.to_dict()}


class EventType(str, enum.Enum):
    PERSON_ENTERED = "person_entered"
    PERSON_LEFT = "person_left"
    MOTION_DETECTED = "motion_detected"
    MOTION_STOPPED = "motion_stopped"
    SCREEN_CONTEXT_CHANGED = "screen_context_changed"
    ACTIVITY_CHANGED = "activity_changed"
    PRESENCE_UPDATED = "presence_updated"
    SENSOR_UNAVAILABLE = "sensor_unavailable"


@dataclass
class PerceptionEvent:
    """A structured observation. Never a claim of certainty."""

    type: str = EventType.MOTION_DETECTED.value
    zone_id: str = ""
    confidence: float = 0.0
    source: str = ""                      # camera | screen | audio | device | fusion
    evidence: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("perc"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"event_id": self.event_id, "type": self.type, "zone_id": self.zone_id,
                "confidence": round(float(self.confidence), 3), "source": self.source,
                "evidence": self.evidence, "ts": self.ts}


@dataclass
class Signal:
    """One sensor's contribution to the environment picture."""

    kind: str = SensorKind.DEVICE.value
    zone_id: str = ""
    name: str = ""
    value: Any = None
    confidence: float = 0.0
    available: bool = True
    detail: str = ""
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "zone_id": self.zone_id, "name": self.name,
                "value": self.value, "confidence": round(float(self.confidence), 3),
                "available": self.available, "detail": self.detail, "ts": self.ts}


@dataclass
class EnvironmentState:
    """The fused picture the Context Engine consumes (§6.11).

    Always carries `degraded` and the list of missing sensors, so GENIE can say *"I am not sure,
    the camera is unavailable"* instead of silently pretending it knows.
    """

    zone_id: str = ""
    presence: bool = False
    presence_confidence: float = 0.0
    activity: str = ""
    signals: List[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    unavailable: List[str] = field(default_factory=list)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def summary(self) -> str:
        if not self.presence:
            return f"nobody detected in {self.zone_id or 'any zone'}"
        confidence = f"{self.presence_confidence:.2f}"
        activity = f", activity={self.activity}" if self.activity else ""
        note = f" (degraded: {', '.join(self.unavailable)})" if self.degraded else ""
        return f"presence in {self.zone_id} confidence={confidence}{activity}{note}"

    def to_dict(self) -> Dict[str, Any]:
        return {"zone_id": self.zone_id, "presence": self.presence,
                "presence_confidence": round(float(self.presence_confidence), 3),
                "activity": self.activity, "signals": self.signals,
                "degraded": self.degraded, "unavailable": self.unavailable, "ts": self.ts,
                "summary": self.summary()}
