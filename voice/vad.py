"""Voice activity detection (voice/vad).

Adaptive energy + zero-crossing VAD, dependency-free (works with plain Python lists) and
optionally accelerated by numpy. It is the component that decides:

    silence -> speech started   (this is what makes barge-in possible)

A VAD must never be the reason a user's sentence is cut off, so the thresholds adapt to the
measured noise floor instead of being fixed constants.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.logging_setup import get_logger

log = get_logger("voice.vad")

FRAME_MS = 20


def _rms(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    try:
        import numpy as np
        arr = np.asarray(samples, dtype="float32")
        return float(np.sqrt((arr ** 2).mean()))
    except Exception:
        return math.sqrt(sum(float(s) * float(s) for s in samples) / len(samples))


def _zcr(samples: Sequence[float]) -> float:
    if len(samples) < 2:
        return 0.0
    try:
        import numpy as np
        arr = np.asarray(samples, dtype="float32")
        return float((np.abs(np.diff(np.signbit(arr)))).mean())
    except Exception:
        crossings = sum(1 for a, b in zip(samples, samples[1:])
                        if (a >= 0) != (b >= 0))
        return crossings / (len(samples) - 1)


@dataclass
class VadConfig:
    frame_ms: int = FRAME_MS
    start_frames: int = 3            # consecutive speech frames needed to open a segment
    end_silence_ms: int = 700        # trailing silence that closes a segment
    min_speech_ms: int = 250         # ignore coughs/clicks shorter than this
    max_segment_ms: int = 30_000
    energy_multiplier: float = 2.6   # speech threshold relative to the noise floor
    absolute_floor: float = 0.006    # never treat pure silence as speech
    calibrate_frames: int = 25       # frames used to learn the room noise floor


@dataclass
class VadEvent:
    kind: str                        # speech_start | speech_end | frame
    ts: float = field(default_factory=time.time)
    rms: float = 0.0
    duration_ms: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ts": self.ts, "rms": round(self.rms, 5),
                "duration_ms": self.duration_ms, "reason": self.reason}


class Vad:
    """Streaming VAD: feed frames, get speech_start / speech_end events."""

    def __init__(self, config: Optional[VadConfig] = None, sample_rate: int = 16_000):
        self.cfg = config or VadConfig()
        self.sample_rate = sample_rate
        self.noise_floor = self.cfg.absolute_floor
        self._calibrated = 0
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False
        self._segment_start = 0.0
        self.stats = {"frames": 0, "speech_frames": 0, "segments": 0}

    # ------------------------------------------------------------------ config
    @property
    def threshold(self) -> float:
        return max(self.cfg.absolute_floor, self.noise_floor * self.cfg.energy_multiplier)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    # ------------------------------------------------------------------ stream
    def process_frame(self, samples: Sequence[float]) -> Optional[VadEvent]:
        """Feed one frame (~20 ms). Returns an event when the speech state changes."""
        rms = _rms(samples)
        zcr = _zcr(samples)
        self.stats["frames"] += 1

        # Learn the room while we are not in speech — but NEVER calibrate on a frame that is
        # already above the current threshold, or a person starting to talk immediately
        # teaches the VAD that speech is noise (A-038).
        if (not self._in_speech and self._calibrated < self.cfg.calibrate_frames
                and rms < self.threshold):
            self.noise_floor = (self.noise_floor * 0.7) + (max(rms, 1e-6) * 0.3)
            self._calibrated += 1

        is_speech = rms >= self.threshold and zcr < 0.6

        if is_speech:
            self.stats["speech_frames"] += 1
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            if not self._in_speech:
                self._speech_frames = 0
                # adapt the floor slowly while idle so noise changes are tracked
                self.noise_floor = (self.noise_floor * 0.95) + (max(rms, 1e-6) * 0.05)

        # ---- speech start
        if not self._in_speech and self._speech_frames >= self.cfg.start_frames:
            self._in_speech = True
            self._segment_start = time.time()
            self.stats["segments"] += 1
            return VadEvent("speech_start", rms=rms, reason=f"threshold={self.threshold:.4f}")

        # ---- speech end
        if self._in_speech:
            silence_ms = self._silence_frames * self.cfg.frame_ms
            duration_ms = int((time.time() - self._segment_start) * 1000)
            if silence_ms >= self.cfg.end_silence_ms:
                self._in_speech = False
                self._speech_frames = 0
                self._silence_frames = 0
                if duration_ms < self.cfg.min_speech_ms:
                    return VadEvent("speech_end", rms=rms, duration_ms=duration_ms,
                                    reason="too_short_ignored")
                return VadEvent("speech_end", rms=rms, duration_ms=duration_ms,
                                reason="silence_timeout")
            if duration_ms >= self.cfg.max_segment_ms:
                self._in_speech = False
                self._speech_frames = 0
                self._silence_frames = 0
                return VadEvent("speech_end", rms=rms, duration_ms=duration_ms,
                                reason="max_segment_length")
        return None

    def process_chunk(self, samples: Sequence[float],
                      sample_rate: Optional[int] = None) -> List[VadEvent]:
        """Feed an arbitrary chunk; it is split into frames internally."""
        rate = sample_rate or self.sample_rate
        frame_len = max(1, int(rate * self.cfg.frame_ms / 1000))
        events: List[VadEvent] = []
        for start in range(0, len(samples), frame_len):
            frame = samples[start:start + frame_len]
            if len(frame) < frame_len // 2:
                break
            event = self.process_frame(frame)
            if event:
                events.append(event)
        return events

    def reset(self) -> None:
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False

    def status(self) -> Dict[str, Any]:
        return {"threshold": round(self.threshold, 5),
                "noise_floor": round(self.noise_floor, 5),
                "in_speech": self._in_speech,
                "calibrated_frames": self._calibrated,
                "stats": dict(self.stats)}


# ---------------------------------------------------------------------- helpers
def synthesize_speech(duration_ms: int, sample_rate: int = 16_000,
                      amplitude: float = 0.25, tone_hz: float = 180.0) -> List[float]:
    """Generate a speech-like test signal (used by tests and offline development)."""
    n = int(sample_rate * duration_ms / 1000)
    return [amplitude * math.sin(2 * math.pi * tone_hz * t / sample_rate)
            for t in range(n)]


def synthesize_silence(duration_ms: int, sample_rate: int = 16_000,
                       noise: float = 0.0008) -> List[float]:
    import random
    n = int(sample_rate * duration_ms / 1000)
    return [random.uniform(-noise, noise) for _ in range(n)]
