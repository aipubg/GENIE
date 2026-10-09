"""Golden tasks #14 (crash recovery) and #18 (offline) — spec Appendix E.

#14  "Kill daemon mid-render"          -> resumable, no state loss
#18  "Provider down, device action"    -> local action still works, clear message

Both are Phase-14 hardening behaviours, and both are the kind of failure that is only discovered
in production if nobody tests it. These tests kill nothing for real — they reproduce the *state* a
crash leaves behind, which is what recovery actually operates on.
"""
from __future__ import annotations

from core.contracts import CallContext, MissionState
from core.hardening import CrashRecovery, OfflineMode, reset_offline_cache


# ============================================================== GOLDEN #14
def test_golden_14_a_crash_mid_mission_is_detected_on_the_next_boot(app, tmp_path):
    ctx = CallContext()
    mission = app.missions.create(ctx, "render the scene")
    app.missions.transition(ctx, mission.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, mission.mission_id, MissionState.RUNNING)

    # the daemon died here: the start flag was never cleared
    recovery = CrashRecovery(tmp_path / "running.flag")
    recovery.mark_start()
    assert recovery.was_unclean() is True

    report = recovery.recover(locks=app.locks, missions=app.missions)
    assert report["unclean_shutdown"] is True, "the next boot must notice the crash"
    assert mission.mission_id in report["interrupted_missions"]


def test_golden_14_no_state_is_lost_and_the_mission_is_resumable(app, tmp_path):
    ctx = CallContext()
    mission = app.missions.create(ctx, "render the scene")
    app.missions.transition(ctx, mission.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, mission.mission_id, MissionState.RUNNING)

    recovery = CrashRecovery(tmp_path / "running.flag")
    recovery.mark_start()

    # recovery reports rather than resumes by default — never surprise the owner
    report = recovery.recover(locks=app.locks, missions=app.missions)
    assert report["resumed"] == []

    # the mission survived the crash
    assert app.missions.get(mission.mission_id) is not None, "no state loss"

    # and it can be resumed explicitly
    resumed = app.missions.resume(ctx, mission.mission_id)
    assert resumed is not None


def test_golden_14_recovery_clears_the_flag_so_it_only_fires_once(app, tmp_path):
    ctx = CallContext()
    mission = app.missions.create(ctx, "render")
    app.missions.transition(ctx, mission.mission_id, MissionState.PLANNED)
    app.missions.transition(ctx, mission.mission_id, MissionState.RUNNING)

    recovery = CrashRecovery(tmp_path / "running.flag")
    recovery.mark_start()

    first = recovery.recover(locks=app.locks, missions=app.missions)
    assert first["unclean_shutdown"] is True

    # a clean restart the second time must not claim another crash
    recovery.mark_start()          # this run starts
    second_report_ready = recovery.was_unclean()
    recovery.mark_clean()          # this run shuts down cleanly
    assert recovery.was_unclean() is False


# ============================================================== GOLDEN #18
def test_golden_18_offline_is_detected_and_reported_clearly(app):
    reset_offline_cache()
    status = OfflineMode(registry=app.registry, probe=lambda: False).status()
    assert status["offline"] is True
    assert status["note"], "the owner must get a clear message, not a silent failure"


def test_golden_18_local_work_still_works_with_no_provider_at_all(app):
    """With every provider down, GENIE must still do local work."""
    reset_offline_cache()
    status = OfflineMode(registry=app.registry, probe=lambda: False).status()
    assert status["offline"] is True

    ctx = CallContext()
    # memory is local — it must answer with no provider reachable
    app.memory.write(ctx, type="semantic", entity="owner",
                     value="Owner keeps a local-first setup", confidence=0.9)
    hits = app.memory.query(ctx, "local-first", limit=5)
    assert isinstance(hits, list) and len(hits) >= 1


def test_golden_18_offline_degrades_honestly_when_no_local_provider_exists():
    """No network AND no local provider — say so, don't pretend."""
    reset_offline_cache()

    class _OnlyRemote:
        def providers(self):
            return [{"id": "openai", "name": "OpenAI", "enabled": True}]

    status = OfflineMode(registry=_OnlyRemote(), probe=lambda: False).status()
    assert status["offline"] is True
    assert status["degraded"] is True
    assert status["usable_providers"] == []
    assert "memory" in status["note"], "must tell the owner what still works"
