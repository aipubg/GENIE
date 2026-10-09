"""Regression: window title matching must tolerate dynamic title changes.

Applications rewrite their own title as a direct result of an action — Notepad
prefixes '*' when the document is modified. Matching the pre-action title
exactly made `uia.find` return nothing after typing, so a correct action was
reported as failed ("typed text not found in the target document").

Deterministic: tests the matching helper only, no real UIA or desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import uia  # noqa: E402


def test_plain_substring_still_matches():
    assert uia._title_matches("Untitled - Notepad", "Untitled - Notepad")


def test_modified_marker_on_actual_title():
    """Notepad adds a leading '*' once the document is modified."""
    assert uia._title_matches("Untitled - Notepad", "*Untitled - Notepad")


def test_modified_marker_on_query():
    assert uia._title_matches("*Untitled - Notepad", "Untitled - Notepad")


def test_whitespace_is_ignored():
    assert uia._title_matches("  My Doc  ", "My Doc")


def test_case_insensitive():
    assert uia._title_matches("NOTEPAD", "Untitled - notepad")


def test_decoration_anywhere_is_ignored():
    assert uia._title_matches("genie uia set - Notepad", "*genie uia set - Notepad")


def test_unrelated_titles_do_not_match():
    assert not uia._title_matches("Calculator", "Untitled - Notepad")


def test_empty_query_never_matches():
    assert not uia._title_matches("", "Anything")


def test_find_elements_uses_tolerant_matching(monkeypatch):
    """find_elements must resolve a window whose title gained a '*' prefix."""
    from computer import windows_api
    monkeypatch.setattr(windows_api, "IS_WINDOWS", False)
    calls: dict = {}

    class FakeInfo:
        def __init__(self, name):
            self.name = name

    class FakeWin:
        def __init__(self, name):
            self.element_info = FakeInfo(name)

        def children(self):
            return []

    seen = FakeWin("*Untitled - Notepad")     # title changed after typing

    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "_desktop", lambda: type(
        "D", (), {"windows": staticmethod(lambda: [seen])})())

    # must NOT bail out early: the tolerant match should find the window
    result = uia.find_elements(window_title="Untitled - Notepad", limit=3)
    assert result == []          # no descendants, but the window was resolved
    # if matching had failed, find_elements returns [] too — so assert via a
    # descendant-producing window that it really resolved the right window
    class FakeWin2(FakeWin):
        def children(self):
            calls["hit"] = True
            return []
    seen2 = FakeWin2("*Untitled - Notepad")
    monkeypatch.setattr(uia, "_desktop", lambda: type(
        "D", (), {"windows": staticmethod(lambda: [seen2])})())
    uia.find_elements(window_title="Untitled - Notepad", limit=3)
    assert calls.get("hit"), "the window should have been resolved and searched"


def test_app_suffix_fallback_resolves_a_first_line_title(monkeypatch):
    """Notepad embeds the document's first line in the title once modified.

    '*genie uia set - Notepad' becomes '*alpha onegenie uia set - Notepad', so
    no pre-captured title can match. find_elements must fall back to the stable
    application part of the title.
    """

    from computer import windows_api
    monkeypatch.setattr(windows_api, "IS_WINDOWS", False)

    class FakeInfo:
        def __init__(self, name):
            self.name = name

    class FakeWin:
        def __init__(self, name):
            self.element_info = FakeInfo(name)

        def children(self):
            test_app_suffix_fallback_resolves_a_first_line_title.hit = True
            return []

    win = FakeWin("*alpha onegenie uia set - Notepad")
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "_desktop", lambda: type(
        "D", (), {"windows": staticmethod(lambda: [win])})())

    test_app_suffix_fallback_resolves_a_first_line_title.hit = False
    uia.find_elements(window_title="*genie uia set - Notepad", limit=3)
    assert test_app_suffix_fallback_resolves_a_first_line_title.hit,         "the window must still be resolved via the '- Notepad' suffix"
