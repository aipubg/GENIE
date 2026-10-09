"""Real isolated browser navigation, playback, and desktop preview verification."""
from pathlib import Path
import json
import socket
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from browser.service import BrowserService
    from browser import cdp
    from computer.desktop_awareness import DesktopAwareness
    root = Path(tempfile.mkdtemp(prefix="genie-owner-fixes-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    browser = BrowserService(port=port, profile_dir=root / "browser-profile", headless=True)
    report = {}
    try:
        for name in ("browser_lab.html", "test_site/index.html", "browser_lab.html"):
            result = browser.navigate({"url": (ROOT / "tests/fixtures" / name).as_uri()})
            assert result.get("ok"), result
        pages = cdp.page_targets(browser.port)
        assert len(pages) == 1, [(p.get("id"), p.get("url")) for p in pages]
        report["three_navigations_tab_count"] = len(pages)
        with wave.open(str(root / "silence.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(16000 * 2 * 10))
        client = browser._connect_page()
        client.evaluate("(() => { const v=document.createElement('video'); v.src=" +
                        json.dumps((root / "silence.wav").as_uri()) +
                        "; document.body.appendChild(v); })()")
        played = client.send("Runtime.evaluate", {"expression": browser._JS_PLAY,
            "awaitPromise": True, "returnByValue": True, "userGesture": True}, timeout=12)
        assert (played.get("result", {}).get("value") or {}).get("ok"), played
        before = client.evaluate(browser._JS_VIDEO)
        time.sleep(1)
        after = client.evaluate(browser._JS_VIDEO)
        assert not after["paused"] and after["currentTime"] > before["currentTime"], after
        report["real_media_clock_advanced"] = True
    finally:
        browser.shutdown()
    da = DesktopAwareness(str(root / "desktop"))
    try:
        da.configure({"enabled": True, "auto_start": True, "local_preview": True})
        deadline = time.time() + 12
        frame = b""
        while time.time() < deadline:
            status = da.status
            displays = status.get("displays") or []
            if displays:
                frame = da.preview_frame(displays[0]["index"])
            if frame:
                break
            time.sleep(0.3)
        assert frame.startswith(b"BM"), da.status
        report["desktop_displays"] = da.status["display_count"]
        report["desktop_frame_bytes"] = len(frame)
        da.pause()
        assert not da.preview_frame(displays[0]["index"])
        assert not list(da._temp_buffer_dir.glob("*.bmp"))
        report["pause_clears_preview"] = True
    finally:
        da.stop()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
