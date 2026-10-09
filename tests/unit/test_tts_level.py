"""Real TTS output level (item 15).

The gem previously used a labelled deterministic shimmer because no output level
was measured. This measures one from the audio GENIE actually plays — and reports
unavailable when there is no audio buffer, rather than inventing an envelope.
"""
from __future__ import annotations

import array
import math
import sys
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voice.tts_level import (TtsLevelMeter, normalise,  # noqa: E402
                             wav_rms_envelope)


def _make_wav(path: Path, *, quiet_amp: int, loud_amp: int, rate: int = 8000):
    frames = []
    for i in range(rate):                       # 1s quiet
        frames.append(int(quiet_amp * math.sin(2 * math.pi * 200 * i / rate)))
    for i in range(rate):                       # 1s loud
        frames.append(int(loud_amp * math.sin(2 * math.pi * 200 * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", frames).tobytes())
    return path


@pytest.fixture()
def wav(tmp_path):
    return _make_wav(tmp_path / "tts.wav", quiet_amp=2000, loud_amp=30000)


# ----------------------------------------------------------------- envelope
def test_envelope_reflects_real_loudness(wav):
    env = wav_rms_envelope(str(wav))
    assert env, "envelope must be produced from real PCM"
    assert env[-1] > env[0] * 5, "the loud half must measure louder"


def test_unreadable_file_returns_none():
    assert wav_rms_envelope("/nonexistent/audio.wav") is None


def test_normalise_peaks_at_one(wav):
    env = wav_rms_envelope(str(wav))
    n = normalise(env)
    assert abs(max(n) - 1.0) < 1e-6
    assert all(0.0 <= v <= 1.0 for v in n)


def test_normalise_handles_empty():
    assert normalise([]) == []


# -------------------------------------------------------------------- meter
def test_meter_reports_available_for_a_real_file(wav):
    seen: list[float] = []
    m = TtsLevelMeter(emit=seen.append, window_ms=10)
    assert m.start_file(str(wav)) is True
    assert m.available is True
    m.stop()


def test_meter_emits_levels_that_actually_rising(wav):
    seen: list[float] = []
    m = TtsLevelMeter(emit=seen.append, window_ms=10)
    m.start_file(str(wav))
    time.sleep(1.2)                 # most of the quiet half
    early = list(seen)
    m.stop()
    assert early, "the meter must emit real levels over time"
    assert any(v > 0.0 for v in early), f"expected non-zero levels, got {early}"


def test_meter_reports_unavailable_without_audio():
    m = TtsLevelMeter(emit=lambda v: None)
    assert m.start_file("/nonexistent/audio.wav") is False
    assert m.available is False, \
        "must report unavailable rather than synthesising an envelope"
    assert m.level == 0.0


def test_meter_stop_resets_level(wav):
    seen: list[float] = []
    m = TtsLevelMeter(emit=seen.append, window_ms=10)
    m.start_file(str(wav))
    m.stop()
    assert m.level == 0.0


# ------------------------------------------------------------ 8-bit support
def test_8bit_wav_is_measured_too(tmp_path):
    p = tmp_path / "u8.wav"
    rate = 8000
    raw = bytes(int(128 + 100 * math.sin(2 * math.pi * 200 * i / rate))
                for i in range(rate))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(raw)
    env = wav_rms_envelope(str(p))
    assert env and all(0.0 <= v <= 1.0 for v in env)
