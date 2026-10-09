"""Browser target-lifecycle regression + long-lived session stress.

RC item 7 (exact reproduction) and item 8 (persistent-session stress).

Background
----------
The download failure was never a download bug. GENIE selected the page to drive
with `pages[0]`, but Chrome target ordering is not an active-tab guarantee: after
tabs were closed, index 0 became a hidden/background target and Chrome would not
start a download from a hidden document. `BrowserTargetResolver` now chooses the
page by evidence (type, liveness, URL, readyState, visibilityState, ownership).

These tests exist so the defect cannot silently return. They deliberately run the
*sequence* that exposed it, not the download in isolation.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.contracts import CallContext, Persona  # noqa: E402
from security.injection_guard import InjectionGuard  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = [pytest.mark.real_machine,
              pytest.mark.skipif(not sys.platform.startswith("win"),
                                 reason="browser tests run on the Windows machine")]

# A separate CDP port so this never shares a browser with the phase-4 suite.
PORT = 9334


@pytest.fixture(scope="module")
def lab_server():
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(QuietHandler, directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(scope="module")
def browser(tmp_path_factory, lab_server):
    from browser.service import get_browser
    tmp = tmp_path_factory.mktemp("browser-regression")
    b = get_browser(port=PORT, workspace_root=tmp)
    res = b.navigate({"url": f"{lab_server}/browser_lab.html", "wait_s": 30})
    if not res.get("ok"):
        pytest.skip(f"browser unavailable: {res.get('verify')}")
    b.lab_url = f"{lab_server}/browser_lab.html"
    b.site_url = f"{lab_server}/test_site/index.html"
    yield b
    try:
        b.close()
    except Exception:  # noqa: BLE001
        pass


def owner_ctx():
    return CallContext(person_id="owner", persona=Persona.OWNER)


def nav(browser, url: str = ""):
    return browser.handle("browser.navigate",
                          {"url": url or browser.lab_url, "wait_s": 30})


def do_download(browser, tmp_path, name="downloads"):
    target = Path(tmp_path) / name
    res = browser.handle("browser.download", {"selector": "#download-link",
                                              "directory": str(target),
                                              "timeout_s": 30})
    assert res["ok"] is True, res
    assert res["verify"]["verified"] is True, res
    assert Path(res["path"]).exists(), res
    assert Path(res["path"]).stat().st_size > 0, res
    assert res["artifact"]["sha256"], res
    return res


# ------------------------------------------------------------------ item 7
def test_download_after_the_sequence_that_used_to_poison_the_target(browser, tmp_path):
    """Exactly the reproduction: tabs -> tab close -> DOM click -> form typing -> download.

    Before the resolver fix this failed with "download did not start", because
    after the tab close `pages[0]` was a hidden target.
    """
    nav(browser)

    # 1. multiple tabs and switching
    opened = browser.handle("browser.tab_new", {"url": browser.site_url})
    assert opened.get("ok") is True, opened
    listed = browser.handle("browser.tabs_list", {})
    assert listed.get("ok") is True, listed
    if listed.get("count", 0) > 1:
        switched = browser.handle("browser.tab_switch", {"index": 0})
        assert switched.get("ok") is True, switched

    # 2. close a tab (this is what collapsed the tab count 5 -> 2)
    closed = browser.handle("browser.tab_close", {})
    assert closed.get("ok") in (True, False)      # refusal on last tab is fine

    # 3. DOM click
    nav(browser)
    clicked = browser.handle("browser.click", {"selector": "#reveal"})
    assert clicked.get("ok") is True, clicked

    # 4. form typing (the operation that preceded the failure)
    typed = browser.handle("browser.type", {"selector": "#name",
                                            "text": "genie regression"})
    assert typed.get("ok") is True, typed

    # 5. the download that used to fail
    do_download(browser, tmp_path)


# ------------------------------------------------------------------ item 8
def test_long_lived_session_stress_then_two_downloads(browser, tmp_path):
    """GENIE keeps browser sessions alive: prove downloads still work after use."""
    nav(browser)

    for call in ({"selector": "#reveal"},
                 ):
        r = browser.handle("browser.click", call)
        assert r.get("ok") is True, r
    r = browser.handle("browser.type", {"selector": "#name", "text": "stress one"})
    assert r.get("ok") is True, r
    r = browser.handle("browser.select", {"selector": "#colour", "value": "Blue"})
    assert r.get("ok") is True, r
    r = browser.handle("browser.checkbox", {"selector": "#agree", "checked": True})
    assert r.get("ok") is True, r

    # redirect + history
    nav(browser, f"{browser.lab_url}")
    browser.handle("browser.history", {"direction": "back"})

    # extraction from the live page
    extracted = browser.handle("browser.extract", {})
    assert extracted.get("ok") is True, extracted

    # more navigation, then a download
    nav(browser, browser.site_url)
    nav(browser)
    first = do_download(browser, tmp_path, "stress-first")

    # keep going, then download AGAIN — the persistent-session case
    r = browser.handle("browser.type", {"selector": "#name", "text": "stress two"})
    assert r.get("ok") is True, r
    r = browser.handle("browser.click", {"selector": "#reveal"})
    assert r.get("ok") is True, r
    nav(browser, browser.site_url)
    nav(browser)
    second = do_download(browser, tmp_path, "stress-second")

    assert first["path"] != second["path"]
    assert second["verify"]["verified"] is True


def test_target_used_is_visible_after_tab_churn(browser):
    """The page GENIE drives must be a real, visible page — not a hidden leftovers."""
    nav(browser)
    browser.handle("browser.tab_new", {"url": browser.site_url})
    browser.handle("browser.tab_close", {})
    nav(browser)

    client = browser._connect_page()
    state = client.evaluate(
        "JSON.stringify({v: document.visibilityState, r: document.readyState,"
        " u: location.href})")
    data = __import__("json").loads(state or "{}")
    assert data.get("v") == "visible", \
        f"GENIE is driving a hidden page ({data}) — target selection regressed"
    assert data.get("r") in ("complete", "interactive"), data
