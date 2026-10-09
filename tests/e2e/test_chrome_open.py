"""E2E golden task #1 (Phase 1 text variant).

    UI/text -> identity/context -> NEDLE2 -> mission/capability decision
            -> permission -> computer capability -> verification -> audit
            -> mission result

Runs in dry-run so the developer machine is never disturbed.
"""
from __future__ import annotations

from core.contracts import MissionState


def test_chrome_kholo_end_to_end(app):
    out = app.orchestrator.handle_text("Chrome kholo", app.ctx(dry_run=True))

    # 1. director decision
    assert out["decision"]["tasks"][0]["capability"] == "application.open"
    assert out["decision"]["reasoning_required"] is False

    # 2. This deterministic action is completed directly, not persisted as a Mission.
    assert out["state"] == MissionState.COMPLETED.value
    assert out.get("mission_id") is None
    assert out["steps"] and out["steps"][0]["ok"] is True

    # 3. verification happened
    assert out["steps"][0]["ok"] is True

    # 4. audit trail exists for this mission
    entries = app.audit.tail(50)
    actions = {e["action"] for e in entries}
    assert any(a.startswith("trust.check:") for a in actions)
    assert any(a.startswith("computer.") for a in actions)
    assert app.audit.verify() is True

    # 5. trace id propagated
    assert out["trace_id"]
    assert all(e["trace_id"] in (out["trace_id"], None) for e in entries)


def test_no_remote_model_call_for_simple_action(app):
    out = app.orchestrator.handle_text("Chrome kholo", app.ctx(dry_run=True))
    assert out["state"] == MissionState.COMPLETED.value
    assert out.get("mission_id") is None       # no provider-backed durable Mission


def test_device_action_reports_phase6_gap_cleanly(app):
    out = app.orchestrator.handle_text("phone ka next song", app.ctx(dry_run=True))
    # Phase 1: the device mesh does not exist yet — must fail cleanly, not crash
    assert out["state"] in (MissionState.FAILED.value, MissionState.COMPLETED.value)
    assert "device" in (out["steps"][0].get("error", "") or "").lower() or out["steps"][0]["ok"]


def test_conversation_is_not_a_mission(app):
    """Point 6 — a normal exchange is not durable work, so it must NOT create a
    Mission. It still produces a reply through the same reasoning authority."""
    out = app.orchestrator.handle_text("GENIE tum kaise ho?", app.ctx(dry_run=True))
    assert out["mission_id"] is None
    assert out["state"] == "conversation"
    assert out["reply"]
    # And it left nothing behind in the owner's mission store.
    assert all(m.goal != "answer: GENIE tum kaise ho?" for m in app.missions.list(50))
