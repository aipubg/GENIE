"""Real TTS output level (voice/tts_level.py) — the item 15 gap.

The blue gem used a labelled deterministic shimmer while GENIE spoke, because
no output level was measured. This measures one — from the audio GENIE actually
plays.

How it works
------------
When a TTS provider returns an audio FILE, its PCM is read and a real RMS
envelope is computed. That envelope is replayed in wall-clock time while the
file plays, so the level the UI sees is the level of the audio coming out of the
speaker.

When there is no audio buffer to measure (for example a platform TTS engine
that speaks directly, like Windows SAPI), nothing is invented: the meter
reports `available = False` and the UI keeps its labelled deterministic
shimmer. A fake envelope would look identical on screen and be a lie.
"""
from __future__ import annotations

import array
import math
import threading
import time
import wave
from typing import Callable, List, Optional

WINDOW_MS = 50          # envelope resolution
MAX_LEVEL = 1.0


def wav_rms_envelope(path: str, window_ms: int = WINDOW_MS) -> Optional[List[float]]:
    """Compute a real RMS envelope from a WAV file. None if it cannot be read."""
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            if not rate or width not in (1, 2):
                return None
            frames_per_window = max(1, int(rate * window_ms / 1000.0))
            env: List[float] = []
            if width == 2:
                while True:
                    raw = wf.readframes(frames_per_window)
                    if not raw:
                        break
                    samples = array.array("h")
                    samples.frombytes(raw)
                    if not samples:
                        break
                    total = sum(float(s) * float(s) for s in samples)
                    env.append(math.sqrt(total / len(samples)) / 32768.0)
            else:                                    # 8-bit unsigned
                while True:
                    raw = wf.readframes(frames_per_window)
                    if not raw:
                        break
                    samples = array.array("B")
                    samples.frombytes(raw)
                    if not samples:
                        break
                    total = sum(((float(s) - 128.0) / 128.0) ** 2 for s in samples)
                    env.append(math.sqrt(total / len(samples)))
            if not env:
                return None
            return env
    except Exception:                                # noqa: BLE001
        return None


def normalise(env: List[float]) -> List[float]:
    """Scale so the loudest point is 1.0 — display shaping, never a fake level."""
    if not env:
        return []
    peak = max(env) or 1.0
    return [min(MAX_LEVEL, (v / peak)) for v in env]


class TtsLevelMeter:
    """Replays a real RMS envelope in wall-clock time, emitting levels."""

    def __init__(self, emit: Callable[[float], None], window_ms: int = WINDOW_MS):
        self.emit = emit
        self.window_ms = window_ms
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._level = 0.0
        self.available = False

    # ------------------------------------------------------------------ run
    def start_file(self, path: str) -> bool:
        """Begin metering a WAV file. False if no real envelope is available."""
        env = wav_rms_envelope(path, self.window_ms)
        if not env:
            self.available = False
            return False
        self.available = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(normalise(env),),
                                        name="tts-level", daemon=True)
        self._thread.start()
        return True

    def _run(self, env: List[float]) -> None:
        step = self.window_ms / 1000.0
        for value in env:
            if self._stop.is_set():
                break
            # light smoothing so the visual does not flicker
            self._level = (self._level * 0.55) + (value * 0.45)
            try:
                self.emit(round(self._level, 4))
            except Exception:                        # noqa: BLE001
                pass
            time.sleep(step)
        self._level = 0.0
        try:
            self.emit(0.0)
        except Exception:                            # noqa: BLE001
            pass

    # ----------------------------------------------------------------- stop
    def stop(self) -> None:
        self._stop.set()
        self._level = 0.0

    @property
    def level(self) -> float:
        return round(self._level, 4)
