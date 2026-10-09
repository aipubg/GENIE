"""Deterministic tests for BrowserTargetResolver (RC item 6).

Chrome target ordering is not an active-tab guarantee, so GENIE must never pick a
page by array position. These prove the resolver selects by evidence and only
creates a target as recovery.

All CDP access is stubbed — no Chrome required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser.targets import BrowserTargetResolver  # noqa: E402


def page(tid: str, url: str = "http://x/", visibility: str = "visible",
         ready: str = "complete", alive: bool = True) -> dict:
    return {"id": tid, "type": "page", "url": url,
            "webSocketDebuggerUrl": f"ws://{tid}"}


def make(pages, states=None):
    """Build a resolver whose probe returns the per-id state map."""
    states = states or {}

    def _state(t):
        return states.get(t.get("id"), {})

    return BrowserTargetResolver(
        port=1,
        list_targets=lambda: list(pages),
        probe=_state,
        create=lambda url: None,
    )


def vis(tid, visibility="visible", ready="complete", alive=True, url=None):
    s = {"visibilityState": visibility, "readyState": ready, "alive": alive}
    if url:
        s["url"] = url
    return s


# ------------------------------------------------------------------- TEST A
def test_A_hidden_first_page_is_not_chosen_because_it_is_index_0():
    """pages[0] is hidden, pages[1] is visible -> the visible one must win."""
    pages = [page("A"), page("B")]
    states = {"A": vis("A", "hidden"), "B": vis("B", "visible")}
    r = make(pages, states)
    r.mark_owned("A")
    r.mark_owned("B")
    chosen = r.resolve()
    assert chosen is not None
    assert chosen["id"] == "B", \
        "must not select A merely because it is index 0"


# ------------------------------------------------------------------- TEST B
def test_B_tracked_target_is_kept_while_healthy():
    pages = [page("A"), page("B")]
    states = {"A": vis("A"), "B": vis("B")}
    r = make(pages, states)
    r.mark_owned("A")
    r.mark_owned("B")
    r.set_active("B")
    assert r.resolve()["id"] == "B", "a healthy tracked target must be preserved"


def test_B2_tracked_but_hidden_target_is_replaced_by_a_visible_owned_one():
    pages = [page("A"), page("B")]
    states = {"A": vis("A", "visible"), "B": vis("B", "hidden")}
    r = make(pages, states)
    r.mark_owned("A")
    r.mark_owned("B")
    r.set_active("B")
    assert r.resolve()["id"] == "A"


# ------------------------------------------------------------------- TEST C
def test_C_tracked_target_disappears_so_another_owned_target_is_used():
    pages = [page("B"), page("C")]
    states = {"B": vis("B"), "C": vis("C")}
    r = make(pages, states)
    r.mark_owned("B")
    r.mark_owned("C")
    r.set_active("B")
    r.forget("B")                     # B vanished
    chosen = r.resolve()
    assert chosen is not None and chosen["id"] == "C"


def test_C2_no_owned_targets_left_returns_none_without_creation():
    r = make([page("U")], {"U": vis("U")})   # U is not owned
    assert r.resolve() is None
    assert "no owned usable target" in r.last_reason


# ------------------------------------------------------------------- TEST D
def test_D_creates_a_new_target_only_when_required():
    created = {"id": "NEW", "type": "page", "url": "http://x/",
               "webSocketDebuggerUrl": "ws://NEW"}
    calls = []

    def _create(url):
        calls.append(url)
        return created

    r = BrowserTargetResolver(port=1, list_targets=lambda: [], probe=lambda t: {},
                              create=_create)
    assert r.resolve(allow_create=False) is None
    assert calls == [], "must not create when creation is not allowed"

    chosen = r.resolve(expected_url="http://x/", allow_create=True)
    assert chosen is not None and chosen["id"] == "NEW"
    assert calls == ["http://x/"]
    assert "recovery" in r.last_reason


def test_D2_no_creation_when_an_owned_target_is_already_usable():
    calls = []
    r = BrowserTargetResolver(
        port=1, list_targets=lambda: [page("A")], probe=lambda t: vis("A"),
        create=lambda url: calls.append(url) or page("NEW"))
    r.mark_owned("A")
    assert r.resolve(allow_create=True)["id"] == "A"
    assert calls == [], "recovery must not fire when a target already works"


# ------------------------------------------------------------------- TEST E
def test_E_unrelated_user_target_is_never_hijacked():
    pages = [page("USER"), page("MINE")]
    states = {"USER": vis("USER"), "MINE": vis("MINE")}
    r = make(pages, states)
    r.mark_owned("MINE")              # only MINE belongs to GENIE
    chosen = r.resolve()
    assert chosen["id"] == "MINE"

    # and with no owned targets at all, an unrelated tab is still not taken
    r2 = make(pages, states)
    assert r2.resolve() is None, "must not silently hijack a user tab"


# ------------------------------------------------------------------- TEST F
def test_F_url_matching_target_is_preferred():
    pages = [page("OTHER", url="http://elsewhere/"),
             page("WANTED", url="http://lab/page.html")]
    states = {"OTHER": vis("OTHER"), "WANTED": vis("WANTED")}
    r = make(pages, states)
    r.mark_owned("OTHER")
    r.mark_owned("WANTED")
    chosen = r.resolve(expected_url="http://lab/page.html")
    assert chosen["id"] == "WANTED"


def test_F2_url_match_outranks_visibility_when_both_owned():
    pages = [page("VIS", url="http://elsewhere/"),
             page("URL", url="http://lab/page.html")]
    states = {"VIS": vis("VIS", "visible"), "URL": vis("URL", "visible")}
    r = make(pages, states)
    r.mark_owned("VIS")
    r.mark_owned("URL")
    assert r.resolve(expected_url="http://lab/page.html")["id"] == "URL"


# --------------------------------------------------------------- misc guards
def test_dead_target_is_not_selected():
    pages = [page("DEAD"), page("OK")]
    states = {"DEAD": vis("DEAD", alive=False), "OK": vis("OK")}
    r = make(pages, states)
    r.mark_owned("DEAD")
    r.mark_owned("OK")
    assert r.resolve()["id"] == "OK"


def test_non_page_targets_are_ignored():
    pages = [{"id": "EXT", "type": "background_page", "url": "http://x/",
              "webSocketDebuggerUrl": "ws://EXT"},
             page("P")]
    r = make(pages, {"P": vis("P")})
    r.mark_owned("EXT")
    r.mark_owned("P")
    assert r.resolve()["id"] == "P"


def test_bad_ready_state_is_rejected():
    pages = [page("LOADING"), page("DONE")]
    states = {"LOADING": vis("LOADING", ready="bogus"), "DONE": vis("DONE")}
    r = make(pages, states)
    r.mark_owned("LOADING")
    r.mark_owned("DONE")
    assert r.resolve()["id"] == "DONE"


def test_ownership_helpers():
    r = make([], {})
    r.set_active("T")
    assert "T" in r.owned and r.active_id == "T"
    r.forget("T")
    assert "T" not in r.owned and r.active_id == ""
