"""Audio preprocessing (voice/audio).

The stage between the microphone and the recognizer. Real microphones deliver quiet, slightly
offset signals (a laptop mic in a room can peak at −40 dBFS), and recognizers are trained on
well-levelled audio — so without this stage a perfectly clear sentence is "heard" but not
understood.

Implemented here:
  * DC offset removal (microphone bias)
  * one-pole high-pass to cut rumble/hum below ~80 Hz
  * peak normalisation with a bounded gain (automatic gain control)

The applied gain is always reported, so nothing is hidden: a transcript produced from a 20×
boosted signal says so in the metrics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from core.logging_setup import get_logger

log = get_logger("voice.audio")


@dataclass
class PreprocessResult:
    samples: List[float]
    gain: float = 1.0
    peak_before: float = 0.0
    peak_after: float = 0.0
    dc_offset: float = 0.0
    clipped: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"gain": round(self.gain, 2), "peak_before": round(self.peak_before, 5),
                "peak_after": round(self.peak_after, 5),
                "dc_offset": round(self.dc_offset, 6), "clipped_samples": self.clipped}


def dc_remove(samples: Sequence[float]) -> Tuple[List[float], float]:
    if not samples:
        return [], 0.0
    try:
        import numpy as np
        arr = np.asarray(samples, dtype="float32")
        offset = float(arr.mean())
        return (arr - offset).tolist(), offset
    except Exception:
        offset = sum(samples) / len(samples)
        return [s - offset for s in samples], offset


def highpass(samples: Sequence[float], sample_rate: int = 16_000,
             cutoff_hz: float = 80.0) -> List[float]:
    """One-pole high-pass filter (cheap, no dependencies)."""
    if not samples:
        return []
    rc = 1.0 / (2 * math.pi * cutoff_hz)
    dt = 1.0 / sample_rate
    alpha = rc / (rc + dt)
    out: List[float] = []
    previous_in = float(samples[0])
    previous_out = 0.0
    for value in samples:
        current = float(value)
        filtered = alpha * (previous_out + current - previous_in)
        out.append(filtered)
        previous_in = current
        previous_out = filtered
    return out


def normalize(samples: Sequence[float], target_peak: float = 0.9,
              max_gain: float = 40.0, min_peak: float = 0.002) -> Tuple[List[float], float, int]:
    """Peak-normalise with a bounded gain. Returns (samples, gain, clipped_count)."""
    if not samples:
        return [], 1.0, 0
    peak = max(abs(float(s)) for s in samples)
    if peak < min_peak:
        return list(samples), 1.0, 0            # pure silence: do not amplify noise
    gain = min(max_gain, target_peak / peak)
    out: List[float] = []
    clipped = 0
    for value in samples:
        scaled = float(value) * gain
        if scaled > 1.0:
            scaled, clipped = 1.0, clipped + 1
        elif scaled < -1.0:
            scaled, clipped = -1.0, clipped + 1
        out.append(scaled)
    return out, gain, clipped


def preprocess(samples: Sequence[float], sample_rate: int = 16_000,
               target_peak: float = 0.9, apply_highpass: bool = True,
               max_gain: float = 40.0) -> PreprocessResult:
    """Full preprocessing chain used before speech recognition."""
    if not samples:
        return PreprocessResult(samples=[])
    peak_before = max(abs(float(s)) for s in samples)
    cleaned, offset = dc_remove(samples)
    if apply_highpass:
        cleaned = highpass(cleaned, sample_rate)
    normalised, gain, clipped = normalize(cleaned, target_peak=target_peak,
                                           max_gain=max_gain)
    peak_after = max((abs(s) for s in normalised), default=0.0)
    result = PreprocessResult(samples=normalised, gain=gain, peak_before=peak_before,
                              peak_after=peak_after, dc_offset=offset, clipped=clipped)
    log.debug("preprocess: peak %.5f -> %.5f (gain %.2fx, clipped %s)",
              peak_before, peak_after, gain, clipped)
    return result
