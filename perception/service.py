"""Perception service (perception/service.py) — the facade.

Ties zones, policies, the camera, motion detection, presence and fusion together, and enforces the
privacy rules that make the whole thing acceptable to live with:

* **Per-zone policy decides, not the caller.** Asking for a bedroom camera does not get one.
* **Camera activation fails closed** when the mandatory indicator cannot be shown.
* **Frames are never streamed.** A camera session is bounded in frames and in time, every grab is
  counted, and at most **one** frame per interesting event is offered to a vision provider.
* **Events expire.** Each zone has a retention window; `purge()` hard-deletes.
* **No sensor is assumed.** Every degradation is reported in the fused state.

The vision path goes through the existing model gateway (no second provider stack): one frame, on
an interesting event, only when a vision-capable provider is actually configured.
"""
from __future__ import annotations

import time
import threading
from functools import wraps
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, DataClass, ModelRequirement
from core.events import get_bus
from core.logging_setup import get_logger
from perception.camera import (CameraProvider, CameraSession, IndicatorKind, NullCameraProvider,
                               select_provider)
from perception.contracts import (EventType, MicPolicy, PerceptionEvent, PRIVATE_ZONE_KINDS,
                                 SensingPolicy, Zone, ZoneKind, default_policy)
from perception.fusion import FusionService
from perception.motion import MotionDetector
from perception.presence import PresenceEngine

log = get_logger("perception.service")


def camera_locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._camera_lock:
            return method(self, *args, **kwargs)
    return call

#: Zones GENIE knows out of the box. The owner can add more; policies persist in the database.
BUILTIN_ZONES = (
    ("office", "Office", ZoneKind.OFFICE.value),
    ("workshop", "Workshop", ZoneKind.WORKSHOP.value),
    ("living", "Living room", ZoneKind.LIVING.value),
    ("kitchen", "Kitchen", ZoneKind.KITCHEN.value),
    ("bedroom", "Bedroom", ZoneKind.BEDROOM.value),
    ("bathroom", "Bathroom", ZoneKind.BATHROOM.value),
    ("hallway", "Hallway", ZoneKind.HALLWAY.value),
)


class VisionProvider:
    """Contract for classifying a single frame. Implementations must be honest about failure."""

    name = "none"

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        return "no vision provider configured"

    def classify(self, frame: Any, width: int, height: int) -> Dict[str, Any]:
        return {"ok": False, "error_code": "unavailable", "detail": self.reason()}


class GatewayVisionProvider(VisionProvider):
    """Classify one frame through the existing model gateway.

    No second provider stack: the same gateway, policy registry and health tracking that serve
    everything else. If no vision-capable provider is configured the call fails honestly and the
    event stays a plain motion event.
    """

    name = "gateway"

    def __init__(self, gateway=None, *, allowed=False):
        self.gateway = gateway
        self.allowed = allowed

    def available(self) -> bool:
        return self.gateway is not None and self.allowed

    def reason(self) -> str:
        if not self.allowed:
            return "Cloud camera analysis has not been enabled by the owner"
        return "no model gateway available" if self.gateway is None \
            else "gateway configured; a vision-capable provider must exist for a call to succeed"

    def classify(self, frame: Any, width: int, height: int) -> Dict[str, Any]:
        if not self.available():
            return {"ok": False, "error_code": "unavailable", "detail": self.reason()}
        prompt = (f"One camera frame ({width}x{height}) from a home sensor. "
                  "Answer with STRICT JSON only: "
                  '{"label": "person|animal|object|nothing|unknown", "confidence": 0.0-1.0}. '
                  "Do not describe anything else.")
        try:
            import base64
            import io
            from PIL import Image
            pixels = bytes(max(0, min(255, int(value))) for value in frame)
            image = Image.frombytes("L", (width, height), pixels)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=70)
            data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            completion = self.gateway.complete(
                CallContext(person_id="system"),
                ModelRequirement(capability="vision", data_class=DataClass.SENSITIVE,
                                 max_input_tokens=2000),
                [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}}]}], max_tokens=120)
            text = completion.text or ""
            import json as _json
            start, end = text.find("{"), text.rfind("}")
            payload = _json.loads(text[start:end + 1]) if start >= 0 else {}
            label = str(payload.get("label", "unknown"))
            return {"ok": True, "label": label,
                    "confidence": float(payload.get("confidence", 0.5) or 0.5),
                    "provider": completion.provider_id,
                    "detail": f"vision classified the frame as {label}"}
        except Exception as exc:
            return {"ok": False, "error_code": "vision_failed", "detail": str(exc)}


class PerceptionService:
    def __init__(self, db=None, *, audit=None, computer=None, devices=None, gateway=None,
                 camera_provider: Optional[CameraProvider] = None, config: Optional[Dict] = None):
        self.db = db
        self.audit = audit
        self.computer = computer
        self.devices = devices
        self.config = config or {}
        self.camera = camera_provider or select_provider(self.config.get("camera") or {})
        self.vision = GatewayVisionProvider(gateway, allowed=self.config.get("cloud_camera_consent") is True)
        self._camera_lock = threading.RLock()
        self.presence = PresenceEngine()
        self.fusion = FusionService(presence=self.presence)
        self._detectors: Dict[str, MotionDetector] = {}
        self._sessions: Dict[str, CameraSession] = {}
        # one vision classification per motion event, plus a floor between events
        self._last_classified: Dict[str, float] = {}
        self.classify_min_interval_s = float(
            self.config.get("classify_min_interval_s", 10.0))
        self._events: List[PerceptionEvent] = []
        self._bus = get_bus()
        self._zones: Dict[str, Zone] = {}
        self._bootstrap_zones()
        self._load_policies()

    # ------------------------------------------------------------------- zones
    def _bootstrap_zones(self) -> None:
        for zone_id, name, kind in BUILTIN_ZONES:
            self._zones[zone_id] = Zone(zone_id=zone_id, name=name, kind=kind,
                                        policy=default_policy(kind))
        if self.db is None:
            return
        try:
            for row in self.db.query("SELECT * FROM perception_zones"):
                zone_id = row["zone_id"]
                self._zones[zone_id] = Zone(
                    zone_id=zone_id, name=row["name"] or zone_id,
                    kind=row["kind"] or ZoneKind.OTHER.value,
                    policy=default_policy(row["kind"] or ZoneKind.OTHER.value))
        except Exception as exc:
            log.debug("no persisted zones: %s", exc)

    def _load_policies(self) -> None:
        if self.db is None:
            return
        try:
            for row in self.db.query("SELECT * FROM perception_policies"):
                zone = self._zones.get(row["zone_id"])
                if zone is None:
                    continue
                zone.policy = SensingPolicy(
                    camera=bool(row["camera"]), microphone=row["microphone"] or "wake_only",
                    screen=bool(row["screen"]), motion=bool(row["motion"]),
                    retention_s=int(row["retention_s"] or 300),
                    owner_enabled=bool(row["owner_enabled"]))
        except Exception as exc:
            log.debug("no persisted policies: %s", exc)

    def zones(self) -> List[Zone]:
        return list(self._zones.values())

    def zone(self, zone_id: str) -> Optional[Zone]:
        return self._zones.get(zone_id)

    def register_zone(self, zone_id: str, name: str = "", kind: str = "",
                      devices: Optional[List[str]] = None) -> Dict[str, Any]:
        kind = kind or ZoneKind.OTHER.value
        zone = Zone(zone_id=zone_id, name=name or zone_id, kind=kind,
                    devices=list(devices or []), policy=default_policy(kind))
        self._zones[zone_id] = zone
        if self.db is not None:
            self.db.execute("INSERT OR REPLACE INTO perception_zones(zone_id, name, kind,"
                            " devices, created_at) VALUES(?,?,?,?,?)",
                            (zone_id, zone.name, kind, "[]", int(time.time() * 1000)))
        return zone.to_dict()

    # ------------------------------------------------------------------ policy
    def set_policy(self, zone_id: str, *, camera: Optional[bool] = None,
                   microphone: Optional[str] = None, screen: Optional[bool] = None,
                   motion: Optional[bool] = None, retention_s: Optional[int] = None,
                   owner_enabled: bool = True, by: str = "owner") -> Dict[str, Any]:
        """Owner action. Enabling sensing in a private zone is allowed but never implicit."""
        zone = self._zones.get(zone_id)
        if zone is None:
            return {"ok": False, "error": "unknown zone"}
        if microphone is not None and microphone not in {m.value for m in MicPolicy}:
            return {"ok": False, "error": f"unknown microphone policy {microphone}"}
        policy = zone.policy
        if camera is not None:
            policy.camera = bool(camera)
        if microphone is not None:
            policy.microphone = microphone
        if screen is not None:
            policy.screen = bool(screen)
        if motion is not None:
            policy.motion = bool(motion)
        if retention_s is not None:
            policy.retention_s = max(0, int(retention_s))
        policy.owner_enabled = owner_enabled
        self._persist_policy(zone_id, policy)
        if self.audit:
            self.audit.record(who=by, action="perception.policy", why=zone_id,
                              result=str(policy.to_dict()))
        log.info("perception policy for %s set to %s", zone_id, policy.to_dict())
        return {"ok": True, "zone_id": zone_id, "policy": policy.to_dict(),
                "private_zone": zone.private}

    def _persist_policy(self, zone_id: str, policy: SensingPolicy) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO perception_policies(zone_id, camera, microphone, screen,"
            " motion, retention_s, owner_enabled, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (zone_id, 1 if policy.camera else 0, policy.microphone,
             1 if policy.screen else 0, 1 if policy.motion else 0, policy.retention_s,
             1 if policy.owner_enabled else 0, int(time.time() * 1000)))

    # ------------------------------------------------------------------ camera
    @camera_locked
    def camera_status(self) -> Dict[str, Any]:
        return {"available": self.camera.available(), "backend": self.camera.backend,
                "reason": "" if self.camera.available() else self.camera.reason(),
                "cameras": [c.to_dict() for c in self.camera.cameras()],
                "active_sessions": [s.to_dict() for s in self._sessions.values()
                                    if not s.closed]}

    @camera_locked
    def activate_camera(self, zone_id: str, *, camera_id: str = "") -> Dict[str, Any]:
        """Open a bounded camera session — policy first, indicator second, camera last."""
        zone = self._zones.get(zone_id)
        if zone is None:
            return {"ok": False, "error_code": "unknown_zone", "detail": f"no zone {zone_id}"}
        if not zone.policy.camera:
            return {"ok": False, "error_code": "policy_denied",
                    "detail": f"camera sensing is disabled in {zone_id} "
                              f"(private={zone.private}); the owner must enable it"}
        if zone_id in self._sessions:
            return {"ok": False, "error_code": "session_active", "detail": "Camera session already active in this zone"}
        if hasattr(self.camera, "discover"):
            self.camera.discover(camera_id)
        if not self.camera.available():
            return {"ok": False, "error_code": "camera_unavailable",
                    "detail": self.camera.reason()}
        cameras = self.camera.cameras()
        if not cameras:
            return {"ok": False, "error_code": "camera_unavailable",
                    "detail": "no camera device is attached"}
        chosen = camera_id or cameras[0].camera_id
        if chosen not in {camera.camera_id for camera in cameras}:
            for camera in cameras:
                if camera.camera_id not in {s.camera_id for s in self._sessions.values()}:
                    self.camera.release(camera.camera_id)
            return {"ok": False, "error_code": "camera_unavailable", "detail": "Selected camera is not connected"}
        if chosen in {s.camera_id for s in self._sessions.values()}:
            return {"ok": False, "error_code": "camera_busy", "detail": "Selected camera is already in use"}
        # the indicator is mandatory: if it cannot be shown, the camera does not open
        if not self.camera.show_indicator(chosen, True):
            self.camera.release(chosen)
            return {"ok": False, "error_code": "indicator_unavailable",
                    "detail": "the mandatory camera indicator could not be shown, so the camera "
                              "was not activated"}
        session = CameraSession(camera_id=chosen, zone_id=zone_id)
        self._sessions[zone_id] = session
        self._detectors[zone_id] = MotionDetector(
            threshold=float(self.config.get("motion_threshold", 0.02)))
        if self.audit:
            self.audit.record(who="owner", action="perception.camera.activate", why=zone_id,
                              result=f"camera={chosen} indicator=on")
        return {"ok": True, "zone_id": zone_id, "camera_id": chosen,
                "session": session.to_dict(),
                "note": "bounded session: no streaming, indicator on"}

    @camera_locked
    def close_camera(self, zone_id: str) -> Dict[str, Any]:
        session = self._sessions.pop(zone_id, None)
        self._detectors.pop(zone_id, None)
        if session is None:
            return {"ok": False, "error": f"no camera session in {zone_id}"}
        session.closed = True
        self.camera.show_indicator(session.camera_id, False)
        self.camera.release(session.camera_id)
        if self.audit:
            self.audit.record(who="owner", action="perception.camera.close", why=zone_id,
                              result=f"frames={session.frames_grabbed} "
                                     f"vision_calls={session.vision_calls}")
        return {"ok": True, "zone_id": zone_id, "session": session.to_dict()}

    # ------------------------------------------------------------------ observe
    @camera_locked
    def observe(self, zone_id: str, *, classify: bool = True, vision_provider=None) -> Dict[str, Any]:
        """Grab one frame, decide locally whether anything happened, and maybe classify it once."""
        zone = self._zones.get(zone_id)
        if zone is None:
            return {"ok": False, "error_code": "unknown_zone", "detail": f"no zone {zone_id}"}
        if not zone.policy.motion and not zone.policy.camera:
            return {"ok": False, "error_code": "policy_denied",
                    "detail": f"sensing is disabled in {zone_id}"}
        if not self.camera.available():
            return {"ok": False, "error_code": "camera_unavailable",
                    "detail": self.camera.reason()}
        session = self._sessions.get(zone_id)
        if session is None or session.closed:
            return {"ok": False, "error_code": "no_session",
                    "detail": f"no camera session is open in {zone_id}"}
        if session.over_budget() or session.expired():
            self.close_camera(zone_id)
            return {"ok": False, "error_code": "session_exhausted",
                    "detail": "the camera session hit its bound and was closed"}

        grabbed = self.camera.grab(session.camera_id)
        session.frames_grabbed += 1
        if not grabbed.get("ok"):
            return {"ok": False, "error_code": grabbed.get("error_code", "grab_failed"),
                    "detail": grabbed.get("detail", "")}

        detector = self._detectors.setdefault(zone_id, MotionDetector())
        motion = detector.observe(grabbed["frame"], int(grabbed["width"]),
                                  int(grabbed["height"]))

        if not motion.motion:
            # nothing interesting: no event, and above all no vision call
            return {"ok": True, "zone_id": zone_id, "motion": False,
                    "score": round(motion.score, 4),
                    "frames_grabbed": session.frames_grabbed,
                    "vision_calls": session.vision_calls, "detail": motion.reason}

        event = self._emit(zone_id, EventType.MOTION_DETECTED.value, "camera",
                           min(0.8, 0.4 + motion.score * 4),
                           {"motion_score": round(motion.score, 4)})

        # A vision provider is consulted **once per motion event**, on the rising edge — not
        # once per frame. Classifying every frame of a person walking past is exactly the
        # continuous streaming the spec forbids, and it would cost a model call per frame.
        classified = None
        vision = vision_provider or self.vision
        eligible = (classify and zone.policy.camera and motion.started
                    and self._vision_rate_ok(zone_id))
        if eligible and vision.available():
            session.vision_calls += 1
            self._last_classified[zone_id] = time.time()
            classified = vision.classify(grabbed["frame"], int(grabbed["width"]),
                                             int(grabbed["height"]))
            if classified.get("ok"):
                label = str(classified.get("label", "unknown"))
                if label == "person":
                    event = self._emit(zone_id, EventType.PERSON_ENTERED.value, "camera",
                                       float(classified.get("confidence", 0.7)),
                                       {"vision": label, "provider": classified.get("provider")})
                else:
                    event = self._emit(zone_id, EventType.ACTIVITY_CHANGED.value, "camera",
                                       float(classified.get("confidence", 0.5)),
                                       {"vision": label})
            else:
                event.evidence["vision_error"] = classified.get("detail", "")
        elif classify and zone.policy.camera:
            if not motion.started:
                event.evidence["vision_skipped"] = "motion already in progress (one call per event)"
            elif not self._vision_rate_ok(zone_id):
                event.evidence["vision_skipped"] = "rate limited"
            else:
                event.evidence["vision_skipped"] = vision.reason()

        # a camera event is itself presence evidence for that zone
        self.presence.observe(zone_id, "camera", event.confidence, event.type)
        return {"ok": True, "zone_id": zone_id, "motion": True,
                "score": round(motion.score, 4), "event": event.to_dict(),
                "frames_grabbed": session.frames_grabbed,
                "vision_calls": session.vision_calls,
                "streaming": False}

    def _vision_rate_ok(self, zone_id: str) -> bool:
        last = self._last_classified.get(zone_id, 0.0)
        return (time.time() - last) >= self.classify_min_interval_s

    # ------------------------------------------------------------------- events
    def _emit(self, zone_id: str, event_type: str, source: str, confidence: float,
              evidence: Optional[Dict[str, Any]] = None) -> PerceptionEvent:
        event = PerceptionEvent(type=event_type, zone_id=zone_id, confidence=confidence,
                                source=source, evidence=dict(evidence or {}))
        self._events.append(event)
        if len(self._events) > 1000:
            self._events = self._events[-1000:]
        if self.db is not None:
            import json as _json
            try:
                self.db.execute(
                    "INSERT INTO perception_events(event_id, type, zone_id, confidence, source,"
                    " evidence, ts) VALUES(?,?,?,?,?,?,?)",
                    (event.event_id, event.type, event.zone_id, event.confidence, event.source,
                     _json.dumps(event.evidence), event.ts))
            except Exception as exc:
                log.debug("event persist failed: %s", exc)
        self._bus.publish("PERCEPTION_EVENT", event.to_dict())
        log.info("perception: %s in %s (confidence %.2f)", event.type, zone_id, confidence)
        return event

    def recent_events(self, *, limit: int = 50, zone_id: str = "",
                      source: str = "") -> List[Dict[str, Any]]:
        self._expire_events()
        events = self._events
        if zone_id:
            events = [e for e in events if e.zone_id == zone_id]
        if source:
            events = [e for e in events if e.source == source]
        return [e.to_dict() for e in events[-limit:]][::-1]

    def _expire_events(self) -> int:
        """Retention: events live only as long as their zone's policy allows."""
        now_ms = int(time.time() * 1000)
        kept: List[PerceptionEvent] = []
        dropped = 0
        for event in self._events:
            zone = self._zones.get(event.zone_id)
            retention_ms = (zone.policy.retention_s if zone else 300) * 1000
            if retention_ms and (now_ms - event.ts) > retention_ms:
                dropped += 1
                continue
            kept.append(event)
        self._events = kept
        return dropped

    def purge(self, zone_id: str = "") -> Dict[str, Any]:
        """Hard delete derived perception data ('forget X')."""
        before = len(self._events)
        if zone_id:
            self._events = [e for e in self._events if e.zone_id != zone_id]
            if self.db is not None:
                self.db.execute("DELETE FROM perception_events WHERE zone_id=?", (zone_id,))
            self.presence.clear_zone(zone_id)
        else:
            self._events = []
            if self.db is not None:
                self.db.execute("DELETE FROM perception_events")
        removed = before - len(self._events)
        if self.audit:
            self.audit.record(who="owner", action="perception.purge",
                              why=zone_id or "all zones", result=f"{removed} event(s) removed")
        return {"ok": True, "zone_id": zone_id or "*", "removed": removed}

    # ---------------------------------------------------------------- fusion
    def environment(self, zone_id: str = "") -> Dict[str, Any]:
        """The fused state the Context Engine consumes."""
        state = self.fusion.fuse(zone_id=zone_id, ctx=None)
        return state.to_dict()

    def presence_for(self, zone_id: str) -> Dict[str, Any]:
        return self.presence.estimate(zone_id).to_dict()

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        return {"zones": [z.to_dict() for z in self._zones.values()],
                "camera": self.camera_status(),
                "vision": {"available": self.vision.available(), "provider": self.vision.name,
                           "reason": "" if self.vision.available() else self.vision.reason()},
                "presence": self.presence.status(),
                "fusion": self.fusion.status(),
                "events": len(self._events),
                "streaming": False,
                "private_zones": sorted(z.zone_id for z in self._zones.values() if z.private)}
