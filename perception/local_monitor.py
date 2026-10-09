"""Owner-enabled, local-only camera and desktop observation.

Reuses DesktopAwareness, PerceptionService and the memory-owned encrypted
timeline. No Gemini calls, face identification, keylogging or model training.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from perception.camera import OpenCvCameraProvider


@dataclass
class MonitorSettings:
    desktop_memory: bool = False
    camera_enabled: bool = False
    auto_start: bool = False
    camera_index: int = 0
    camera_zone: str = "office"
    cloud_camera_consent: bool = False
    interval_s: int = 3
    retention_days: int = 30


class LocalPersonDetector:
    name = "opencv-local-hog"

    def __init__(self):
        self._hog = None

    def available(self):
        try:
            import cv2
            return True
        except ImportError:
            return False

    def reason(self):
        return "Local person detection requires OpenCV"

    def classify(self, frame, width, height):
        import cv2
        import numpy as np
        if self._hog is None:
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        pixels = np.array(frame, dtype=np.uint8).reshape(height, width)
        if width < 64 or height < 128:
            return {"ok": False, "detail": "Frame too small for local person detection"}
        boxes, weights = self._hog.detectMultiScale(pixels, winStride=(8, 8), padding=(8, 8), scale=1.1)
        return {"ok": True, "label": "person" if len(boxes) else "unknown", "confidence": 0.6,
                "provider": self.name, "detail": "Local heuristic detection; not identity recognition"}


class LocalMonitor:
    def __init__(self, perception, desktop, observations, data_dir, vault=None):
        self.perception, self.desktop, self.observations = perception, desktop, observations
        self.vault = vault
        self.path = Path(data_dir) / "local_monitor.json"
        self.settings = MonitorSettings()
        try:
            self.settings = self._validate(json.loads(self.path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.camera_error = self.memory_error = ""
        self.last_event = ""
        self.last_event_at = 0.0
        self._last_desktop = None
        self._last_camera_attempt = 0.0
        self._last_trim = 0.0
        self._camera_configured = False
        self._owned_session = None
        self._vision = LocalPersonDetector()
        if self.settings.auto_start:
            self.start()

    def _validate(self, data):
        if set(data) - set(MonitorSettings.__dataclass_fields__):
            raise ValueError("Unknown local awareness setting")
        settings = MonitorSettings(**data)
        for field in ("desktop_memory", "camera_enabled", "auto_start", "cloud_camera_consent"):
            if type(getattr(settings, field)) is not bool:
                raise ValueError(field + " must be boolean")
        for field, low, high in (("camera_index", 0, 15), ("interval_s", 1, 60), ("retention_days", 1, 365)):
            value = getattr(settings, field)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{field} must be between {low} and {high}")
        if settings.camera_zone not in {zone.zone_id for zone in self.perception.zones()}:
            raise ValueError("Choose an existing camera zone")
        return settings

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def status(self):
        return {"settings": asdict(self.settings), "running": self.running,
                "camera_error": self.camera_error, "memory_error": self.memory_error,
                "camera_active": self._owns_camera(),
                "last_event": self.last_event, "last_event_at": self.last_event_at,
                "storage": self.observations.status(), "cloud_upload": False,
                "live_camera_sharing_allowed": self.settings.cloud_camera_consent}

    def _owns_camera(self):
        return (self._owned_session is not None and not self._owned_session.closed
                and self.perception._sessions.get(self.settings.camera_zone) is self._owned_session)

    def preview(self, *, for_cloud=False):
        if not self.running or not self._owns_camera():
            return b""
        if for_cloud and not self.settings.cloud_camera_consent:
            return b""
        return getattr(self.perception.camera, "latest_jpeg", b"")

    def _close_owned_camera(self):
        with self.perception._camera_lock:
            if self._owns_camera():
                self.perception.close_camera(self.settings.camera_zone)
            self._owned_session = None

    def configure(self, changes):
        with self._lock:
            try:
                settings = self._validate({**asdict(self.settings), **changes})
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
            if not self.stop()["ok"]:
                return {"ok": False, "error": "Camera is still stopping; wait before changing sources."}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
            temp.replace(self.path)
            self.settings = settings
            self._camera_configured = False
            self._last_desktop = None
            self._last_camera_attempt = 0
            self.camera_error = self.memory_error = ""
            if settings.camera_enabled:
                self.perception.set_policy(settings.camera_zone, camera=True, motion=True,
                                           retention_s=settings.retention_days * 86400)
            if settings.desktop_memory or settings.camera_enabled:
                self.start()
            return {"ok": True, **self.status()}

    def start(self):
        with self._lock:
            if self.running:
                return {"ok": True, "already_running": True}
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="local-awareness", daemon=True)
            self._thread.start()
            return {"ok": True}

    def stop(self):
        with self._lock:
            self._stop.set()
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(6)
            return {"ok": not self.running}

    def _desktop_tick(self):
        if not self.desktop.settings.enabled or not self.desktop.is_running or self.desktop.is_paused:
            return
        observation = self.desktop.observe()
        if observation.locked or observation.paused or not observation.foreground:
            return
        foreground = observation.foreground
        evidence = {"process": foreground.get("process", ""), "title": str(foreground.get("title", ""))[:400],
                    "monitor": foreground.get("monitor"), "display_count": observation.display_count,
                    "source": "desktop_observation", "inferred_preference": False}
        signature = json.dumps(evidence, sort_keys=True)
        if signature != self._last_desktop:
            self.observations.append("desktop", evidence)
            self._last_desktop = signature

    def _camera_tick(self):
        with self.perception._camera_lock:
            return self._camera_tick_locked()

    def _camera_tick_locked(self):
        zone = self.settings.camera_zone
        session = self.perception._sessions.get(zone)
        if session is not None and session is not self._owned_session:
            self.camera_error = "This zone already has a camera session. Close it before monitoring."
            return
        if session is None:
            if self._last_camera_attempt and time.monotonic() - self._last_camera_attempt < 30:
                return
            self._last_camera_attempt = time.monotonic()
            if not self._camera_configured:
                with self.perception._camera_lock:
                    if self.perception._sessions:
                        self.camera_error = "Another camera session is active. Close it before monitoring."
                        return
                    self.perception.camera = OpenCvCameraProvider([self.settings.camera_index])
                    self._camera_configured = True
            opened = self.perception.activate_camera(zone)
            if not opened.get("ok"):
                self.camera_error = opened.get("detail", "Camera unavailable")
                return
            self._owned_session = self.perception._sessions.get(zone)
        result = self.perception.observe(zone, vision_provider=self._vision)
        if not result.get("ok"):
            self.camera_error = result.get("detail", "Camera observation failed")
            self._close_owned_camera()
            return
        self.camera_error = ""
        event = result.get("event")
        if event and time.time() - self.last_event_at >= 30:
            person = event.get("type") == "person_entered"
            self.last_event = ("Possible person detected" if person else "Motion detected") + f" at {zone}."
            self.last_event_at = time.time()
            self.observations.append("camera", {"message": self.last_event, "event": event,
                                                 "identity": "unknown", "source": "local_detection"})

    def _run(self):
        try:
            while not self._stop.is_set():
                if self.settings.desktop_memory:
                    try:
                        self._desktop_tick()
                        self.memory_error = ""
                    except Exception:
                        self.memory_error = "Local observation could not be encrypted or stored."
                if self.settings.camera_enabled and not self._stop.is_set():
                    try:
                        self._camera_tick()
                    except Exception:
                        self.camera_error = "Local camera processing failed. Check the source and OpenCV runtime."
                if time.monotonic() - self._last_trim > 60:
                    try:
                        self.observations.trim(self.settings.retention_days)
                    except Exception:
                        self.memory_error = "Local observation retention could not be applied."
                    self._last_trim = time.monotonic()
                self._stop.wait(self.settings.interval_s)
        finally:
            self._close_owned_camera()
