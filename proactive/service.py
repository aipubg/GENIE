"""Proactive service (proactive/service.py) — the facade.

Turns things GENIE notices into things GENIE might say, and answers the one question the owner
will always have: *"why am I seeing this?"*

Golden #13 lives here: a **risky file delete** must produce a **timely** warning that is
**non-annoying**. Timely means the warning is produced *before* the action runs — the orchestrator
pre-scans the plan and consults this service, so a destructive step is flagged while it can still
be stopped. Non-annoying means it goes through the ordinary notification rules: duplicate
suppression applies, quiet hours clamp the channel — but a destructive action is marked as an
urgent override, so quiet hours can reduce it to a silent toast and never to nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from proactive.contracts import Candidate, EventClass, Notification, Outcome
from proactive.notifications import NotificationPolicy, Notifier

log = get_logger("proactive.service")

#: Capabilities that destroy or overwrite something the owner cannot trivially get back.
DESTRUCTIVE_CAPABILITIES = {
    "files.delete", "files.move", "files.write", "files.append",
    "process.kill", "application.close", "window.close",
    "shell.run", "shell.powershell_json",
}

#: Operations whose whole purpose is removal — these get the strongest warning.
PURE_DESTRUCTIVE = {"files.delete", "process.kill"}


@dataclass
class RiskAssessment:
    risky: bool = False
    severity: str = ""                  # low | medium | high
    capability: str = ""
    detail: str = ""
    needs_confirmation: bool = False
    notification: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"risky": self.risky, "severity": self.severity, "capability": self.capability,
                "detail": self.detail, "needs_confirmation": self.needs_confirmation,
                "notification": self.notification}


class ProactiveService:
    def __init__(self, db=None, *, audit=None, presence=None, devices=None,
                 deliver=None, config: Optional[Dict[str, Any]] = None):
        self.db = db
        self.config = config or {}
        policy = NotificationPolicy(
            dedupe_window_s=int(self.config.get("dedupe_window_s", 300)))
        self.notifier = Notifier(policy=policy, db=db, audit=audit, presence=presence,
                                 devices=devices, deliver=deliver)
        self.notifier.load_quiet_hours()
        self._load_preferences()

    def _load_preferences(self) -> None:
        if self.db is None:
            return
        try:
            for row in self.db.query("SELECT * FROM proactive_preferences"):
                self.notifier.policy.preferences[row["event_class"]] = float(row["value"])
        except Exception as exc:
            log.debug("no persisted preferences: %s", exc)

    # ------------------------------------------------------------------- input
    def consider(self, candidate: Candidate, *, person_id: str = "owner") -> Notification:
        """The single entry point: score, apply the MUST rules, deliver, record."""
        preference = self.notifier.policy.preferences.get(candidate.event_class)
        if preference is not None:
            candidate.user_preference = preference
        return self.notifier.submit(candidate, person_id=person_id)

    def observe_perception(self, event: Dict[str, Any]) -> Notification:
        """A perception event becomes a candidate; presence gives it relevance."""
        event_type = str(event.get("type", "motion_detected"))
        confidence = float(event.get("confidence", 0.5) or 0.5)
        interesting = event_type in ("person_entered", "person_left")
        return self.consider(Candidate(
            event_class=EventClass.PERCEPTION.value,
            title=f"{event_type.replace('_', ' ')} in {event.get('zone_id', '?')}",
            detail=f"observed by {event.get('source', 'a sensor')}",
            urgency=0.35 if interesting else 0.2,
            relevance=0.5 if interesting else 0.3,
            confidence=confidence,
            interruption_cost=0.5,
            zone_id=str(event.get("zone_id", "")),
            source_event=str(event.get("event_id", ""))))

    def observe_mission_failure(self, mission_id: str, goal: str, error: str) -> Notification:
        return self.consider(Candidate(
            event_class=EventClass.MISSION_FAILED.value,
            title=f"mission failed: {goal[:60]}",
            detail=error[:200], urgency=0.6, relevance=0.8, confidence=0.9,
            interruption_cost=0.4, mission_id=mission_id, source_event=mission_id))

    def observe_device_offline(self, device_id: str, detail: str = "") -> Notification:
        return self.consider(Candidate(
            event_class=EventClass.DEVICE_OFFLINE.value,
            title=f"{device_id} went offline",
            detail=detail or "the device stopped answering",
            urgency=0.4, relevance=0.5, confidence=0.95, interruption_cost=0.35,
            device_id=device_id, source_event=device_id))

    # ---------------------------------------------------------------- golden #13
    def risk_for(self, capability: str, params: Optional[Dict[str, Any]] = None,
                 *, mission_id: str = "") -> RiskAssessment:
        """Assess one capability for destructive risk (no notification yet)."""
        params = params or {}
        if capability not in DESTRUCTIVE_CAPABILITIES:
            return RiskAssessment(risky=False, capability=capability)
        target = str(params.get("path") or params.get("target") or params.get("name")
                     or params.get("command") or "")
        pure = capability in PURE_DESTRUCTIVE
        severity = "high" if pure else "medium"
        detail = (f"{capability} would {'delete' if pure else 'change'} "
                  f"{target or 'something'}")
        return RiskAssessment(risky=True, severity=severity, capability=capability,
                              detail=detail, needs_confirmation=pure)

    def warn_risky_action(self, capability: str, params: Optional[Dict[str, Any]] = None,
                          *, mission_id: str = "", person_id: str = "owner") -> RiskAssessment:
        """Assess *and* raise a timely warning. Called before the action runs."""
        assessment = self.risk_for(capability, params, mission_id=mission_id)
        if not assessment.risky:
            return assessment
        target = str((params or {}).get("path") or (params or {}).get("target")
                     or (params or {}).get("name") or (params or {}).get("command") or "")
        pure = capability in PURE_DESTRUCTIVE
        notification = self.consider(Candidate(
            event_class=EventClass.RISKY_ACTION.value,
            title=f"about to {'delete' if pure else 'modify'} {target or 'something'}",
            detail=assessment.detail,
            urgency=0.9 if pure else 0.6,
            relevance=0.85,
            confidence=0.95,
            interruption_cost=0.2 if pure else 0.4,
            # destructive work may be reduced to a silent toast, never silenced entirely
            urgent_override=pure,
            mission_id=mission_id, source_event=f"{capability}:{target}"),
            person_id=person_id)
        assessment.notification = notification.to_dict()
        return assessment

    def prescan(self, tasks: List[Dict[str, Any]], *, mission_id: str = "",
                person_id: str = "owner") -> List[Dict[str, Any]]:
        """Warn about every destructive step *before* any of them runs.

        This is what makes the warning timely rather than a post-mortem: the orchestrator calls
        this after planning and before execution.
        """
        out: List[Dict[str, Any]] = []
        for task in tasks or []:
            capability = str(task.get("capability", ""))
            params = dict(task.get("params") or {})
            if task.get("target"):
                params.setdefault("target", task["target"])
            assessment = self.warn_risky_action(capability, params, mission_id=mission_id,
                                               person_id=person_id)
            if assessment.risky:
                out.append(assessment.to_dict())
        return out

    # ------------------------------------------------------------------ policy
    def set_quiet_hours(self, **kwargs) -> Dict[str, Any]:
        return self.notifier.set_quiet_hours(**kwargs)

    def set_preference(self, event_class: str, value: float) -> Dict[str, Any]:
        return self.notifier.set_preference(event_class, value)

    def explain(self, notification_id: str) -> Dict[str, Any]:
        text = self.notifier.explain(notification_id)
        if text is None:
            return {"ok": False, "error": f"unknown notification {notification_id}"}
        return {"ok": True, "notification_id": notification_id, "why": text}

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        status = self.notifier.status()
        status["outbox"] = self.notifier.outbox()
        status["history"] = self.notifier.history(limit=10)
        return status
