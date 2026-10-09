"""Proactivity contracts (proactive/contracts.py) — master spec §7.6–§7.8.

GENIE does not talk to fill silence. It speaks when an event is *useful*, and it has a vocabulary
for the gradations in between:

    ignore · save · show silently · mention later · speak now · interrupt

The types here make that gradation explicit, and make every notification traceable back to the
event and mission that caused it, so *"why am I seeing this?"* is always answerable.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import new_id


class Outcome(str, enum.Enum):
    """Ordered from least to most intrusive. The order is meaningful: policies clamp by rank."""

    IGNORE = "ignore"
    SAVE = "save"
    SHOW_SILENTLY = "show_silently"
    MENTION_LATER = "mention_later"
    SPEAK_NOW = "speak_now"
    INTERRUPT = "interrupt"


#: Rank, so quiet hours and duplicate suppression can *clamp* an outcome rather than replace it.
OUTCOME_RANK = {o.value: index for index, o in enumerate(Outcome)}


def clamp_outcome(outcome: str, maximum: str) -> str:
    """Return the least intrusive of the two — used by quiet hours."""
    if OUTCOME_RANK.get(outcome, 0) <= OUTCOME_RANK.get(maximum, 0):
        return outcome
    return maximum


class Channel(str, enum.Enum):
    SILENT_LOG = "silent_log"
    TOAST = "toast"
    VOICE_ALERT = "voice_alert"
    MOBILE_PUSH = "mobile_push"
    URGENT_INTERRUPT = "urgent_interrupt"


#: Which channel each outcome is delivered on.
CHANNEL_FOR_OUTCOME = {
    Outcome.IGNORE.value: Channel.SILENT_LOG.value,
    Outcome.SAVE.value: Channel.SILENT_LOG.value,
    Outcome.SHOW_SILENTLY.value: Channel.TOAST.value,
    Outcome.MENTION_LATER.value: Channel.TOAST.value,
    Outcome.SPEAK_NOW.value: Channel.VOICE_ALERT.value,
    Outcome.INTERRUPT.value: Channel.URGENT_INTERRUPT.value,
}


class EventClass(str, enum.Enum):
    """Coarse classes used for duplicate suppression."""

    RISKY_ACTION = "risky_action"
    MISSION_FAILED = "mission_failed"
    MISSION_COMPLETED = "mission_completed"
    DEVICE_OFFLINE = "device_offline"
    PERCEPTION = "perception"
    REMINDER = "reminder"
    COST = "cost"
    SECURITY = "security"
    GENERAL = "general"


@dataclass
class Candidate:
    """Something GENIE *could* say. Not yet a decision."""

    event_class: str = EventClass.GENERAL.value
    title: str = ""
    detail: str = ""
    #: How much it matters that this is acted on (0..1).
    urgency: float = 0.5
    #: How much it concerns the owner's current goal (0..1).
    relevance: float = 0.5
    #: How sure GENIE is (0..1).
    confidence: float = 0.5
    #: Whether the owner is mid-task; high means "do not break their flow" (0..1).
    current_task_load: float = 0.0
    #: How disruptive this interruption would be right now (0..1).
    interruption_cost: float = 0.3
    #: Owner preference for this class (0..1); 0.5 is neutral.
    user_preference: float = 0.5
    #: Safety-relevant events may override quiet hours.
    urgent_override: bool = False
    zone_id: str = ""
    device_id: str = ""
    mission_id: str = ""
    source_event: str = ""
    candidate_id: str = field(default_factory=lambda: new_id("cand"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"candidate_id": self.candidate_id, "event_class": self.event_class,
                "title": self.title, "detail": self.detail, "urgency": self.urgency,
                "relevance": self.relevance, "confidence": self.confidence,
                "current_task_load": self.current_task_load,
                "interruption_cost": self.interruption_cost,
                "user_preference": self.user_preference,
                "urgent_override": self.urgent_override, "zone_id": self.zone_id,
                "device_id": self.device_id, "mission_id": self.mission_id,
                "source_event": self.source_event, "ts": self.ts}


@dataclass
class Decision:
    """The scoring outcome, with the factors that produced it."""

    candidate_id: str
    outcome: str = Outcome.IGNORE.value
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    clamped_by: str = ""                  # quiet_hours | duplicate | task_load | ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"candidate_id": self.candidate_id, "outcome": self.outcome,
                "score": round(self.score, 4),
                "factors": {k: round(v, 4) for k, v in self.factors.items()},
                "clamped_by": self.clamped_by, "reason": self.reason}


@dataclass
class Notification:
    """A delivered (or deliberately withheld) message, traceable to its cause."""

    candidate_id: str = ""
    event_class: str = EventClass.GENERAL.value
    channel: str = Channel.SILENT_LOG.value
    outcome: str = Outcome.IGNORE.value
    title: str = ""
    detail: str = ""
    delivered: bool = False
    suppressed: bool = False
    suppression_reason: str = ""
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    zone_id: str = ""
    device_id: str = ""
    mission_id: str = ""
    source_event: str = ""
    notification_id: str = field(default_factory=lambda: new_id("note"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"notification_id": self.notification_id, "candidate_id": self.candidate_id,
                "event_class": self.event_class, "channel": self.channel,
                "outcome": self.outcome, "title": self.title, "detail": self.detail,
                "delivered": self.delivered, "suppressed": self.suppressed,
                "suppression_reason": self.suppression_reason,
                "score": round(self.score, 4),
                "factors": {k: round(v, 4) for k, v in self.factors.items()},
                "zone_id": self.zone_id, "device_id": self.device_id,
                "mission_id": self.mission_id, "source_event": self.source_event,
                "ts": self.ts}

    def explain(self) -> str:
        """Answer 'why am I seeing this?' in one line."""
        if self.suppressed:
            return (f"withheld: {self.suppression_reason} "
                    f"(class={self.event_class}, score={self.score:.2f})")
        return (f"{self.outcome} via {self.channel} — score {self.score:.2f} "
                f"(class={self.event_class}, source={self.source_event or 'unknown'}"
                + (f", mission={self.mission_id}" if self.mission_id else "") + ")")


@dataclass
class QuietHours:
    """Per-person/room silence window. Urgent overrides are explicit, never inferred."""

    start_hour: int = 22
    end_hour: int = 7
    person_id: str = ""
    zone_id: str = ""
    enabled: bool = True
    #: Outcomes allowed to exceed the quiet-hours clamp.
    allow_override: bool = True

    def active(self, when: Optional[float] = None) -> bool:
        if not self.enabled:
            return False
        hour = time.localtime(when if when is not None else time.time()).tm_hour
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # wraps midnight
        return hour >= self.start_hour or hour < self.end_hour

    def to_dict(self) -> Dict[str, Any]:
        return {"start_hour": self.start_hour, "end_hour": self.end_hour,
                "person_id": self.person_id, "zone_id": self.zone_id,
                "enabled": self.enabled, "allow_override": self.allow_override,
                "active_now": self.active()}


@dataclass
class DeliveryTarget:
    """Where a notification should land — nearest/active device (§6.10)."""

    device_id: str = "pc_main"
    zone_id: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"device_id": self.device_id, "zone_id": self.zone_id, "reason": self.reason}
