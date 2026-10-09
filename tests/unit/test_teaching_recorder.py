"""Phase 5 — teaching recorder: semantic capture + secret redaction (§5.11-§5.13, §5.26).

A demonstration is recorded *semantically*. Raw coordinates are debug evidence only, and
secrets must never reach the recording in the first place.
"""
from __future__ import annotations

import pytest

from teaching.recorder import TeachingRecorder


@pytest.fixture()
def recorder():
    return TeachingRecorder()


def test_recorder_captures_semantic_events_not_coordinates(recorder):
    recorder.start(goal="write a dated note", application="notepad.exe")
    recorder.note_app_launch("notepad")
    recorder.note_uia_invoke("Notepad", "Save", control_type="MenuItem")
    recorder.note_typing("note for 2026-09-17")
    summary = recorder.summary()
    assert summary["semantic_events"] >= 2
    kinds = [e.kind for e in recorder.events]
    assert "mouse" not in kinds, "a semantic demonstration must not be recorded as clicks"


def test_raw_coordinates_are_flagged_as_debug_evidence(recorder):
    recorder.start(goal="click around")
    event = recorder.note_mouse(844, 512, target="some button")
    assert event["note"].startswith("raw coordinates are debug evidence")
    assert recorder.summary()["raw_coordinates"] == 1


def test_recorder_ignores_events_before_start_and_after_stop(recorder):
    assert recorder.record("typing", text="too early") is None
    recorder.start(goal="x")
    recorder.record("typing", text="captured")
    recorder.stop()
    assert recorder.record("typing", text="too late") is None
    assert len(recorder.events) == 1


def test_recorder_stops_at_the_event_limit():
    recorder = TeachingRecorder(max_events=5)
    recorder.start(goal="x")
    for index in range(10):
        recorder.record("typing", text=f"line {index}")
    assert len(recorder.events) == 5


# ------------------------------------------------------------------ redaction §5.26
def test_password_fields_are_redacted_at_capture_time(recorder):
    recorder.start(goal="log in")
    event = recorder.record("typing", text="hunter2", target_field="password")
    assert event["text"] == "[redacted]"
    assert event["redacted"] is True
    assert "hunter2" not in str(recorder.to_dict())


def test_nested_secret_fields_are_redacted(recorder):
    recorder.start(goal="fill a form")
    event = recorder.record("browser_action", capability="browser.type",
                            params={"selector": "#card", "value": "4111", "cvv": "123"})
    assert event["params"]["cvv"] == "[redacted]"
    assert event["params"]["selector"] == "#card"


def test_secret_looking_values_are_redacted_even_in_ordinary_fields(recorder):
    recorder.start(goal="x")
    event = recorder.record("typing", text="token sk-abcdef1234567890")
    assert "sk-abcdef1234567890" not in event["text"]
    assert "[redacted-token]" in event["text"]


def test_card_and_email_patterns_are_redacted(recorder):
    recorder.start(goal="x")
    card = recorder.record("typing", text="4111111111111111")
    email = recorder.record("typing", text="owner@example.com")
    assert "[redacted-card]" in card["text"]
    assert "[redacted-email]" in email["text"]


def test_password_assignment_pattern_is_redacted(recorder):
    recorder.start(goal="x")
    event = recorder.record("typing", text="password=SuperSecret1")
    assert "SuperSecret1" not in event["text"]


def test_sensitive_field_detection_is_reusable(recorder):
    assert TeachingRecorder.is_sensitive_field("api_key") is True
    assert TeachingRecorder.is_sensitive_field("username") is False


def test_redaction_counter_is_reported(recorder):
    recorder.start(goal="x")
    recorder.record("typing", text="hunter2", target_field="password")
    recorder.record("typing", text="plain")
    assert recorder.summary()["redacted"] == 1


# ------------------------------------------------------------------ session shape
def test_snapshot_records_before_and_after_state(recorder):
    recorder.start(goal="write a note")
    recorder.note_typing("hello")
    recorder.stop()
    payload = recorder.to_dict()
    assert "foreground" in payload["before_state"]
    assert "foreground" in payload["after_state"]


def test_summary_counts_events_by_kind(recorder):
    recorder.start(goal="x")
    recorder.note_app_launch("notepad")
    recorder.note_typing("a")
    recorder.note_typing("b")
    counts = recorder.summary()["by_kind"]
    assert counts["typing"] == 2 and counts["app_launch"] == 1
