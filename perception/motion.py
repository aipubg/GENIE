"""Motion detection (perception/motion.py) — the cheap gate before any vision call.

The spec is explicit (§6.8): a camera must **not** stream to a cloud model. The pipeline is

    camera → cheap local change/motion detection → interesting event
           → frame/sample → vision provider *only if necessary* → structured event

So this module is the thing that makes "no 24/7 streaming" true rather than aspirational: frames
are compared locally, in memory, and a frame is only ever *offered* onward when something actually
changed. A static scene produces nothing at all.

The detector is deliberately simple and dependency-free (downsample → mean absolute difference →
threshold + hysteresis). Simple is a feature here: it must run on a Raspberry Pi and its behaviour
must be explainable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.logging_setup import get_logger

log = get_logger("perception.motion")

#: A frame is a flat list of 0..255 luma samples plus its dimensions.
Frame = Sequence[int]


def downsample(samples: Sequence[int], width: int, height: int,
               target: int = 32) -> List[float]:
    """Reduce a frame to a small grid. Cheap, and it removes sensor noise."""
    if width <= 0 or height <= 0 or not samples:
        return []
    step_x = max(1, width // target)
    step_y = max(1, height // target)
    out: List[float] = []
    for y in range(0, height, step_y):
        row_start = y * width
        for x in range(0, width, step_x):
            index = row_start + x
            if index < len(samples):
                out.append(float(samples[index]))
    return out


def frame_difference(previous: Sequence[float], current: Sequence[float]) -> float:
    """Mean absolute difference, normalised to 0..1."""
    if not previous or not current:
        return 0.0
    span = min(len(previous), len(current))
    if span == 0:
        return 0.0
    total = 0.0
    for index in range(span):
        total += abs(previous[index] - current[index])
    return (total / span) / 255.0


@dataclass
class MotionResult:
    motion: bool
    score: float
    changed_ratio: float = 0.0
    reason: str = ""
    started: bool = False        # rising edge: motion just began
    stopped: bool = False        # falling edge: motion just ended
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"motion": self.motion, "score": round(self.score, 4),
                "changed_ratio": round(self.changed_ratio, 4), "reason": self.reason,
                "started": self.started, "stopped": self.stopped, "ts": self.ts}


class MotionDetector:
    """Frame-difference motion detection with hysteresis.

    Hysteresis matters: a single noisy frame must not raise an event, and motion must not
    "flutter" on and off while someone is standing still but the sensor is noisy. Motion starts
    only after `start_frames` consecutive above-threshold frames and stops after `stop_frames`
    consecutive below-threshold frames.
    """

    def __init__(self, *, threshold: float = 0.02, start_frames: int = 2,
                 stop_frames: int = 3, grid: int = 32):
        self.threshold = float(threshold)
        self.start_frames = max(1, int(start_frames))
        self.stop_frames = max(1, int(stop_frames))
        self.grid = int(grid)
        self._previous: Optional[List[float]] = None
        self._above = 0
        self._below = 0
        self.active = False
        self.frames_seen = 0

    # ------------------------------------------------------------------ feeding
    def observe(self, samples: Frame, width: int, height: int) -> MotionResult:
        """Feed one frame. Returns whether motion is now considered active."""
        small = downsample(samples, width, height, self.grid)
        self.frames_seen += 1
        if self._previous is None:
            self._previous = small
            return MotionResult(False, 0.0, reason="baseline frame")
        score = frame_difference(self._previous, small)
        self._previous = small
        return self._classify(score)

    def _classify(self, score: float) -> MotionResult:
        above = score >= self.threshold
        if above:
            self._above += 1
            self._below = 0
        else:
            self._below += 1
            self._above = 0

        started = stopped = False
        reason = ""
        if not self.active and self._above >= self.start_frames:
            self.active = True
            started = True
            reason = f"motion started (score {score:.3f} >= {self.threshold})"
        elif self.active and self._below >= self.stop_frames:
            self.active = False
            stopped = True
            reason = f"motion stopped (score {score:.3f} < {self.threshold})"
        else:
            reason = ("above threshold" if above else "below threshold")

        return MotionResult(self.active, score, changed_ratio=score, reason=reason,
                            started=started, stopped=stopped)

    def reset(self) -> None:
        self._previous = None
        self._above = 0
        self._below = 0
        self.active = False
        self.frames_seen = 0

    def status(self) -> Dict[str, Any]:
        return {"active": self.active, "threshold": self.threshold,
                "frames_seen": self.frames_seen,
                "start_frames": self.start_frames, "stop_frames": self.stop_frames}


def synthetic_frame(width: int, height: int, *, fill: int = 100,
                    block: Optional[Dict[str, int]] = None) -> List[int]:
    """Build a frame for tests and for exercising the pipeline without a camera.

    This is a frame *source*, not a fake detector: the detector still does real arithmetic on
    real pixel values. It exists because this machine has no camera.
    """
    frame = [fill] * (width * height)
    if block:
        for y in range(block.get("y", 0), min(height, block.get("y", 0) + block.get("h", 0))):
            for x in range(block.get("x", 0), min(width, block.get("x", 0) + block.get("w", 0))):
                frame[y * width + x] = block.get("value", 255)
    return frame
