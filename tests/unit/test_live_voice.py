import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from voice.live import LiveSession, LiveSettings


def make_live(tmp_path, **kwargs):
    return LiveSession(vault=SimpleNamespace(resolve=lambda _: "test-only-not-a-real-key"),
                       settings_path=tmp_path / "live.json", **kwargs)


def test_live_mode_status_keeps_initialized_multilingual_stt_visibility():
    from voice.service import VoiceService

    service = VoiceService.__new__(VoiceService)
    service._live_selected = True
    service.cfg = SimpleNamespace(language="hinglish")
    service.live = SimpleNamespace(status=lambda: {
        "amplitude": 0.0, "tts_amplitude": 0.0, "active": False})
    service.stt_provider = SimpleNamespace(status=lambda: {
        "provider": "faster-whisper", "model_id": "small",
        "ready": True, "loaded": True})

    status = service.status()
    assert status["providers"]["stt"]["provider"] == "faster-whisper"
    assert status["providers"]["stt"]["ready"] is True


@pytest.mark.parametrize("changes", [
    {"enabled": "false"}, {"headset_mode": 1}, {"model": "not-live"},
    {"voice": "unknown"}, {"input_device": -1}, {"output_device": True},
    {"api_key": "must-not-be-stored"}, {"idle_timeout_s": 1}, {"max_session_s": 99999},
])
def test_settings_reject_invalid_or_secret_fields(changes):
    with pytest.raises((ValueError, TypeError)):
        LiveSettings.validated(changes)


def test_settings_roundtrip_never_stores_key(tmp_path):
    live = make_live(tmp_path)
    assert live.configure({"voice": "Kore", "headset_mode": True})["ok"]
    loaded = make_live(tmp_path)
    assert loaded.settings.voice == "Kore" and loaded.settings.headset_mode
    assert "test-only" not in live.settings_path.read_text()


def test_missing_key_fails_before_starting_microphone(tmp_path):
    live = make_live(tmp_path)
    live.vault = None
    assert not live.start()["ok"]
    assert not live.active
    assert "API key" in live.status()["error"]


def test_duplex_transport_connects_microphone_and_speaker_callbacks(tmp_path):
    pytest.importorskip("google.genai")
    import time
    live = make_live(tmp_path)
    microphone = b"\x10\x00" * 800
    response = b"\x20\x00" * 1200
    sent, played, closed = [], [], []
    callbacks = {}

    class Stream:
        def __init__(self, kind, **kw):
            self.kind = kind
            callbacks[kind] = kw["callback"]
        def __enter__(self):
            if self.kind == "input":
                callbacks["input"](microphone, 800, None, None)
            return self
        def __exit__(self, *args):
            closed.append(self.kind)

    async def run():
        live._loop = asyncio.get_running_loop()
        got_input = asyncio.Event()
        async def send(**kwargs):
            sent.append(kwargs)
            got_input.set()
        async def receive(session):
            await asyncio.wait_for(got_input.wait(), 1)
            live._pcm.extend(response)
            output = bytearray(2400)
            callbacks["output"](output, 1200, None, None)
            played.append(bytes(output))
            live._stop.set()
        live._receive = receive
        sd = SimpleNamespace(RawInputStream=lambda **kw: Stream("input", **kw),
                             RawOutputStream=lambda **kw: Stream("output", **kw),
                             default=SimpleNamespace(device=[0, 1]),
                             query_devices=lambda *a: {"default_samplerate": 44100,
                                 "max_input_channels": 1, "max_output_channels": 1},
                             check_input_settings=lambda **kw: None,
                             check_output_settings=lambda **kw: None)
        await asyncio.wait_for(live._connected(SimpleNamespace(send_realtime_input=send), sd, time.monotonic()), 2)
    asyncio.run(run())
    assert sent[0]["audio"].data == microphone
    assert sent[0]["audio"].mime_type == "audio/pcm;rate=16000"
    assert played == [response]
    assert set(closed) == {"input", "output"}


def test_duplicate_start_owns_one_thread_and_stop_joins(tmp_path, monkeypatch):
    live = make_live(tmp_path)
    entered = threading.Event()
    def worker():
        entered.set()
        live._stop.wait(3)
    monkeypatch.setattr(live, "_run", worker)
    assert live.start()["ok"]
    assert entered.wait(1)
    thread = live._thread
    assert live.start()["already_running"]
    assert thread is live._thread
    assert not live.configure({"voice": "Kore"})["ok"]
    assert live.stop()["ok"]


def test_sdk_accepts_config_and_context_is_bounded(tmp_path):
    types = pytest.importorskip("google.genai.types")
    live = make_live(tmp_path, context_provider=lambda: [("old", "data")] * 50 + [("new", "reply")])
    config = types.LiveConnectConfig(**live._config())
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Aoede"
    assert "new" in config.system_instruction
    assert config.system_instruction.count('"old"') == 5
    assert config.input_audio_transcription is not None


def test_interrupt_flushes_audio_and_suppresses_inflight_reply(tmp_path):
    live = make_live(tmp_path)
    live._pcm.extend(b"\1\0" * 100)
    live.speaker_level = 0.2
    live.interrupt()
    assert not live._pcm and live.speaker_level == 0 and live._muted_turn


def test_error_does_not_expose_secret_and_auth_is_actionable(tmp_path):
    live = make_live(tmp_path)
    assert "test-only" not in live._safe_error(RuntimeError("request test-only-not-a-real-key failed"))
    assert "Replace the key" in live._safe_error(RuntimeError("API key not valid"))
    assert "quota" in live._safe_error(RuntimeError("429 RESOURCE_EXHAUSTED"))


def test_tools_have_receipts_and_duplicate_call_is_not_reexecuted(tmp_path):
    types = pytest.importorskip("google.genai.types")
    handler = MagicMock(return_value={"ok": True, "verified": True})
    live = make_live(tmp_path, tool_handler=handler)
    sent = []
    async def send(**kwargs):
        sent.append(kwargs)
    async def run():
        live._tool_lock = asyncio.Lock()
        call = types.FunctionCall(id="call-1", name="perform_task", args={"request": "open youtube"})
        session = SimpleNamespace(send_tool_response=send)
        await live._tool_calls(session, [call, call])
    asyncio.run(run())
    handler.assert_called_once()
    assert len(sent) == 2
    assert sent[0]["function_responses"][0].response["result"]["verified"]


def test_cancelled_and_unknown_tools_never_dispatch(tmp_path):
    types = pytest.importorskip("google.genai.types")
    handler = MagicMock()
    live = make_live(tmp_path, tool_handler=handler)
    live._cancelled_calls.add("cancelled")
    async def send(**kwargs):
        assert not kwargs["function_responses"][0].response["result"]["ok"]
    async def run():
        live._tool_lock = asyncio.Lock()
        await live._tool_calls(SimpleNamespace(send_tool_response=send), [
            types.FunctionCall(id="cancelled", name="perform_task", args={}),
            types.FunctionCall(id="unknown", name="shell.run", args={}),
            types.FunctionCall(name="perform_task", args={})])
    asyncio.run(run())
    handler.assert_not_called()


def test_receive_audio_transcripts_and_turn_completion(tmp_path):
    types = pytest.importorskip("google.genai.types")
    records = []
    live = make_live(tmp_path, record_turn=lambda u, r: records.append((u, r)))
    class Session:
        async def receive(self):
            yield types.LiveServerMessage(server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="hello"),
                output_transcription=types.Transcription(text="hi"),
                model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
                    data=b"\1\0" * 100, mime_type="audio/pcm;rate=24000"))])))
            yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
            live._stop.set()
    asyncio.run(live._receive(Session()))
    assert records == [("hello", "hi")]
    assert len(live._pcm) == 200 and live.turn_id == 1


def test_server_interruption_drops_buffer(tmp_path):
    types = pytest.importorskip("google.genai.types")
    live = make_live(tmp_path)
    live._pcm.extend(b"\1\0" * 100)
    class Session:
        async def receive(self):
            yield types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))
            live._stop.set()
    asyncio.run(live._receive(Session()))
    assert not live._pcm


def test_direct_voice_action_does_not_call_another_model(app, monkeypatch):
    from core.contracts import ActionResult
    classify = MagicMock(side_effect=AssertionError("second model must not be called"))
    monkeypatch.setattr(app.orchestrator.director, "classify", classify)
    execute = MagicMock(return_value=ActionResult(True, "system.volume.set", "done", verified=True))
    monkeypatch.setattr(app.computer, "execute", execute)
    result = app.orchestrator.handle_direct_action("set volume to 30", app.ctx(), threading.Event())
    assert result["state"] == "COMPLETED"
    assert result["steps"][0]["verified"]
    assert execute.call_count == 1
    classify.assert_not_called()


def test_direct_voice_action_rejects_conversation_without_execution(app, monkeypatch):
    execute = MagicMock()
    monkeypatch.setattr(app.computer, "execute", execute)
    result = app.orchestrator.handle_direct_action("hello", app.ctx(), threading.Event())
    assert not result["ok"]
    execute.assert_not_called()


def test_direct_voice_cancel_is_not_cleared(app, monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    execute = MagicMock()
    monkeypatch.setattr(app.computer, "execute", execute)
    result = app.orchestrator.handle_direct_action("volume 30", app.ctx(), cancelled)
    assert result["state"] == "CANCELLED"
    execute.assert_not_called()


def test_no_cloud_screen_capture_without_consent(tmp_path):
    from core.lifecycle import Daemon
    awareness = SimpleNamespace(settings=SimpleNamespace(remote_visual_consent=False), observe=MagicMock())
    daemon = SimpleNamespace(services={"computer": SimpleNamespace(desktop_awareness=awareness)})
    assert Daemon._live_screen_frames(daemon) == []
    awareness.observe.assert_not_called()


def test_portaudio_stop_cannot_deadlock_waiting_on_callback(monkeypatch):
    import numpy as np
    import sounddevice as sd
    from voice.providers import SoundDeviceInput
    class Stream:
        def __init__(self, **kwargs): self.callback = kwargs["callback"]
        def start(self): pass
        def stop(self):
            thread = threading.Thread(target=lambda: self.callback(np.zeros((320, 1)), 320, None, None))
            thread.start()
            thread.join(1)
            assert not thread.is_alive(), "callback blocked on the lifecycle lock"
        def close(self): pass
    monkeypatch.setattr(sd, "InputStream", Stream)
    source = SoundDeviceInput()
    callback = MagicMock()
    assert source.start(callback)
    source.stop()
    callback.assert_not_called()


def test_audio_format_falls_back_on_same_selected_device():
    from voice.audio_formats import select_format
    calls = []
    def check(**kwargs):
        calls.append(kwargs)
        if kwargs["samplerate"] != 44100:
            raise ValueError("Invalid sample rate")
    sd = SimpleNamespace(default=SimpleNamespace(device=[2, 0]),
        query_devices=lambda *a: {"default_samplerate": 44100, "max_output_channels": 2},
        check_output_settings=check)
    result = select_format(sd, None, "output", 24000)
    assert (result.device, result.rate, result.channels) == (0, 44100, 1)
    assert all(c["device"] == 0 for c in calls)


def test_audio_format_explicit_missing_device_never_changes_device():
    from voice.audio_formats import select_format
    sd = SimpleNamespace(query_devices=MagicMock(side_effect=ValueError("missing")))
    with pytest.raises(RuntimeError, match="device 12 is unavailable"):
        select_format(sd, 12, "input", 16000)
    sd.query_devices.assert_called_once_with(12, "input")


def test_pcm_conversion_keeps_streaming_rate_and_channels():
    from voice.audio_formats import PcmConverter
    converter = PcmConverter(44100, 16000, 2)
    result = b"".join(converter.convert(b"\x10\0\x30\0" * 2205) for _ in range(20))
    assert abs(len(result) - 32000) <= 2
    assert result[:2] == b"\x20\0"
    output = PcmConverter(24000, 44100, target_channels=2)
    result = b"".join(output.convert(b"\x10\0" * 1200) for _ in range(20))
    assert abs(len(result) - 44100 * 4) <= 8
    assert result[:4] == b"\x10\0\x10\0"


def test_interrupt_resets_conversion_and_status_does_not_claim_capture(tmp_path):
    live = make_live(tmp_path)
    live._output_converter.state = (1, ((100, 100),))
    live._session = object()
    live.state = "LISTENING"
    assert not live.status()["input"]["device_verified"]
    live.interrupt()
    assert live._output_converter.state is None
