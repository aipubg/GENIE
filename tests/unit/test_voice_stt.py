"""Real speech recognition + preprocessing + spoken-number tests (Phase 3 completion)."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from director.normalize import normalize
from voice import providers as prov
from voice.audio import dc_remove, highpass, normalize as agc_normalize, preprocess
from voice.vad import synthesize_silence, synthesize_speech
from core.paths import model_dir

MODEL = model_dir() / "vosk-model-small-en-us-0.15"


# ------------------------------------------------------------- preprocessing
def test_dc_offset_is_removed():
    samples = [0.5 + 0.001 * i / 100 for i in range(1000)]
    cleaned, offset = dc_remove(samples)
    assert abs(offset) > 0.001
    assert abs(sum(cleaned) / len(cleaned)) < 1e-6


def test_agc_reaches_the_target_when_the_gain_cap_allows():
    # AGC in isolation: the high-pass is a separate stage and would attenuate this low tone
    quiet = [s * 0.02 for s in synthesize_speech(200)]      # peak ~0.005
    result = preprocess(quiet, 16_000, apply_highpass=False, max_gain=500.0)
    assert result.gain > 100
    assert 0.8 < result.peak_after <= 1.0


def test_agc_gain_is_capped_so_noise_is_not_amplified_without_limit():
    quiet = [s * 0.02 for s in synthesize_speech(200)]
    result = preprocess(quiet, 16_000, apply_highpass=False, max_gain=40.0)
    assert result.gain == 40.0
    assert result.peak_after <= 0.25        # bounded output, never unbounded amplification


def test_agc_does_not_amplify_pure_silence():
    silence = synthesize_silence(200, noise=0.0001)
    result = preprocess(silence, 16_000)
    assert result.gain == 1.0


def test_agc_never_clips_beyond_full_scale():
    loud = [s * 3.0 for s in synthesize_speech(100)]
    result = preprocess(loud, 16_000)
    assert max(abs(s) for s in result.samples) <= 1.0


def test_highpass_removes_a_dc_like_rumble():
    rumble = [0.4] * 2000
    filtered = highpass(rumble, 16_000, cutoff_hz=80)
    assert abs(filtered[-1]) < 0.1


# --------------------------------------------------- spoken numbers -> digits
def test_spoken_numbers_become_digits():
    assert normalize("volume thirty") == "volume 30"
    assert normalize("set volume to twenty five") == "set volume to 25"
    assert normalize("volume one hundred") == "volume 100"


def test_hinglish_spoken_number_routes():
    assert normalize("awaz thirty kar do") == "volume 30"


def test_non_number_words_are_untouched():
    assert "chrome" in normalize("chrome kholo")
    assert normalize("open blender") == "open blender"


# ------------------------------------------------------- real STT provider
@pytest.fixture()
def vosk_importable(monkeypatch):
    """Make `import vosk` succeed so the MODEL-missing branch is what gets tested.

    The provider deliberately reports the most fundamental problem first: on a
    machine without the vosk package the reason is about the package, not the
    model. That is correct product behaviour - telling someone to download a
    model when the engine itself is absent would be misleading.

    The consequence is that this assertion used to depend on whether the
    developer's interpreter happened to have vosk installed, which made it fail
    for the wrong reason. This fixture isolates the intended condition so the
    test is deterministic everywhere.
    """
    import types

    stub = types.ModuleType("vosk")

    class _Model:  # pragma: no cover - never constructed in this test
        def __init__(self, path):
            raise RuntimeError("stub model should not be constructed")

    stub.Model = _Model
    stub.SetLogLevel = lambda level: None
    stub.KaldiRecognizer = object
    monkeypatch.setitem(sys.modules, "vosk", stub)
    yield stub


def test_vosk_provider_reports_unavailable_without_a_model(vosk_importable):
    stt = prov.VoskSttProvider("definitely/not/a/model")
    assert stt.available() is False
    assert "not found" in stt.status()["reason"]
    result = stt.transcribe([0.0] * 100)
    assert result.ok is False


def test_vosk_provider_reports_missing_package_before_missing_model(monkeypatch):
    """With no vosk package at all, the package is named first - not the model."""
    monkeypatch.setitem(sys.modules, "vosk", None)   # forces ImportError
    stt = prov.VoskSttProvider("definitely/not/a/model")
    assert stt.available() is False
    assert "vosk" in stt.status()["reason"]


@pytest.mark.skipif(not MODEL.exists(), reason="Vosk model not installed")
def test_vosk_transcribes_real_synthesised_speech():
    """Real speech -> real recognizer (no transcript injection anywhere)."""
    tts = prov.WindowsSapiFileTts(Path("data/voice"))
    if not tts.available():
        pytest.skip("no real speech synthesiser on this machine")
    speech = tts.synthesize("volume 30", out_path="data/voice/pytest_stt.wav")
    assert speech.ok and speech.bytes_out > 10_000
    samples, rate = prov.read_wav(speech.audio_path)
    stt = prov.VoskSttProvider(MODEL)
    assert stt.available() is True
    result = stt.transcribe(samples, rate)
    assert result.ok is True
    assert "volume" in result.text
    assert result.confidence > 0.5
    assert result.provider == "vosk"
    # the recognised phrase must route to the real capability
    assert normalize(result.text) == "volume 30"


@pytest.mark.skipif(not MODEL.exists(), reason="Vosk model not installed")
def test_vosk_reports_no_text_for_silence():
    stt = prov.VoskSttProvider(MODEL)
    result = stt.transcribe(synthesize_silence(800, noise=0.0005), 16_000)
    assert result.text == ""


# ------------------------------------------------------------- SAPI file TTS
def test_sapi_file_tts_writes_real_speech(tmp_path):
    tts = prov.WindowsSapiFileTts(tmp_path)
    if not tts.available():
        pytest.skip("no Windows SAPI on this machine")
    out = tts.synthesize("GENIE is ready", out_path=tmp_path / "speech.wav")
    assert out.ok is True
    assert out.synthetic is False            # real speech, not a test tone
    assert out.bytes_out > 10_000
    samples, rate = prov.read_wav(out.audio_path)
    assert rate > 8000 and len(samples) > 4000
    assert max(abs(s) for s in samples) > 0.01


def test_synthetic_provider_is_marked_synthetic(tmp_path):
    out = prov.FileTtsProvider(tmp_path).synthesize("test")
    assert out.synthetic is True
