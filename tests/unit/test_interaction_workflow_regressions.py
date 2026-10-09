"""Regressions reproduced from reported voice/browser/desktop workflows."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading
import struct

import pytest


def test_microphone_idle_audio_is_bounded():
    pipe = VoicePipeline(input_provider=MagicMock(), stt=MagicMock(),
                         tts=MagicMock(), output=MagicMock())
    pipe._capturing = True
    for _ in range(100):
        pipe._on_chunk([0.0] * 320, 16000)
    assert len(pipe._audio) <= 8000


def test_voice_worker_does_not_block_capture_and_recovers_from_stt_exception():
    entered, release = threading.Event(), threading.Event()
    stt = MagicMock()
    def transcribe(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("recognizer failed")
    stt.transcribe.side_effect = transcribe
    pipe = VoicePipeline(input_provider=MagicMock(), stt=stt, tts=MagicMock(),
                         output=MagicMock(), config=VoiceConfig(speak_replies=False))
    pipe._capturing = True
    pipe.turns.begin_listening()
    pipe._audio = [0.1] * 1600
    pipe._dispatch_utterance()
    try:
        assert entered.wait(2)
        pipe._on_chunk([0.1] * 320, 16000)
        assert not pipe._audio
        assert pipe._utterance_worker.is_alive()
    finally:
        release.set()
        pipe._utterance_worker.join(3)
    assert pipe.turns.can_accept_owner_utterance()
    assert pipe.stats["stt_failures"] == 1


def test_stopping_voice_discards_late_transcript_without_action():
    entered, release = threading.Event(), threading.Event()
    stt, handler = MagicMock(), MagicMock()
    def transcribe(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return TranscriptResult(True, text="open browser")
    stt.transcribe.side_effect = transcribe
    pipe = VoicePipeline(input_provider=MagicMock(), stt=stt, tts=MagicMock(),
                         output=MagicMock(), handler=handler)
    pipe._capturing = True
    pipe.turns.begin_listening()
    pipe._audio = [0.1] * 1600
    pipe._dispatch_utterance()
    try:
        assert entered.wait(2)
        pipe.stop()
    finally:
        release.set()
        pipe._utterance_worker.join(3)
    handler.assert_not_called()


def test_start_updates_mode_and_does_not_open_second_microphone():
    source = MagicMock()
    pipe = VoicePipeline(input_provider=source, stt=MagicMock(), tts=MagicMock(),
        output=MagicMock(), config=VoiceConfig(greet_on_session_start=False))
    assert pipe.start("continuous")["ok"]
    assert pipe.cfg.mode == "continuous"
    assert pipe.start("continuous")["ok"]
    source.start.assert_called_once()
    pipe.stop()

from browser.service import BrowserService
from browser.mode import MODE_OWNER_EXISTING, decide_browser_mode
from browser.website_task import WebsiteTaskEngine
from computer.desktop_awareness import DesktopAwareness, _redact_bmp_rects
from director.heuristics import plan_web_action
from voice.providers import FasterWhisperSttProvider, TranscriptResult
from voice.pipeline import VoiceConfig, VoicePipeline


@pytest.mark.parametrize("language,expected", [("hinglish", None), ("auto", None),
    ("hi", "hi"), ("Hindi", "hi"), ("en", "en"), ("", None)])
def test_whisper_accepts_product_language(language, expected):
    pytest.importorskip("numpy")
    stt = FasterWhisperSttProvider()
    stt._model = MagicMock()
    stt._model.transcribe.return_value = ([SimpleNamespace(text="hello")],
        SimpleNamespace(language="en", language_probability=0.9))
    stt._error = "previous failure"
    result = stt.transcribe([0.1] * 100, language=language)
    assert result.ok
    assert stt._model.transcribe.call_args.kwargs["language"] == expected
    assert not stt.status()["error"]


def test_failed_transcript_does_not_disable_next_utterance():
    stt = MagicMock()
    stt.transcribe.side_effect = [TranscriptResult(False, error="temporary failure"),
                                  TranscriptResult(True, text="hello")]
    handler = MagicMock(return_value={"reply": "hello back"})
    pipe = VoicePipeline(input_provider=MagicMock(), stt=stt, tts=MagicMock(),
        output=MagicMock(), handler=handler,
        config=VoiceConfig(mode="continuous", speak_replies=False))
    pipe.turns.begin_listening()
    pipe._audio = [0.1] * 100
    pipe._finish_utterance()
    assert pipe.turns.can_accept_owner_utterance()
    pipe._audio = [0.1] * 100
    pipe._finish_utterance()
    handler.assert_called_once()


def test_navigation_uses_current_tab_not_destination_matching(monkeypatch):
    svc = BrowserService()
    client = MagicMock()
    client.evaluate.return_value = {"href": "https://example.org", "ready": "complete",
                                     "title": "Example", "content": 60}
    connect = MagicMock(return_value=client)
    monkeypatch.setattr(svc, "_connect_page", connect)
    assert svc.navigate({"url": "https://example.org"})["ok"]
    connect.assert_called_once_with()
    assert [c.args[0] for c in client.send.call_args_list] == ["Page.navigate"]


def test_cold_launch_claims_only_its_initial_target(monkeypatch, tmp_path):
    svc = BrowserService(profile_dir=tmp_path)
    monkeypatch.setattr("browser.service.cdp.launch", lambda **kw: {
        "pid": 44, "port": 9338, "initial_target": {"id": "new"}})
    assert svc.ensure()["ok"]
    assert svc._resolver.owned == {"new"}


def test_failed_attachment_never_observes_or_launches_fallback():
    calls = []
    def dispatch(cap, params):
        calls.append(cap)
        return {"ok": False, "blocked": True, "detail": "connection required"}
    result = WebsiteTaskEngine(dispatch).run([{"capability": "browser.navigate",
        "params": {"browser": "brave", "browser_mode": MODE_OWNER_EXISTING},
        "verify": {"url_contains": "youtube.com"}}])
    assert result["state"] == "FAILED"
    assert calls == ["browser.navigate"]


def test_media_keeps_browser_mode_and_stops_on_failed_navigation(monkeypatch):
    svc = BrowserService()
    ensure = MagicMock(return_value={"ok": True})
    navigate = MagicMock(return_value={"ok": False, "detail": "network down"})
    monkeypatch.setattr(svc, "_ensure_requested_browser", ensure)
    monkeypatch.setattr(svc, "navigate", navigate)
    monkeypatch.setattr(svc, "_connect_page", MagicMock(side_effect=AssertionError("no page access")))
    result = svc.media_play({"query": "rain & music", "browser": "brave",
                             "browser_mode": MODE_OWNER_EXISTING})
    assert not result["ok"]
    assert ensure.call_args.kwargs["mode"] == MODE_OWNER_EXISTING
    assert navigate.call_args.args[0]["browser_mode"] == MODE_OWNER_EXISTING
    assert "rain+%26+music" in navigate.call_args.args[0]["url"]


def test_owner_browser_phrase_without_brand_is_not_silently_downgraded():
    assert decide_browser_mode("hamare browser mein youtube kholo", "", "youtube") == MODE_OWNER_EXISTING
    assert not BrowserService()._ensure_requested_browser("", MODE_OWNER_EXISTING)["ok"]


def test_unknown_website_is_blocked_before_dependent_media():
    plan = plan_web_action("Deluxe salon website open karo aur wahan par gane chalu kar do")
    assert plan[0]["capability"] == "plan.unsupported"
    assert plan_web_action("open example.org")[0]["params"]["url"] == "example.org"


def test_youtube_song_routes_to_browser_not_optional_media_plugin():
    from director.heuristics import HeuristicDirector
    from core.contracts import CallContext
    decision = HeuristicDirector().classify(
        "YouTube par koi barsaat song chala do", CallContext(person_id="owner"))
    assert decision.tasks
    assert decision.tasks[0].capability == "browser.media.play"
    assert decision.tasks[0].params["query"]


def test_explicit_download_routes_to_core_browser_download():
    from director.heuristics import plan_web_action
    steps = plan_web_action("download https://example.com/file.zip")
    assert steps[0]["capability"] == "browser.download"
    assert steps[0]["params"]["url"] == "https://example.com/file.zip"


def test_media_action_uses_bounded_page_waits(monkeypatch):
    svc = BrowserService()
    calls = []
    monkeypatch.setattr(svc, "_ensure_requested_browser", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(svc, "navigate", lambda p: calls.append(p) or {"ok": False, "detail": "timeout"})
    result = svc.media_play({"query": "rain song"})
    assert not result["ok"]
    assert calls[0]["wait_s"] <= 12 and calls[0]["content_wait_s"] <= 4


def test_pause_prevents_explicit_observation(monkeypatch, tmp_path):
    da = DesktopAwareness(str(tmp_path))
    monkeypatch.setattr("computer.desktop_awareness._is_desktop_locked", lambda: False)
    monkeypatch.setattr("computer.desktop_awareness.win.list_monitors",
                        MagicMock(side_effect=AssertionError("paused")))
    da.pause()
    assert da.observe().displays == []
    assert not da.capture_display(0)["ok"]


def test_settings_start_stop_and_persist(monkeypatch, tmp_path):
    da = DesktopAwareness(str(tmp_path))
    monkeypatch.setattr(da, "_poll_loop", lambda: da._stop_event.wait(5))
    try:
        da.configure({"enabled": True, "auto_start": True})
        assert da.is_running
        da.configure({"enabled": False})
        assert not da.is_running
        again = DesktopAwareness(str(tmp_path))
        assert again.settings.auto_start and not again.settings.enabled
        with pytest.raises(ValueError):
            da.configure({"enabled": "false"})
    finally:
        da.stop()


def test_sensitive_rectangles_are_redacted_in_actual_bmp(tmp_path):
    path = tmp_path / "capture.bmp"
    width, height, stride = 4, 3, 12
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<I", header, 10, 54)
    struct.pack_into("<i", header, 18, width)
    struct.pack_into("<i", header, 22, -height)
    struct.pack_into("<H", header, 28, 24)
    path.write_bytes(header + bytes([255]) * stride * height)
    assert _redact_bmp_rects(str(path), [(1, 0, 3, 2)]) == 1
    pixels = path.read_bytes()[54:]
    assert pixels[3:9] == bytes(6)
    assert pixels[0:3] == bytes([255]) * 3
    assert pixels[24:] == bytes([255]) * 12


def test_gemini_invalid_key_marks_provider_auth_required():
    from models.failures import classify, FailureKind
    assert classify(Exception("HTTP 400 API key not valid. Please pass a valid API key.")) == FailureKind.MISSING_CREDENTIAL


def test_locked_desktop_status_preserves_lock_without_window_data(monkeypatch, tmp_path):
    monkeypatch.setattr("computer.desktop_awareness._is_desktop_locked", lambda: True)
    da = DesktopAwareness(str(tmp_path))
    observation = da.observe()
    assert observation.locked and not observation.displays
    assert da.status["locked"] is True
    assert da.status["window_count"] == 0


def test_desktop_command_routes_without_model_narration():
    from core.orchestrator import _apply_desktop_action
    from director.base import DirectorDecision
    decision = _apply_desktop_action("start screen sharing automatically", DirectorDecision())
    assert decision.tasks[0].capability == "desktop.settings.set"
    assert decision.tasks[0].params["settings"]["auto_start"] is True
    assert not decision.mission_required


def test_desktop_settings_http_roundtrip_uses_permission_checked_service(app, tmp_path, monkeypatch):
    import json
    import urllib.request
    from core.ipc.server import IPCServer
    from computer.service import ComputerService
    computer = ComputerService(trust=app.trust, db=app.db, audit=app.audit,
                               workspace_root=str(tmp_path / "workspace"), data_dir=str(tmp_path))
    monkeypatch.setattr(computer.desktop_awareness, "_poll_loop",
                        lambda: computer.desktop_awareness._stop_event.wait(10))
    server = IPCServer(SimpleNamespace(services={"computer": computer}), port=0)
    server.start()
    port = server._httpd.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/desktop/settings",
            data=json.dumps({"settings": {"enabled": True, "auto_start": True}}).encode(),
            headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=5) as response:
            result = json.load(response)
        assert result["ok"], result
        assert result["status"]["running"] and result["settings"]["auto_start"]
        with opener.open(f"http://127.0.0.1:{port}/api/desktop", timeout=5) as response:
            assert json.load(response)["settings"]["enabled"]
    finally:
        computer.desktop_awareness.stop()
        server.stop()
