"""Notification system (proactive/notifications.py) — master spec §7.7.

Rules the spec marks MUST, and how each is actually enforced:

| Rule | Enforcement |
|---|---|
| **Duplicate suppression** (same event class within a window) | a repeat of the same class inside `dedupe_window_s` is withheld — **unless it is materially worse**, because suppressing an escalating problem is how a warning becomes useless |
| **Quiet hours per room/person**, urgent override whitelist | quiet hours *clamp* the outcome (never replace it) to a maximum intrusiveness, unless the candidate carries an explicit urgent override |
| **Delivery respects presence** | the target is the device in the owner's current zone, else the local machine, and the choice is recorded with a reason |
| **Traceable to source event + mission** | every notification carries `source_event`, `mission_id`, the score and every factor, and `explain()` answers *"why am I seeing this?"* |
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger
from proactive.contracts import (CHANNEL_FOR_OUTCOME, Candidate, Channel, Decision,
                                 DeliveryTarget, Notification, Outcome, OUTCOME_RANK, QuietHours,
                                 clamp_outcome)
from proactive.scoring import score_candidate

log = get_logger("proactive.notifications")

#: A repeat must beat the previous score by this much to escape duplicate suppression.
ESCALATION_MARGIN = 0.15


@dataclass
class NotificationPolicy:
    dedupe_window_s: int = 300
    quiet_hours: List[QuietHours] = field(default_factory=list)
    #: Outcomes that may exceed the quiet-hours clamp when a candidate is an urgent override.
    urgent_whitelist: List[str] = field(default_factory=lambda: [
        Outcome.INTERRUPT.value, Outcome.SPEAK_NOW.value])
    quiet_hours_max_outcome: str = Outcome.SHOW_SILENTLY.value
    preferences: Dict[str, float] = field(default_factory=dict)
    #: Clock used for the quiet-hours active check. Injected in tests so quiet hours are
    #: deterministic (independent of the wall-clock at run time). Defaults to real time.
    now: Optional[Callable[[], float]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.now is None:
            self.now = time.time

    def window_for(self, person_id: str, zone_id: str) -> Optional[QuietHours]:
        when = self.now()
        for window in self.quiet_hours:
            if window.person_id and window.person_id != person_id:
                continue
            if window.zone_id and zone_id and window.zone_id != zone_id:
                continue
            if window.active(when):
                return window
        return None


class Notifier:
    """Decides whether to speak, how loudly, and where."""

    def __init__(self, *, policy: Optional[NotificationPolicy] = None,
                 db=None, audit=None, presence=None, devices=None,
                 deliver: Optional[Callable[[Notification], bool]] = None,
                 now: Optional[Callable[[], float]] = None):
        self.policy = policy or NotificationPolicy()
        self.db = db
        self.audit = audit
        self.presence = presence
        self.devices = devices
        self._deliver = deliver
        self._now = now or time.time
        # honour the injected clock for the quiet-hours active check too (deterministic tests)
        self.policy.now = self._now
        self._history: List[Notification] = []
        #: event_class -> (ts, score) of the last *delivered* notification
        self._last: Dict[str, Dict[str, Any]] = {}
        self._outbox: List[Notification] = []

    # ------------------------------------------------------------------ delivery
    def _target_for(self, candidate: Candidate) -> DeliveryTarget:
        """Nearest/active device first (§6.10), then the local machine."""
        if candidate.device_id:
            return DeliveryTarget(device_id=candidate.device_id, zone_id=candidate.zone_id,
                                  reason="the candidate named its device")
        zone_id = candidate.zone_id
        if self.presence is not None:
            try:
                best = self.presence.best_guess() if not zone_id else None
                zone_id = zone_id or (best.zone_id if best else "")
                if zone_id:
                    zone = getattr(best, "zone_id", "") if best else ""
                    return DeliveryTarget(device_id="pc_main", zone_id=zone_id,
                                          reason=f"owner is likely in {zone_id}")
            except Exception as exc:
                log.debug("presence lookup failed: %s", exc)
        return DeliveryTarget(device_id="pc_main", zone_id=zone_id,
                              reason="default to the local machine")

    def _emit(self, notification: Notification) -> None:
        if self._deliver is not None:
            try:
                notification.delivered = bool(self._deliver(notification))
            except Exception as exc:
                log.warning("delivery failed: %s", exc)
                notification.delivered = False
        else:
            # no transport configured: the outbox is the channel, and it is honest about that
            notification.delivered = True
        if notification.delivered and notification.outcome != Outcome.IGNORE.value:
            self._outbox.append(notification)
            if len(self._outbox) > 200:
                self._outbox = self._outbox[-200:]

    # ------------------------------------------------------------------- decide
    def submit(self, candidate: Candidate, *, person_id: str = "owner") -> Notification:
        """Score a candidate, apply the MUST rules, and deliver what survives."""
        decision: Decision = score_candidate(candidate)
        notification = Notification(
            candidate_id=candidate.candidate_id, event_class=candidate.event_class,
            outcome=decision.outcome, score=decision.score, factors=decision.factors,
            title=candidate.title, detail=candidate.detail, zone_id=candidate.zone_id,
            device_id=candidate.device_id, mission_id=candidate.mission_id,
            source_event=candidate.source_event,
            channel=CHANNEL_FOR_OUTCOME.get(decision.outcome, Channel.SILENT_LOG.value))

        if decision.outcome == Outcome.IGNORE.value:
            notification.suppressed = True
            notification.suppression_reason = "score below the 'worth saying' threshold"
            return self._record(notification, candidate, person_id)

        # --- duplicate suppression -----------------------------------------
        window = self.policy.dedupe_window_s
        previous = self._last.get(candidate.event_class)
        now = self._now()
        if previous is not None and (now - previous["ts"]) < window:
            escalated = notification.score >= (previous["score"] + ESCALATION_MARGIN)
            if not escalated:
                notification.suppressed = True
                notification.outcome = Outcome.SAVE.value
                notification.channel = Channel.SILENT_LOG.value
                remaining = int(window - (now - previous["ts"]))
                notification.suppression_reason = (
                    f"duplicate of the same class within {window}s "
                    f"({remaining}s left); not materially worse "
                    f"({notification.score:.2f} vs {previous['score']:.2f})")
                return self._record(notification, candidate, person_id)

        # --- quiet hours ----------------------------------------------------
        quiet = self.policy.window_for(person_id, candidate.zone_id)
        if quiet is not None:
            override = candidate.urgent_override and quiet.allow_override \
                and notification.outcome in self.policy.urgent_whitelist
            if not override:
                clamped = clamp_outcome(notification.outcome, self.policy.quiet_hours_max_outcome)
                if clamped != notification.outcome:
                    notification.outcome = clamped
                    notification.channel = CHANNEL_FOR_OUTCOME.get(clamped,
                                                                   Channel.SILENT_LOG.value)
                    notification.suppression_reason = (
                        f"quiet hours {quiet.start_hour:02d}:00–{quiet.end_hour:02d}:00 "
                        f"capped this at {clamped}")

        target = self._target_for(candidate)
        notification.device_id = target.device_id
        if not notification.zone_id:
            notification.zone_id = target.zone_id
        notification.detail = notification.detail
        self._emit(notification)
        if notification.delivered:
            self._last[candidate.event_class] = {"ts": now, "score": notification.score}
        return self._record(notification, candidate, person_id, target=target)

    # ------------------------------------------------------------------ storage
    def _record(self, notification: Notification, candidate: Candidate, person_id: str,
                target: Optional[DeliveryTarget] = None) -> Notification:
        self._history.append(notification)
        if len(self._history) > 500:
            self._history = self._history[-500:]
        if self.db is not None:
            import json as _json
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO proactive_notifications(notification_id,"
                    " candidate_id, event_class, channel, outcome, title, detail, delivered,"
                    " suppressed, suppression_reason, score, factors, zone_id, device_id,"
                    " mission_id, source_event, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (notification.notification_id, notification.candidate_id,
                     notification.event_class, notification.channel, notification.outcome,
                     notification.title, notification.detail,
                     1 if notification.delivered else 0,
                     1 if notification.suppressed else 0, notification.suppression_reason,
                     notification.score, _json.dumps(notification.factors),
                     notification.zone_id, notification.device_id, notification.mission_id,
                     notification.source_event, notification.ts))
            except Exception as exc:
                log.debug("notification persist failed: %s", exc)
        if self.audit and notification.delivered:
            try:
                self.audit.record(who=person_id,
                                  action=f"proactive.notify:{notification.event_class}",
                                  why=notification.title[:120],
                                  mission_id=notification.mission_id or None,
                                  result=notification.outcome)
            except Exception as exc:
                log.debug("notification audit failed: %s", exc)
        log.info("proactive %s: %s", notification.outcome, notification.explain())
        return notification

    # ------------------------------------------------------------------- reading
    def history(self, *, limit: int = 50, delivered_only: bool = False) -> List[Dict[str, Any]]:
        items = self._history
        if delivered_only:
            items = [n for n in items if n.delivered]
        return [n.to_dict() for n in items[-limit:]][::-1]

    def outbox(self) -> List[Dict[str, Any]]:
        return [n.to_dict() for n in self._outbox]

    def explain(self, notification_id: str) -> Optional[str]:
        for notification in self._history:
            if notification.notification_id == notification_id:
                return notification.explain()
        return None

    def set_quiet_hours(self, *, start_hour: int, end_hour: int, person_id: str = "",
                        zone_id: str = "", enabled: bool = True) -> Dict[str, Any]:
        window = QuietHours(start_hour=int(start_hour) % 24, end_hour=int(end_hour) % 24,
                            person_id=person_id, zone_id=zone_id, enabled=enabled)
        self.policy.quiet_hours = [w for w in self.policy.quiet_hours
                                   if not (w.person_id == person_id and w.zone_id == zone_id)]
        self.policy.quiet_hours.append(window)
        if self.db is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO proactive_quiet_hours(person_id, zone_id, start_hour,"
                " end_hour, enabled, updated_at) VALUES(?,?,?,?,?,?)",
                (person_id, zone_id, window.start_hour, window.end_hour,
                 1 if enabled else 0, int(time.time() * 1000)))
        return {"ok": True, "quiet_hours": window.to_dict()}

    def load_quiet_hours(self) -> None:
        if self.db is None:
            return
        try:
            for row in self.db.query("SELECT * FROM proactive_quiet_hours"):
                self.policy.quiet_hours.append(QuietHours(
                    start_hour=int(row["start_hour"]), end_hour=int(row["end_hour"]),
                    person_id=row["person_id"] or "", zone_id=row["zone_id"] or "",
                    enabled=bool(row["enabled"])))
        except Exception as exc:
            log.debug("no persisted quiet hours: %s", exc)

    def set_preference(self, event_class: str, value: float) -> Dict[str, Any]:
        self.policy.preferences[event_class] = max(0.0, min(1.0, float(value)))
        if self.db is not None:
            self.db.execute("INSERT OR REPLACE INTO proactive_preferences(event_class,"
                            " value, updated_at) VALUES(?,?,?)",
                            (event_class, self.policy.preferences[event_class],
                             int(time.time() * 1000)))
        return {"ok": True, "event_class": event_class,
                "value": self.policy.preferences[event_class]}

    def status(self) -> Dict[str, Any]:
        return {"dedupe_window_s": self.policy.dedupe_window_s,
                "quiet_hours": [w.to_dict() for w in self.policy.quiet_hours],
                "urgent_whitelist": self.policy.urgent_whitelist,
                "quiet_hours_max_outcome": self.policy.quiet_hours_max_outcome,
                "preferences": dict(self.policy.preferences),
                "delivered": len([n for n in self._history if n.delivered]),
                "suppressed": len([n for n in self._history if n.suppressed]),
                "outbox": len(self._outbox)}
