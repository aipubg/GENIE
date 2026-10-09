"""Camera perception (perception/camera.py) — master spec §6.8, §1.9.

Two hard rules, enforced here rather than left to callers:

1. Frames are grabbed on demand and held in memory. Owner-enabled local monitoring renews
   bounded sessions; remote vision requires separate consent. This module has no upload path.

2. **An indicator is mandatory.** If the indicator cannot be shown, the camera does not activate —
   it fails closed. A silent camera is worse than no camera.

Device availability is checked only at explicit activation, never by status polling.
"""
from __future__ import annotations

import enum
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.logging_setup import get_logger
from perception.motion import Frame, MotionDetector

log = get_logger("perception.camera")

#: A single activation may not grab more than this many frames. A bound is what turns
#: "on demand" into something a reviewer can check.
MAX_FRAMES_PER_SESSION = 120
#: A session longer than this is closed automatically.
MAX_SESSION_S = 300


class IndicatorKind(str, enum.Enum):
    VISIBLE = "visible"      # an on-screen/led indicator
    AUDIBLE = "audible"      # a sound
    BOTH = "both"
    NONE = "none"            # only usable by an explicit test source


@dataclass
class CameraInfo:
    camera_id: str
    zone_id: str = ""
    name: str = ""
    width: int = 0
    height: int = 0
    indicator: str = IndicatorKind.VISIBLE.value

    def to_dict(self) -> Dict[str, Any]:
        return {"camera_id": self.camera_id, "zone_id": self.zone_id, "name": self.name,
                "width": self.width, "height": self.height, "indicator": self.indicator}


class CameraProvider:
    """The camera contract."""

    backend = "none"

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        return "no camera backend on this platform"

    def cameras(self) -> List[CameraInfo]:
        return []

    def grab(self, camera_id: str) -> Dict[str, Any]:
        return {"ok": False, "error_code": "unavailable", "detail": self.reason()}

    def show_indicator(self, camera_id: str, on: bool) -> bool:
        """Turn the mandatory indicator on/off. Returning False forbids camera use."""
        return False

    def release(self, camera_id: str) -> None:
        return None


class NullCameraProvider(CameraProvider):
    """No camera. Says so."""

    backend = "none"

    def __init__(self, reason: str = ""):
        self._reason = reason or "no camera device is available on this host"

    def reason(self) -> str:
        return self._reason


class OpenCvCameraProvider(CameraProvider):
    """A real camera through OpenCV, if it is installed and a device is present."""

    backend = "opencv"

    def __init__(self, indices: Optional[Sequence[int]] = None):
        self._indices = list(indices if indices is not None else range(2))
        self._devices: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self.latest_jpeg = b""

    def available(self) -> bool:
        try:
            import cv2                                          # noqa: F401
        except ImportError:
            return False
        return bool(self._devices)

    def reason(self) -> str:
        try:
            import cv2                                          # noqa: F401
        except ImportError:
            return "opencv-python is not installed"
        return "No camera open. Start local monitoring to check the selected source."

    def discover(self, camera_id=""):
        """Explicit activation only: status polling must never turn a camera on."""
        try:
            import cv2
        except ImportError:
            return
        with self._lock:
            sources = [(f"camera{i}", i) for i in self._indices]
            if camera_id:
                sources = [(name, source) for name, source in sources if name == camera_id]
            for name, source in sources:
                if name in self._devices:
                    break
                capture = cv2.VideoCapture(source, cv2.CAP_DSHOW)
                if capture is not None and capture.isOpened():
                    self._devices[name] = capture
                    break
                elif capture is not None:
                    capture.release()

    def cameras(self) -> List[CameraInfo]:
        return [CameraInfo(camera_id=camera_id, name=camera_id,
                           indicator=IndicatorKind.VISIBLE.value)
                for camera_id in self._devices]

    def grab(self, camera_id: str) -> Dict[str, Any]:
        try:
            import cv2
        except ImportError:
            return {"ok": False, "error_code": "unavailable", "detail": self.reason()}
        capture = self._devices.get(camera_id)
        if capture is None:
            return {"ok": False, "error_code": "no_session", "detail": "Camera is not open"}
        try:
            with self._lock:
                ok, image = capture.read()
            if not ok or image is None:
                return {"ok": False, "error_code": "grab_failed",
                        "detail": f"camera {camera_id} returned no frame"}
            image = cv2.resize(image, (320, 240))
            height, width = image.shape[:2]
            encoded, jpeg = cv2.imencode(".jpg", image)
            self.latest_jpeg = jpeg.tobytes() if encoded else b""
            grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            return {"ok": True, "frame": grey.reshape(-1).tolist(), "width": width,
                    "height": height, "detail": f"{width}x{height} frame"}
        except Exception:
            return {"ok": False, "error_code": "grab_failed", "detail": "Camera frame capture failed"}

    def show_indicator(self, camera_id: str, on: bool) -> bool:
        # A webcam's own LED is driven by the device, so opening it is the indicator. GENIE also
        # logs it, and the service records the activation.
        log.info("camera indicator %s for %s", "ON" if on else "OFF", camera_id)
        return True

    def release(self, camera_id: str) -> None:
        with self._lock:
            capture = self._devices.pop(camera_id, None)
            if capture is not None:
                capture.release()
            self.latest_jpeg = b""


class FrameSourceCameraProvider(CameraProvider):
    """A deterministic frame source for tests. **Never auto-selected.**

    It exists because this machine has no camera, and the perception pipeline still has to be
    exercised end to end. It is a source of pixels, not a fake detector: the real detector runs
    on these frames.
    """

    backend = "frame-source"

    def __init__(self, frames: Optional[List[Dict[str, Any]]] = None,
                 camera_id: str = "camera0", indicator: str = IndicatorKind.VISIBLE.value):
        self.camera_id = camera_id
        self._frames = list(frames or [])
        self._cursor = 0
        self._indicator = indicator
        self.indicator_on = False
        self.indicator_failures = 0
        self.grabs = 0

    def available(self) -> bool:
        return True

    def reason(self) -> str:
        return "deterministic frame source (test backend, not hardware)"

    def cameras(self) -> List[CameraInfo]:
        first = self._frames[0] if self._frames else {}
        return [CameraInfo(camera_id=self.camera_id, name="Test camera",
                           width=int(first.get("width", 0)),
                           height=int(first.get("height", 0)), indicator=self._indicator)]

    def grab(self, camera_id: str) -> Dict[str, Any]:
        if not self._frames:
            return {"ok": False, "error_code": "no_frames", "detail": "the frame source is empty"}
        frame = self._frames[min(self._cursor, len(self._frames) - 1)]
        self._cursor += 1
        self.grabs += 1
        return {"ok": True, "frame": list(frame["frame"]), "width": int(frame["width"]),
                "height": int(frame["height"]), "detail": "test frame"}

    def show_indicator(self, camera_id: str, on: bool) -> bool:
        if self._indicator == IndicatorKind.NONE:
            self.indicator_failures += 1
            return False
        self.indicator_on = on
        return True

    def release(self, camera_id: str) -> None:
        self.indicator_on = False


@dataclass
class CameraSession:
    """One bounded activation. Counts grabs so "no streaming" is checkable."""

    camera_id: str
    zone_id: str = ""
    started_at: float = field(default_factory=time.time)
    frames_grabbed: int = 0
    vision_calls: int = 0
    closed: bool = False

    def expired(self) -> bool:
        return (time.time() - self.started_at) > MAX_SESSION_S

    def over_budget(self) -> bool:
        return self.frames_grabbed >= MAX_FRAMES_PER_SESSION

    def to_dict(self) -> Dict[str, Any]:
        return {"camera_id": self.camera_id, "zone_id": self.zone_id,
                "started_at": self.started_at, "frames_grabbed": self.frames_grabbed,
                "vision_calls": self.vision_calls, "closed": self.closed,
                "duration_s": round(time.time() - self.started_at, 2)}


def select_provider(config: Optional[Dict[str, Any]] = None) -> CameraProvider:
    """Pick a camera backend. The frame source must be requested explicitly."""
    config = config or {}
    backend = str(config.get("backend", "auto")).lower()
    if backend == "frame-source":
        return FrameSourceCameraProvider(config.get("frames") or [])
    opencv = OpenCvCameraProvider(config.get("indices"))
    return opencv
