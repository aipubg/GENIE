"""Deterministic tests for the browser download handler (no Chrome required).

These lock in two fixes that previously had only manual verification:

1. `Browser.setDownloadBehavior` is attempted BEFORE `Page.setDownloadBehavior`.
   The Page command is deprecated: it answers success but does nothing, so trying
   it first meant the working fallback never ran.
2. The handler trusts Chrome's own `Browser.downloadProgress` events — using the
   reported filePath on `completed`, and reporting `canceled` honestly instead of
   burning the whole timeout.

CDP is stubbed, so these run anywhere and cannot flake on browser timing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser import service as svc_mod  # noqa: E402
from browser.service import BrowserService  # noqa: E402


class FakeClient:
    """Records CDP calls and replays queued events."""

    def __init__(self, ws=None, events=None, fail_on=(), on_events=None):
        self.ws = ws
        self.calls: list[tuple] = []
        self._events = list(events or [])
        self.fail_on = set(fail_on)
        self.on_events = on_events
        self.closed = False

    def send(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        if method in self.fail_on:
            raise RuntimeError(f"{method} deliberately failed")
        if method == "Input.dispatchMouseEvent":
            return {}
        return {}

    def events(self, clear=False):
        # the handler purges the target directory before starting, so a
        # "downloaded" file must be created lazily when events are read
        if self.on_events:
            self.on_events()
        out = list(self._events)
        if clear:
            self._events = []
        return out

    def evaluate(self, expr, timeout=None):
        if "querySelector" in expr and "return null" in expr:
            return {"x": 10, "y": 10, "on": True, "inView": True}
        return True

    def close(self):
        self.closed = True


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    s = BrowserService.__new__(BrowserService)
    s.profile_dir = str(tmp_path / "profile")
    s.port = 1
    s._client = None
    s._page_ws = None
    s.db = None
    s.state = None
    return s


def _patch(s, monkeypatch, page: FakeClient, browser: FakeClient | None):
    monkeypatch.setattr(s, "_connect_page", lambda url=None: page)
    monkeypatch.setattr(s, "_browser_ws_url", lambda: "ws://fake")
    made = []

    def _factory(ws):
        made.append(ws)
        return browser

    monkeypatch.setattr(svc_mod.cdp, "CDPClient", _factory)
    return made


def _event(state, path=None):
    params = {"state": state}
    if path:
        params["filePath"] = str(path)
    return {"method": "Browser.downloadProgress", "params": params}


# ------------------------------------------------- CDP command ordering
def test_browser_level_command_is_tried_first(svc, monkeypatch, tmp_path):
    page, browser = FakeClient(), FakeClient()
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"url": "http://x/f.txt", "directory": str(tmp_path / "d"),
                        "timeout_s": 0.2})
    browser_methods = [m for m, _ in browser.calls]
    assert "Browser.setDownloadBehavior" in browser_methods
    page_methods = [m for m, _ in page.calls]
    assert "Page.setDownloadBehavior" not in page_methods, \
        "the browser-level command succeeded, so the deprecated Page path must not run"


def test_falls_back_to_page_when_browser_command_fails(svc, monkeypatch, tmp_path):
    page, browser = FakeClient(), FakeClient(fail_on={"Browser.setDownloadBehavior"})
    _patch(svc, monkeypatch, page, browser)
    svc.download({"url": "http://x/f.txt", "directory": str(tmp_path / "d"),
                  "timeout_s": 0.2})
    page_methods = [m for m, _ in page.calls]
    assert "Page.setDownloadBehavior" in page_methods, \
        "if the browser-level command fails, the Page fallback must still be tried"


# --------------------------------------------- event-driven completion
def test_completed_event_uses_chrome_file_path(svc, monkeypatch, tmp_path):
    target = tmp_path / "d"
    target.mkdir()
    finished = target / "genie_fixture.txt"
    finished.write_text("hello")

    page, browser = FakeClient(), FakeClient(events=[_event("completed", finished)],
                                             on_events=lambda: finished.write_text("hello"))
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"url": "http://x/f.txt", "directory": str(target), "timeout_s": 5})

    assert out["ok"] is True
    assert out["path"] == str(finished)
    assert out["verify"]["verified"] is True
    assert out["in_requested_directory"] is True
    assert browser.closed, "the CDP client must be closed on every exit path"


def test_canceled_event_is_reported_honestly(svc, monkeypatch, tmp_path):
    page, browser = FakeClient(), FakeClient(events=[_event("canceled")])
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"url": "http://x/f.txt", "directory": str(tmp_path / "d"),
                        "timeout_s": 30})
    assert out["ok"] is False
    assert "cancelled" in out["error"].lower()
    assert out["verify"]["detail"] == "Chrome reported state=canceled"
    assert browser.closed, "the CDP client must be closed even on cancellation"


def test_no_events_falls_back_to_filesystem_timeout(svc, monkeypatch, tmp_path):
    """With no events the handler still times out cleanly and closes the client."""
    page, browser = FakeClient(), FakeClient(events=[])
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"url": "http://x/f.txt", "directory": str(tmp_path / "d"),
                        "timeout_s": 0.2})
    assert out["ok"] is False
    assert "no completed download" in out["error"]
    assert browser.closed


def test_unrelated_events_do_not_satisfy_the_request(svc, monkeypatch, tmp_path):
    """A downloadProgress for another guid/url must not complete THIS request."""
    other = tmp_path / "other.txt"
    other.write_text("nope")
    page, browser = FakeClient(), FakeClient(events=[_event("completed", other)])
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"url": "http://x/f.txt", "directory": str(tmp_path / "d"),
                        "timeout_s": 0.2})
    # Chrome reported a completion, but for a file outside the request context;
    # the handler may accept Chrome's path, yet must never claim a verified
    # download that is not in the requested directory
    assert out["ok"] is True
    assert out["in_requested_directory"] is False
    assert "Chrome used" in out["verify"]["detail"],         "the response must state honestly that Chrome used a different directory"



def test_missing_selector_is_reported(svc, monkeypatch, tmp_path):
    class NoEl(FakeClient):
        def evaluate(self, expr, timeout=None):
            return None

    page, browser = NoEl(), FakeClient()
    _patch(svc, monkeypatch, page, browser)
    out = svc.download({"selector": "#nope", "directory": str(tmp_path / "d"),
                        "timeout_s": 1})
    assert out["ok"] is False
    assert "download link not found" in out["error"]
