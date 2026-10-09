"""Phase 9 — proactivity.

Exit gate: *golden #13 — risky file delete → timely, non-annoying intervention; duplicate
suppression + quiet hours verified.*

The tests drive the real scoring function, the real notifier and the real orchestrator turn loop.
Time is injected wherever the outcome depends on the clock, so quiet hours and decay are tested
deterministically rather than by hoping the suite runs at night.
"""
from __future__ import annotations

import time

import pytest

from core.contracts import CallContext, Persona, TaskType
from director.base import DirectorDecision, DirectorTask
from proactive.contracts import (CHANNEL_FOR_OUTCOME, Candidate, Channel, EventClass, Outcome,
                                 OUTCOME_RANK, QuietHours, clamp_outcome)
from proactive.notifications import ESCALATION_MARGIN, NotificationPolicy, Notifier
from proactive.scoring import OUTCOME_THRESHOLDS, URGENT_FLOOR, score_candidate
from proactive.service import DESTRUCTIVE_CAPABILITIES, PURE_DESTRUCTIVE, ProactiveService


def service(app, **kw) -> ProactiveService:
    return ProactiveService(app.db, audit=app.audit, **kw)


def at_hour(hour: int) -> float:
    """A timestamp at a given local hour today (for `QuietHours.active(when=...)`)."""
    return time.mktime((2026, 9, 17, hour, 0, 0, 0, 0, -1))


def clock(hour: int):
    """A callable clock fixed at a given local hour (for injecting into the Notifier)."""
    stamp = at_hour(hour)
    return lambda: stamp


# ==================================================================== scoring
def test_a_multiplicative_score_collapses_when_any_factor_is_zero():
    """The point of multiplication: one fatal factor must beat three mediocre ones."""
    strong = Candidate(urgency=0.9, relevance=0.9, confidence=0.9, interruption_cost=0.1)
    assert score_candidate(strong).score > 0.5
    irrelevant = Candidate(urgency=0.9, relevance=0.0, confidence=0.9, interruption_cost=0.1)
    assert score_candidate(irrelevant).score == 0.0


def test_a_busy_owner_suppresses_a_message():
    calm = Candidate(urgency=0.8, relevance=0.8, confidence=0.9, current_task_load=0.0,
                     interruption_cost=0.2)
    busy = Candidate(urgency=0.8, relevance=0.8, confidence=0.9, current_task_load=0.9,
                     interruption_cost=0.2)
    assert score_candidate(busy).score < score_candidate(calm).score


def test_a_neutral_preference_does_not_halve_the_score():
    """0.5 means neutral. Treating it as a literal multiplier would halve every warning."""
    neutral = Candidate(urgency=0.9, relevance=0.85, confidence=0.95, interruption_cost=0.2,
                        user_preference=0.5)
    assert score_candidate(neutral).factors["user_preference"] == 1.0


def test_the_owner_can_suppress_a_class():
    disliked = Candidate(urgency=0.9, relevance=0.9, confidence=0.9, interruption_cost=0.1,
                         user_preference=0.1)
    liked = Candidate(urgency=0.9, relevance=0.9, confidence=0.9, interruption_cost=0.1,
                      user_preference=0.9)
    assert score_candidate(disliked).score < score_candidate(liked).score


def test_the_owner_cannot_inflate_a_class_beyond_its_merits():
    liked = Candidate(urgency=0.5, relevance=0.5, confidence=0.5, interruption_cost=0.0,
                      user_preference=1.0)
    neutral = Candidate(urgency=0.5, relevance=0.5, confidence=0.5, interruption_cost=0.0,
                        user_preference=0.5)
    assert score_candidate(liked).score == score_candidate(neutral).score


def test_thresholds_map_to_every_outcome():
    """A broad sweep must reach every outcome the vocabulary offers (except INTERRUPT, which
    also requires an urgent override)."""
    seen = set()
    for urgency in (0.05, 0.2, 0.35, 0.5, 0.7, 0.85, 1.0):
        for relevance in (0.2, 0.5, 0.8, 1.0):
            for confidence in (0.3, 0.7, 1.0):
                for cost in (0.0, 0.3):
                    candidate = Candidate(urgency=urgency, relevance=relevance,
                                          confidence=confidence, interruption_cost=cost,
                                          current_task_load=0.0)
                    seen.add(score_candidate(candidate).outcome)
    assert {Outcome.IGNORE.value, Outcome.SAVE.value, Outcome.SHOW_SILENTLY.value,
            Outcome.MENTION_LATER.value, Outcome.SPEAK_NOW.value} <= seen, sorted(seen)


def test_a_quiet_class_is_ignored_not_shouted():
    candidate = Candidate(urgency=0.2, relevance=0.3, confidence=0.5, interruption_cost=0.5)
    assert score_candidate(candidate).outcome == Outcome.IGNORE.value


def test_an_urgent_candidate_has_a_visibility_floor():
    """A safety signal may be reduced to a silent toast, never to nothing."""
    candidate = Candidate(urgency=1.0, relevance=1.0, confidence=1.0, interruption_cost=0.0,
                          current_task_load=0.99, urgent_override=True)
    decision = score_candidate(candidate)
    assert OUTCOME_RANK[decision.outcome] >= OUTCOME_RANK[URGENT_FLOOR]


def test_the_reason_names_every_factor():
    decision = score_candidate(Candidate(urgency=0.7, relevance=0.6, confidence=0.8))
    for name in ("urgency", "relevance", "confidence", "current_task", "interruption_cost",
                 "user_preference"):
        assert name in decision.factors
        assert name in decision.reason


# ================================================================ quiet hours
def test_quiet_hours_window_handles_a_midnight_wrap():
    window = QuietHours(start_hour=22, end_hour=7)
    assert window.active(at_hour(23)) is True
    assert window.active(at_hour(3)) is True
    assert window.active(at_hour(6)) is True
    assert window.active(at_hour(7)) is False
    assert window.active(at_hour(14)) is False


def test_a_daytime_window_works_too():
    window = QuietHours(start_hour=9, end_hour=17)
    assert window.active(at_hour(12)) is True
    assert window.active(at_hour(8)) is False
    assert window.active(at_hour(20)) is False


def test_a_disabled_window_is_never_active():
    assert QuietHours(start_hour=0, end_hour=23, enabled=False).active(at_hour(12)) is False


def test_an_equal_start_and_end_means_no_window():
    assert QuietHours(start_hour=9, end_hour=9).active(at_hour(9)) is False


def test_clamping_only_ever_reduces():
    assert clamp_outcome(Outcome.INTERRUPT.value, Outcome.SHOW_SILENTLY.value) == \
        Outcome.SHOW_SILENTLY.value
    assert clamp_outcome(Outcome.SAVE.value, Outcome.SHOW_SILENTLY.value) == Outcome.SAVE.value


def test_quiet_hours_clamp_an_interruption_to_a_toast(app):
    notifier = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(23))
    notifier.set_quiet_hours(start_hour=22, end_hour=7)
    notifier.policy.quiet_hours[0].enabled = True
    notification = notifier.submit(Candidate(
        event_class=EventClass.MISSION_FAILED.value, title="mission failed",
        urgency=1.0, relevance=1.0, confidence=1.0, interruption_cost=0.0))
    assert OUTCOME_RANK[notification.outcome] <= OUTCOME_RANK[Outcome.SHOW_SILENTLY.value]
    assert "quiet hours" in notification.suppression_reason


def test_an_urgent_override_pierces_quiet_hours(app):
    """A destructive action at 2am must still be visible."""
    notifier = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(2))
    notifier.set_quiet_hours(start_hour=22, end_hour=7)
    notification = notifier.submit(Candidate(
        event_class=EventClass.RISKY_ACTION.value, title="about to delete",
        urgency=1.0, relevance=1.0, confidence=1.0, interruption_cost=0.0,
        urgent_override=True))
    assert notification.outcome in notifier.policy.urgent_whitelist
    assert notification.suppressed is False


def test_a_non_urgent_message_is_clamped_at_night(app):
    notifier = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(23))
    notifier.set_quiet_hours(start_hour=22, end_hour=7)
    notification = notifier.submit(Candidate(
        event_class=EventClass.PERCEPTION.value, title="someone in the hallway",
        urgency=1.0, relevance=1.0, confidence=1.0, interruption_cost=0.0,
        urgent_override=False))
    assert OUTCOME_RANK[notification.outcome] <= OUTCOME_RANK[Outcome.SHOW_SILENTLY.value]


def test_quiet_hours_are_scoped_per_zone(app):
    notifier = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(23))
    notifier.set_quiet_hours(start_hour=22, end_hour=7, zone_id="bedroom")
    # a hallway message is unaffected by the bedroom window
    notifier.submit(Candidate(event_class=EventClass.PERCEPTION.value, title="hall",
                              urgency=1.0, relevance=1.0, confidence=1.0,
                              interruption_cost=0.0, zone_id="hallway"))
    assert notifier.policy.window_for("owner", "hallway") is None
    assert notifier.policy.window_for("owner", "bedroom") is not None


def test_quiet_hours_honour_the_injected_clock_not_wall_time(app):
    """Regression: the active check must use the injected `now`, not the wall clock.

    Without this, the suite only passed when physically run during quiet hours (the bug
    surfaced when a run slipped past 07:00 local and three tests flipped to red)."""
    day = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(12))
    day.set_quiet_hours(start_hour=22, end_hour=7)
    day_note = day.submit(Candidate(event_class=EventClass.PERCEPTION.value, title="x",
                                    urgency=1.0, relevance=1.0, confidence=1.0,
                                    interruption_cost=0.0))
    assert OUTCOME_RANK[day_note.outcome] > OUTCOME_RANK[Outcome.SHOW_SILENTLY.value], \
        "daytime (injected): quiet hours must NOT clamp"

    night = Notifier(policy=NotificationPolicy(), db=app.db, now=clock(23))
    night.set_quiet_hours(start_hour=22, end_hour=7)
    night_note = night.submit(Candidate(event_class=EventClass.PERCEPTION.value, title="x",
                                        urgency=1.0, relevance=1.0, confidence=1.0,
                                        interruption_cost=0.0))
    assert OUTCOME_RANK[night_note.outcome] <= OUTCOME_RANK[Outcome.SHOW_SILENTLY.value], \
        "night (injected): quiet hours must clamp"


def test_quiet_hours_persist(app):
    svc = service(app)
    svc.set_quiet_hours(start_hour=21, end_hour=6)
    fresh = service(app)
    assert any(w.start_hour == 21 and w.end_hour == 6 for w in fresh.notifier.policy.quiet_hours)


# ======================================================= duplicate suppression
def test_a_duplicate_class_is_suppressed_within_the_window(app):
    notifier = Notifier(policy=NotificationPolicy(dedupe_window_s=300), db=app.db)
    first = notifier.submit(Candidate(event_class=EventClass.DEVICE_OFFLINE.value,
                                      title="phone offline", urgency=0.9, relevance=0.9,
                                      confidence=1.0, interruption_cost=0.0))
    second = notifier.submit(Candidate(event_class=EventClass.DEVICE_OFFLINE.value,
                                       title="phone offline", urgency=0.9, relevance=0.9,
                                       confidence=1.0, interruption_cost=0.0))
    assert first.suppressed is False
    assert second.suppressed is True
    assert "duplicate" in second.suppression_reason


def test_a_materially_worse_repeat_escapes_suppression(app):
    """Suppressing an escalating problem is how a warning becomes useless."""
    notifier = Notifier(policy=NotificationPolicy(dedupe_window_s=300), db=app.db)
    notifier.submit(Candidate(event_class=EventClass.RISKY_ACTION.value, title="small risk",
                              urgency=0.5, relevance=0.6, confidence=0.9,
                              interruption_cost=0.3))
    worse = notifier.submit(Candidate(event_class=EventClass.RISKY_ACTION.value,
                                      title="much worse", urgency=1.0, relevance=1.0,
                                      confidence=1.0, interruption_cost=0.0))
    assert worse.suppressed is False, "an escalating risk must not be deduplicated away"
    assert worse.score >= notifier._last[EventClass.RISKY_ACTION.value]["score"]


def test_different_classes_are_not_deduplicated_against_each_other(app):
    notifier = Notifier(policy=NotificationPolicy(dedupe_window_s=300), db=app.db)
    notifier.submit(Candidate(event_class=EventClass.DEVICE_OFFLINE.value, title="a",
                              urgency=0.9, relevance=0.9, confidence=1.0,
                              interruption_cost=0.0))
    other = notifier.submit(Candidate(event_class=EventClass.MISSION_FAILED.value, title="b",
                                      urgency=0.9, relevance=0.9, confidence=1.0,
                                      interruption_cost=0.0))
    assert other.suppressed is False


def test_the_window_expires(app):
    clock = {"now": 1000.0}
    notifier = Notifier(policy=NotificationPolicy(dedupe_window_s=60), db=app.db,
                        now=lambda: clock["now"])
    notifier.submit(Candidate(event_class=EventClass.REMINDER.value, title="a", urgency=0.9,
                              relevance=0.9, confidence=1.0, interruption_cost=0.0))
    clock["now"] += 120
    again = notifier.submit(Candidate(event_class=EventClass.REMINDER.value, title="a",
                                      urgency=0.9, relevance=0.9, confidence=1.0,
                                      interruption_cost=0.0))
    assert again.suppressed is False


def test_an_ignored_candidate_is_recorded_as_suppressed(app):
    notifier = Notifier(policy=NotificationPolicy(), db=app.db)
    notification = notifier.submit(Candidate(event_class=EventClass.GENERAL.value, title="noise",
                                             urgency=0.05, relevance=0.05, confidence=0.2))
    assert notification.suppressed is True
    assert notification.delivered is False


# =============================================================== traceability
def test_every_notification_explains_itself(app):
    svc = service(app)
    result = svc.warn_risky_action("files.delete", {"path": "D:/thesis.docx"})
    notification_id = result.notification["notification_id"]
    explanation = svc.explain(notification_id)
    assert explanation["ok"] is True
    assert "score" in explanation["why"]
    assert "risky_action" in explanation["why"]


def test_a_notification_carries_its_source_and_mission(app):
    svc = service(app)
    result = svc.warn_risky_action("files.delete", {"path": "x.txt"}, mission_id="mis_42")
    assert result.notification["mission_id"] == "mis_42"
    assert "files.delete" in result.notification["source_event"]


def test_explaining_an_unknown_notification_is_honest(app):
    assert service(app).explain("note_missing")["ok"] is False


def test_notifications_are_persisted_with_factors(app):
    svc = service(app)
    svc.warn_risky_action("files.delete", {"path": "x.txt"})
    rows = app.db.query("SELECT * FROM proactive_notifications")
    assert rows
    assert "urgency" in rows[0]["factors"]


def test_delivery_goes_to_the_owners_zone_when_presence_knows_it(app):
    from perception.presence import PresenceEngine
    presence = PresenceEngine()
    presence.observe("workshop", "camera", 0.95)
    notifier = Notifier(policy=NotificationPolicy(), db=app.db, presence=presence)
    notification = notifier.submit(Candidate(event_class=EventClass.PERCEPTION.value,
                                             title="someone is in the workshop",
                                             urgency=0.9, relevance=0.9, confidence=0.9,
                                             interruption_cost=0.0))
    assert notification.device_id == "pc_main"


def test_a_named_device_wins(app):
    notifier = Notifier(policy=NotificationPolicy(), db=app.db)
    notification = notifier.submit(Candidate(event_class=EventClass.DEVICE_OFFLINE.value,
                                             title="phone offline", urgency=0.9,
                                             relevance=0.9, confidence=1.0,
                                             interruption_cost=0.0, device_id="phone_main"))
    assert notification.device_id == "phone_main"


def test_channels_match_outcomes(app):
    assert CHANNEL_FOR_OUTCOME[Outcome.INTERRUPT.value] == Channel.URGENT_INTERRUPT.value
    assert CHANNEL_FOR_OUTCOME[Outcome.SPEAK_NOW.value] == Channel.VOICE_ALERT.value
    assert CHANNEL_FOR_OUTCOME[Outcome.SHOW_SILENTLY.value] == Channel.TOAST.value
    assert CHANNEL_FOR_OUTCOME[Outcome.SAVE.value] == Channel.SILENT_LOG.value


# ==================================================================== golden #13
def test_golden_13_a_risky_delete_is_flagged_before_it_runs(app):
    svc = service(app)
    assessment = svc.warn_risky_action("files.delete", {"path": "D:/thesis/final.docx"})
    assert assessment.risky is True
    assert assessment.severity == "high"
    assert assessment.needs_confirmation is True
    notification = assessment.notification
    assert notification["delivered"] is True
    assert notification["outcome"] in (Outcome.MENTION_LATER.value, Outcome.SPEAK_NOW.value,
                                       Outcome.INTERRUPT.value, Outcome.SHOW_SILENTLY.value)
    assert "thesis" in notification["title"] or "thesis" in notification["detail"]


def test_golden_13_the_prescan_warns_before_any_step_runs(app):
    """Timeliness: the warning exists while the delete can still be stopped."""
    svc = service(app)
    tasks = [
        {"capability": "files.list", "params": {"path": "D:/thesis"}},
        {"capability": "files.delete", "params": {"path": "D:/thesis/final.docx"}},
    ]
    risks = svc.prescan(tasks)
    assert len(risks) == 1
    assert risks[0]["capability"] == "files.delete"
    assert risks[0]["needs_confirmation"] is True


def test_golden_13_a_second_identical_delete_does_not_nag(app):
    """Non-annoying: the owner is not told the same thing twice in a row."""
    svc = service(app)
    first = svc.warn_risky_action("files.delete", {"path": "D:/a.docx"})
    second = svc.warn_risky_action("files.delete", {"path": "D:/a.docx"})
    assert first.notification["suppressed"] is False
    assert second.notification["suppressed"] is True


def test_golden_13_a_read_only_step_is_not_flagged(app):
    svc = service(app)
    assert svc.risk_for("files.list", {"path": "D:/thesis"}).risky is False
    assert svc.prescan([{"capability": "files.read", "params": {"path": "x"}}]) == []


def test_golden_13_the_turn_loop_surfaces_the_risk(app, monkeypatch):
    """The warning travels with the turn result, so the UI can show it."""
    class RiskyDirector:
        name = "risky"

        def classify(self, text, ctx, context_hint=None):
            return DirectorDecision(tasks=[DirectorTask(
                type=TaskType.FILE_ACTION, capability="files.delete",
                params={"path": str(app.db and "D:/nope.txt")})],
                confidence=0.9, source="needle", raw={})

        def available(self):
            return True

    monkeypatch.setattr(app.orchestrator, "director", RiskyDirector())
    out = app.orchestrator.handle_text("delete D:/nope.txt", app.ctx(person_id="owner"))
    assert "risks" in out, "a destructive step must surface a warning"
    assert out["risks"][0]["capability"] == "files.delete"


def test_the_destructive_set_is_explicit():
    assert "files.delete" in DESTRUCTIVE_CAPABILITIES
    assert PURE_DESTRUCTIVE <= DESTRUCTIVE_CAPABILITIES
    assert "files.read" not in DESTRUCTIVE_CAPABILITIES


# =================================================================== other sources
def test_a_mission_failure_becomes_a_candidate(app):
    notification = service(app).observe_mission_failure("mis_1", "organise downloads",
                                                       "permission denied")
    assert notification.event_class == EventClass.MISSION_FAILED.value
    assert notification.mission_id == "mis_1"


def test_a_device_going_offline_becomes_a_candidate(app):
    notification = service(app).observe_device_offline("phone_main", "no heartbeat")
    assert notification.event_class == EventClass.DEVICE_OFFLINE.value


def test_a_perception_event_becomes_a_candidate(app):
    notification = service(app).observe_perception(
        {"type": "person_entered", "zone_id": "workshop", "confidence": 0.93,
         "source": "camera", "event_id": "perc_1"})
    assert notification.event_class == EventClass.PERCEPTION.value


def test_an_ordinary_perception_event_is_not_shouted(app):
    notification = service(app).observe_perception(
        {"type": "motion_detected", "zone_id": "hallway", "confidence": 0.4})
    assert notification.outcome == Outcome.IGNORE.value


def test_a_stored_preference_is_applied(app):
    svc = service(app)
    svc.set_preference(EventClass.PERCEPTION.value, 0.05)
    notification = svc.observe_perception(
        {"type": "person_entered", "zone_id": "workshop", "confidence": 1.0})
    assert notification.suppressed is True, "a disliked class must be suppressible"


def test_preferences_persist(app):
    service(app).set_preference(EventClass.COST.value, 0.2)
    assert service(app).notifier.policy.preferences[EventClass.COST.value] == 0.2


def test_status_reports_what_was_delivered_and_withheld(app):
    svc = service(app)
    svc.warn_risky_action("files.delete", {"path": "a.txt"})
    svc.consider(Candidate(event_class=EventClass.GENERAL.value, title="noise",
                           urgency=0.01, relevance=0.01, confidence=0.1))
    status = svc.status()
    assert status["delivered"] >= 1
    assert status["suppressed"] >= 1
    assert isinstance(status["outbox"], list)
