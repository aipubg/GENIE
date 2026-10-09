"""Mission state machine, cancellation, resumability, locks."""
from __future__ import annotations

import pytest

from core.contracts import MissionState


def test_legal_lifecycle(app):
    ctx = app.ctx()
    m = app.missions.create(ctx, "open chrome")
    assert m.state is MissionState.CREATED
    app.missions.transition(ctx, m.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, m.mission_id, MissionState.RUNNING)
    app.missions.transition(ctx, m.mission_id, MissionState.VERIFYING)
    app.missions.transition(ctx, m.mission_id, MissionState.COMPLETED)
    assert app.missions.get(m.mission_id).state is MissionState.COMPLETED


def test_illegal_transition_rejected(app):
    ctx = app.ctx()
    m = app.missions.create(ctx, "x")
    with pytest.raises(Exception):
        app.missions.transition(ctx, m.mission_id, MissionState.COMPLETED)


def test_cancel_releases_locks(app):
    ctx = app.ctx()
    m = app.missions.create(ctx, "render")
    app.locks.acquire("desktop.control", m.mission_id)
    app.missions.transition(ctx, m.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, m.mission_id, MissionState.RUNNING)
    app.missions.cancel(ctx, m.mission_id)
    assert app.missions.get(m.mission_id).state is MissionState.CANCELLED
    assert app.locks.holders() == []


def test_snapshot_and_resume_after_interrupt(app):
    ctx = app.ctx()
    m = app.missions.create(ctx, "long render")
    app.missions.transition(ctx, m.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, m.mission_id, MissionState.RUNNING)
    snap = app.missions.snapshot(m.mission_id)
    assert snap["goal"] == "long render" and snap["state"] == "RUNNING"
    assert m.mission_id in [x.mission_id for x in app.missions.interrupted()]
    resumed = app.missions.resume(ctx, m.mission_id)
    assert resumed.state is MissionState.RUNNING


def test_lock_lease_expires(app):
    app.locks.acquire("mouse", "agent-1", ttl_ms=1)
    import time
    time.sleep(0.01)
    assert app.locks.acquire("mouse", "agent-2") is True
    assert app.locks.purge_stale() >= 0


def test_cost_accounting(app):
    ctx = app.ctx()
    m = app.missions.create(ctx, "costly")
    app.missions.add_cost(m.mission_id, 0.25, provider="deepseek")
    app.missions.add_cost(m.mission_id, 0.10, provider="deepseek")
    assert abs(app.missions.get(m.mission_id).cost_usd - 0.35) < 1e-9
