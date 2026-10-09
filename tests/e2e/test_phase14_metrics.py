"""Phase 14 — wrong-action rate instrumentation.

The exit gate says "wrong-action rate within target", so the classification rules matter as much
as the arithmetic. The important one: **a correctly-refused action is not a wrong action.** If
denials counted as failures, the metric would discourage the agent from ever asking permission —
exactly the wrong incentive.
"""
from __future__ import annotations

from core.action_metrics import DEFAULT_TARGET, ActionMeter, classify


# --------------------------------------------------------------- classification
def test_outcomes_are_classified_correctly():
    assert classify(True, True) == "success"
    assert classify(False, False, "boom") == "failed"
    assert classify(True, False) == "unverified"          # claimed ok, verification disagreed
    assert classify(False, False, "denied: no scope") == "refused"
    assert classify(False, False, "confirmation required") == "refused"
    assert classify(False, False, "blocked in safe mode") == "blocked"


# --------------------------------------------------------------- the rate
def test_a_clean_run_has_a_zero_wrong_action_rate():
    meter = ActionMeter()
    for _ in range(10):
        meter.record(capability="files.read", ok=True, verified=True)
    rate = meter.rate()
    assert rate["executed"] == 10 and rate["wrong"] == 0
    assert rate["rate"] == 0.0 and rate["within_target"] is True


def test_unverified_success_counts_as_wrong():
    """The dangerous case: it looks fine but isn't."""
    meter = ActionMeter()
    meter.record(capability="browser.click", ok=True, verified=True)
    meter.record(capability="browser.click", ok=True, verified=False)
    rate = meter.rate()
    assert rate["executed"] == 2 and rate["wrong"] == 1
    assert rate["rate"] == 0.5
    assert rate["within_target"] is False


def test_failures_count_as_wrong():
    meter = ActionMeter()
    meter.record(capability="shell.run", ok=False, verified=False, detail="command failed")
    assert meter.rate()["wrong"] == 1


def test_a_refused_action_is_not_a_wrong_action():
    """Correctly refusing is the safety layer working — it must not inflate the rate."""
    meter = ActionMeter()
    meter.record(capability="files.delete", ok=False, verified=False, detail="denied: no scope")
    meter.record(capability="files.delete", ok=False, verified=False,
                 detail="confirmation required")
    rate = meter.rate()
    assert rate["executed"] == 0, "refusals are not executed actions"
    assert rate["wrong"] == 0
    assert rate["counts"]["refused"] == 2
    assert rate["within_target"] is True


def test_safe_mode_blocks_are_not_wrong_actions():
    meter = ActionMeter()
    meter.record(capability="browser.navigate", ok=False, verified=False,
                 detail="blocked in safe mode (matches 'browser.')")
    assert meter.rate()["executed"] == 0
    assert meter.rate()["counts"]["blocked"] == 1


# ---------------------------------------------------------------- target
def test_target_is_enforced_and_reported():
    meter = ActionMeter(target=0.1)
    for _ in range(9):
        meter.record(capability="x", ok=True, verified=True)
    assert meter.status()["within_target"] is True
    meter.record(capability="x", ok=False, detail="failed")   # 1/10 = 10% — at target
    assert meter.rate()["rate"] == 0.1
    assert meter.status()["within_target"] is True
    meter.record(capability="x", ok=False, detail="failed")   # 2/11 — above target
    status = meter.status()
    assert status["within_target"] is False
    assert "ABOVE target" in status["note"]


def test_default_target_is_two_percent():
    assert DEFAULT_TARGET == 0.02
    assert ActionMeter().target == 0.02


# --------------------------------------------------------------- reporting
def test_status_reports_counts_and_a_human_note():
    meter = ActionMeter()
    meter.record(capability="a", ok=True, verified=True)
    meter.record(capability="b", ok=False, detail="nope")
    status = meter.status()
    assert status["executed"] == 2 and status["wrong"] == 1
    assert status["counts"]["success"] == 1 and status["counts"]["failed"] == 1
    assert "ABOVE target" in status["note"]


def test_recent_and_reset():
    meter = ActionMeter()
    meter.record(capability="a", ok=True, verified=True)
    assert len(meter.recent()) == 1
    meter.reset()
    assert meter.recent() == []
    assert meter.rate()["executed"] == 0


def test_rate_can_be_windowed():
    meter = ActionMeter()
    meter.record(capability="old", ok=False, detail="failed")
    meter.reset()
    meter.record(capability="new", ok=True, verified=True)
    assert meter.rate(window_s=3600)["executed"] == 1


# --------------------------------------------------------------- persistence
def test_outcomes_persist_when_a_db_is_available(app):
    meter = ActionMeter(db=app.db)
    meter.record(capability="files.read", ok=True, verified=True)
    rows = app.db.query("SELECT * FROM agent_action_outcomes")
    assert len(rows) >= 1
    assert rows[-1]["capability"] == "files.read"
    assert rows[-1]["kind"] == "success"


def test_meter_works_without_a_db():
    meter = ActionMeter()          # no db — must not raise
    meter.record(capability="x", ok=True, verified=True)
    assert meter.rate()["executed"] == 1
