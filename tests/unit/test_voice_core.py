"""Phase 3 unit tests: VAD, turn manager, communication brain, profiles, metrics, providers."""
from __future__ import annotations

import time

import pytest

from voice import providers as prov
from voice.communication import CommunicationBrain, CommunicationPolicy
from voice.metrics import VoiceMetrics
from voice.profiles import ConsentRecord, VoiceProfile, VoiceProfileRegistry
from voice.turn_manager import TurnManager, TurnState
from voice.vad import Vad, VadConfig, synthesize_silence, synthesize_speech


# ------------------------------------------------------------------------- VAD
def test_vad_detects_speech_and_end_of_speech():
    vad = Vad(VadConfig())
    events = []
    events += vad.process_chunk(synthesize_silence(300))
    events += vad.process_chunk(synthesize_speech(600))
    events += vad.process_chunk(synthesize_silence(900))
    kinds = [e.kind for e in events]
    assert "speech_start" in kinds
    assert "speech_end" in kinds
    assert kinds.index("speech_start") < kinds.index("speech_end")


def test_vad_ignores_pure_silence():
    vad = Vad(VadConfig())
    events = vad.process_chunk(synthesize_silence(2000))
    assert events == []
    assert vad.in_speech is False


def test_vad_does_not_calibrate_on_speech():
    """A person talking immediately must not teach the VAD that speech is noise (A-038)."""
    vad = Vad(VadConfig())
    events = vad.process_chunk(synthesize_speech(800))
    assert any(e.kind == "speech_start" for e in events)
    assert vad.noise_floor < 0.05


def test_vad_ignores_very_short_noises():
    vad = Vad(VadConfig(min_speech_ms=400))
    events = []
    events += vad.process_chunk(synthesize_speech(80))      # a click
    events += vad.process_chunk(synthesize_silence(900))
    ends = [e for e in events if e.kind == "speech_end"]
    assert ends and ends[0].reason == "too_short_ignored"


def test_vad_threshold_adapts_to_room_noise():
    quiet = Vad(VadConfig())
    quiet.process_chunk(synthesize_silence(1000, noise=0.0005))
    noisy = Vad(VadConfig())
    noisy.process_chunk(synthesize_silence(1000, noise=0.02))
    assert noisy.noise_floor > quiet.noise_floor


# ----------------------------------------------------------------- turn manager
def test_turn_manager_state_flow():
    tm = TurnManager()
    assert tm.state is TurnState.IDLE
    tm.begin_listening()
    assert tm.state is TurnState.LISTENING
    tm.end_speech("hello")
    assert tm.state is TurnState.THINKING
    tm.begin_speaking("hi")
    assert tm.state is TurnState.SPEAKING
    turn = tm.finish_turn()
    assert turn is not None and turn.transcript == "hello"
    assert tm.state is TurnState.IDLE


def test_barge_in_stops_speech_and_records_latency():
    stopped = []
    tm = TurnManager(on_interrupt=lambda: stopped.append(True))
    tm.begin_listening()
    tm.end_speech("open chrome")
    tm.begin_speaking("opening chrome now")
    info = tm.detect_barge_in(True)
    assert info is not None
    assert stopped == [True]
    assert tm.state is TurnState.INTERRUPTED
    assert info["stop_latency_ms"] >= 0
    assert tm.stats["interruptions"] == 1


def test_barge_in_ignored_when_genie_is_not_speaking():
    tm = TurnManager()
    tm.begin_listening()
    assert tm.detect_barge_in(True) is None
    assert tm.state is TurnState.LISTENING


def test_interrupted_turn_is_marked():
    tm = TurnManager()
    tm.begin_speaking("talking")
    tm.detect_barge_in(True)
    turn = tm.finish_turn()
    assert turn is not None and turn.interrupted is True


# ----------------------------------------------------------- communication brain
def test_action_intents_get_short_confirmations():
    brain = CommunicationBrain()
    policy = brain.policy_for("Chrome kholo", intent="application_action")
    assert policy.length == "short"
    assert policy.acknowledge is False
    assert policy.max_spoken_chars <= 250


def test_serious_context_disables_humour():
    brain = CommunicationBrain()
    policy = brain.policy_for("the backup file was deleted by mistake", urgency=0.9)
    assert policy.humour == 0
    assert policy.reason


def test_frustration_reduces_chatter():
    brain = CommunicationBrain()
    policy = brain.policy_for("why is this not working again")
    assert policy.humour == 0
    assert policy.proactivity == "silent"


def test_playful_user_gets_light_humour():
    brain = CommunicationBrain()
    policy = brain.policy_for("haha nice one")
    assert policy.humour >= 2


def test_interruption_makes_replies_shorter():
    brain = CommunicationBrain()
    policy = brain.policy_for("tell me everything", interrupted=True)
    assert policy.length == "short"
    assert "interrupted" in policy.reason


def test_low_confidence_reduces_verbosity():
    brain = CommunicationBrain()
    policy = brain.policy_for("something vague", confidence=0.2)
    assert policy.length == "short"


def test_shaping_strips_markdown_and_links():
    brain = CommunicationBrain()
    text = "# Heading\n\n- item one\n- item two\n\nSee [docs](https://example.com) and `code`."
    spoken = brain.shape_for_speech(text, CommunicationPolicy())
    assert "#" not in spoken and "- item" not in spoken
    assert "https://" not in spoken
    assert "`" not in spoken


def test_shaping_truncates_at_a_sentence_boundary():
    brain = CommunicationBrain()
    long_text = ("This is a sentence about GENIE. " * 40)
    spoken = brain.shape_for_speech(long_text, CommunicationPolicy(max_spoken_chars=200))
    assert len(spoken) <= 220
    assert spoken.rstrip().endswith((".", "…"))


def test_proactivity_is_scored_not_timed():
    brain = CommunicationBrain()
    urgent = brain.should_speak(urgency=0.95, relevance=0.9, confidence=0.9)
    trivial = brain.should_speak(urgency=0.05, relevance=0.1, confidence=0.2)
    assert urgent["action"] in ("interrupt", "speak_now")
    assert trivial["action"] in ("ignore", "show_silently")


def test_brain_behaviour_does_not_depend_on_a_provider():
    """Same input -> same policy, regardless of which TTS/LLM is configured."""
    a, b = CommunicationBrain(), CommunicationBrain()
    assert a.policy_for("volume 30", intent="computer_action").to_dict() == \
           b.policy_for("volume 30", intent="computer_action").to_dict()


# ------------------------------------------------------------------ profiles
def test_cloned_voice_cannot_be_used_without_consent():
    registry = VoiceProfileRegistry()
    result = registry.add(VoiceProfile("clone-1", "My clone", cloned=True))
    assert result["ok"] is True
    assert result["usable"] is False and result["consent_required"] is True
    # and it can never become active while consent is missing
    assert registry.set_active("clone-1")["ok"] is False
    assert registry.active().profile_id == "genie-default"


def test_consent_enables_and_revocation_disables_a_cloned_voice():
    registry = VoiceProfileRegistry()
    registry.add(VoiceProfile("clone-1", "My clone", cloned=True))
    registry.grant_consent("clone-1", "owner", "personal assistant voice", "consent.txt")
    assert registry.set_active("clone-1")["ok"] is True
    assert registry.active().profile_id == "clone-1"

    registry.revoke_consent("clone-1")
    assert registry.active().profile_id == "genie-default"     # falls back safely
    assert registry.set_active("clone-1")["ok"] is False


def test_profile_persistence(tmp_path):
    path = tmp_path / "profiles.json"
    registry = VoiceProfileRegistry(path)
    registry.set_active("genie-concise")
    reloaded = VoiceProfileRegistry(path)
    assert reloaded.active().profile_id == "genie-concise"


# ------------------------------------------------------------------- metrics
def test_metrics_record_each_stage(app):
    metrics = VoiceMetrics(db=app.db)
    metrics.begin_turn("t1")
    metrics.mark("stt_ms", 120)
    metrics.mark("director_ms", 600)
    record = metrics.finish_turn()
    assert record["stages"]["stt_ms"] == 120
    assert record["stages"]["total_ms"] >= 0
    summary = metrics.summary()
    assert summary["turns"] == 1
    assert summary["stt_ms"]["avg"] == 120
    rows = app.db.query("SELECT * FROM voice_metrics WHERE turn_id='t1'")
    assert rows and rows[0]["stt_ms"] == 120


# ------------------------------------------------------------------ providers
def test_wav_roundtrip(tmp_path):
    path = tmp_path / "tone.wav"
    samples = synthesize_speech(300)
    prov.write_wav(path, samples, 16_000)
    loaded, rate = prov.read_wav(path)
    assert rate == 16_000
    assert abs(len(loaded) - len(samples)) <= 2
    assert abs(loaded[100] - samples[100]) < 0.001


def test_unavailable_providers_report_honestly():
    stt = prov.NullSttProvider("no key")
    result = stt.transcribe([0.0] * 100)
    assert result.ok is False and "no key" in result.error
    assert stt.available() is False

    tts = prov.NullTtsProvider("no key")
    out = tts.synthesize("hello")
    assert out.ok is False and out.error


def test_file_stt_reads_a_sidecar_transcript(tmp_path):
    wav = tmp_path / "speech.wav"
    prov.write_wav(wav, synthesize_speech(200))
    (tmp_path / "speech.txt").write_text("Chrome kholo", encoding="utf-8")
    stt = prov.FileSttProvider()
    stt.bind(wav, "Chrome kholo")
    result = stt.transcribe([0.0] * 10)
    assert result.ok and result.text == "Chrome kholo"


def test_file_tts_marks_its_output_as_synthetic(tmp_path):
    tts = prov.FileTtsProvider(tmp_path)
    result = tts.synthesize("a short reply")
    assert result.ok and result.synthetic is True
    assert result.audio_path and result.duration_ms > 0


def test_http_providers_are_unavailable_without_a_key():
    stt = prov.HttpSttProvider(base_url="https://example.com/v1", api_key_ref="secret://x")
    assert stt.available() is False
    assert "key" in stt.status()["reason"]
    tts = prov.HttpTtsProvider(base_url="https://example.com/v1", api_key_ref="secret://x")
    assert tts.available() is False


def test_realtime_provider_requires_a_key(app):
    live = prov.GeminiLiveProvider(vault=app.vault)
    assert live.available() is False
    assert live.connect() is False
    assert "key" in live.status()["reason"]


def test_wav_file_input_feeds_the_pipeline(tmp_path):
    wav = tmp_path / "in.wav"
    prov.write_wav(wav, synthesize_speech(120), 16_000)
    chunks = []
    source = prov.WavFileInput(wav)
    source.start(lambda samples, rate: chunks.append(len(samples)))
    deadline = time.time() + 3
    while not chunks and time.time() < deadline:
        time.sleep(0.05)
    source.stop()
    assert sum(chunks) > 0
