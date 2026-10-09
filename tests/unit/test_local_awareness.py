import io
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.db import Database
from memory.observations import ObservationStore
from perception.camera import CameraSession, FrameSourceCameraProvider, OpenCvCameraProvider
from perception.local_monitor import LocalMonitor
from perception.service import GatewayVisionProvider, PerceptionService
from security.vault import Vault
from voice.live import LiveSession


@pytest.fixture
def journal(tmp_path):
    db = Database(tmp_path / "events.db")
    # Deterministic cipher double. The separate Windows test exercises DPAPI.
    store = ObservationStore(db, protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])
    yield store
    db.close()


def test_journal_encrypted_search_retention_and_clear(journal):
    journal.append("desktop", {"title": "Owner project", "inferred_preference": False})
    row = journal.db.query_one("SELECT payload FROM local_observations")
    assert b"Owner project" not in bytes(row["payload"])
    assert journal.recent(query="owner")[0]["evidence"]["title"] == "Owner project"
    assert not journal.recent(query="missing")
    journal.db.execute("UPDATE local_observations SET ts=0")
    journal.trim()
    assert journal.status()["events"] == 0
    journal.append("desktop", {"title": "New"})
    journal.clear()
    assert not journal.recent()


def test_encryption_failure_never_writes_plaintext(journal):
    def fail(_):
        raise OSError("encryption unavailable")
    journal.protect = fail
    with pytest.raises(OSError):
        journal.append("desktop", {"title": "private"})
    assert journal.status()["events"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_real_dpapi_roundtrip(tmp_path):
    db = Database(tmp_path / "dpapi.db")
    try:
        journal = ObservationStore(db)
        journal.append("test", {"title": "synthetic-only"})
        assert journal.recent()[0]["evidence"]["title"] == "synthetic-only"
        assert b"synthetic-only" not in bytes(db.query_one("SELECT payload FROM local_observations")[0])
    finally:
        db.close()


def monitor(tmp_path, journal):
    desktop = SimpleNamespace(settings=SimpleNamespace(enabled=True), is_running=True, is_paused=False,
        observe=lambda: SimpleNamespace(locked=False, paused=False,
            foreground={"process": "editor.exe", "title": "Project", "monitor": 0}, display_count=2))
    return LocalMonitor(PerceptionService(), desktop, journal, tmp_path)


def test_desktop_memory_deduplicates_and_respects_pause(tmp_path, journal):
    service = monitor(tmp_path, journal)
    service._desktop_tick()
    service._desktop_tick()
    assert journal.status()["events"] == 1
    service.desktop.is_paused = True
    service._last_desktop = None
    service._desktop_tick()
    assert journal.status()["events"] == 1
    assert journal.recent()[0]["evidence"]["inferred_preference"] is False


def test_monitor_validates_and_persists_without_starting_hardware(tmp_path, journal):
    service = monitor(tmp_path, journal)
    assert not service.configure({"camera_index": -1})["ok"]
    assert not service.configure({"camera_enabled": "true"})["ok"]
    assert not service.configure({"network_camera": True})["ok"]
    assert service.configure({"camera_index": 2})["ok"]
    assert monitor(tmp_path, journal).settings.camera_index == 2
    assert not service.running and not service.status()["camera_active"]


def test_monitor_does_not_take_or_close_another_session(tmp_path, journal):
    service = monitor(tmp_path, journal)
    other = CameraSession("camera0", "office")
    service.perception._sessions["office"] = other
    service._camera_tick()
    service._close_owned_camera()
    assert service.perception._sessions["office"] is other
    assert "already" in service.camera_error
    assert not service.status()["camera_active"]


def test_webcam_preview_and_cloud_consent_are_independent(tmp_path, journal):
    service = monitor(tmp_path, journal)
    service._thread = SimpleNamespace(is_alive=lambda: True)
    service._owned_session = CameraSession("camera0", "office")
    service.perception._sessions["office"] = service._owned_session
    service.perception.camera.latest_jpeg = b"image"
    assert service.preview() == b"image"
    assert service.preview(for_cloud=True) == b""
    service.settings.cloud_camera_consent = True
    assert service.preview(for_cloud=True) == b"image"
    service._owned_session.closed = True
    assert service.preview() == b""


def test_camera_status_does_not_open_hardware(monkeypatch):
    cv = pytest.importorskip("cv2")
    capture = MagicMock()
    monkeypatch.setattr(cv, "VideoCapture", capture)
    provider = OpenCvCameraProvider()
    assert not provider.available()
    assert provider.cameras() == []
    assert not PerceptionService(camera_provider=provider).camera_status()["available"]
    capture.assert_not_called()


def test_explicit_camera_open_reuses_handle_and_release_closes(monkeypatch):
    cv = pytest.importorskip("cv2")
    handle = MagicMock()
    handle.isOpened.return_value = True
    capture = MagicMock(return_value=handle)
    monkeypatch.setattr(cv, "VideoCapture", capture)
    provider = OpenCvCameraProvider([2, 3])
    provider.discover("camera2")
    provider.discover("camera2")
    assert capture.call_count == 1
    assert [c.camera_id for c in provider.cameras()] == ["camera2"]
    provider.release("camera2")
    handle.release.assert_called_once()
    assert not provider.available()


def test_no_camera_cloud_call_without_explicit_consent():
    gateway = MagicMock()
    vision = GatewayVisionProvider(gateway)
    assert not vision.classify([0] * 16, 4, 4)["ok"]
    gateway.complete.assert_not_called()


def test_camera_gateway_receives_pixels_not_only_dimensions():
    gateway = MagicMock()
    gateway.complete.return_value = SimpleNamespace(text='{"label":"object","confidence":0.8}', provider_id="test")
    result = GatewayVisionProvider(gateway, allowed=True).classify([100] * 64, 8, 8)
    assert result["ok"], result
    content = gateway.complete.call_args.args[2][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_gemini_setup_only_completes_after_validation_and_vault_save(tmp_path, monkeypatch):
    vault = Vault(tmp_path / "vault")
    live = LiveSession(vault=vault, settings_path=tmp_path / "voice_live.json")
    assert not live.setup_status()["completed"]
    monkeypatch.setattr(live, "test_connection", lambda **kw: {"ok": False, "error": "invalid key"})
    assert not live.complete_setup("test-only-invalid")["ok"]
    assert not vault.has("secret://provider/gemini/key")
    monkeypatch.setattr(live, "test_connection", lambda **kw: {"ok": True})
    assert live.complete_setup("test-only-valid")["ok"]
    assert live.setup_status()["completed"]
    assert "test-only-valid" not in (tmp_path / "voice_setup.json").read_text()
    vault.store("secret://provider/gemini/key", "test-only-replacement")
    assert not live.setup_status()["completed"]


def test_failed_vault_write_preserves_previous_key(tmp_path, monkeypatch):
    vault = Vault(tmp_path / "vault")
    vault.store("secret://test/key", "before")
    previous = vault.path.read_bytes()
    def fail(_):
        raise OSError("cannot encrypt")
    monkeypatch.setattr(vault, "_encrypt", fail)
    with pytest.raises(OSError):
        vault.store("secret://test/key", "after")
    assert vault.resolve("secret://test/key") == "before"
    assert vault.path.read_bytes() == previous


def test_live_camera_mosaic_requires_consent_without_screen_consent():
    from core.lifecycle import Daemon
    from PIL import Image
    data = io.BytesIO()
    Image.new("RGB", (320, 240), "red").save(data, format="JPEG")
    consent = SimpleNamespace(cloud_camera_consent=False)
    monitor = SimpleNamespace(settings=consent, running=True,
                              preview=lambda **kw: data.getvalue() if consent.cloud_camera_consent else b"")
    daemon = object.__new__(Daemon)
    daemon.services = {"local_monitor": monitor,
        "computer": SimpleNamespace(desktop_awareness=SimpleNamespace(
            settings=SimpleNamespace(remote_visual_consent=False), is_paused=False))}
    daemon._live_screen_frames = lambda: []
    assert daemon._live_visual_frames() == []
    consent.cloud_camera_consent = True
    frames = daemon._live_visual_frames()
    assert len(frames) == 1
    assert Image.open(io.BytesIO(frames[0])).height == 270
