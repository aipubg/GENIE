"""Phase 8 — perception.

Exit gate: *golden #12 — person enters workshop → structured event, no 24/7 cloud streaming;
bedroom privacy default OFF.*

Everything here runs the **real** pipeline: a real motion detector doing real arithmetic on real
pixel arrays, a real presence engine, a real fusion service, and a real database. Two things are
necessarily doubles on this machine and are labelled as such:

* the **camera** — there is no camera device, so `FrameSourceCameraProvider` supplies frames. It is
  a source of pixels, not a fake detector.
* the **vision model** — no vision-capable provider is configured, so a stub gateway stands in for
  the remote model. The gateway call path, the "one frame per event" rule and the failure handling
  are all real.
"""
from __future__ import annotations

import time

import pytest

from core.contracts import CallContext
from perception.camera import (IndicatorKind, FrameSourceCameraProvider, NullCameraProvider,
                               select_provider)
from perception.contracts import EventType, MicPolicy, PRIVATE_ZONE_KINDS
from perception.motion import MotionDetector, frame_difference, synthetic_frame
from perception.presence import KIND_WEIGHTS, PresenceEngine
from perception.service import PerceptionService

FRAME_W, FRAME_H = 64, 48
EMPTY = synthetic_frame(FRAME_W, FRAME_H, fill=100)
PERSON = synthetic_frame(FRAME_W, FRAME_H, fill=100,
                         block={"x": 20, "y": 12, "w": 16, "h": 20, "value": 240})


def person_at(x: int) -> list:
    """A frame with a person standing at column `x`."""
    return synthetic_frame(FRAME_W, FRAME_H, fill=100,
                           block={"x": x, "y": 12, "w": 16, "h": 20, "value": 240})


def walking(*positions) -> list:
    """A realistic sequence: a person actually moving.

    Frame-difference detection needs *consecutive changed frames* to confirm motion — which is
    exactly what a real person walking produces and what a single swapped frame does not. The
    earlier version of these tests used one changed frame and therefore tested nothing.
    """
    return [person_at(x) for x in (positions or (20, 24, 28, 32, 36))]


def frames(*specs) -> list:
    return [{"frame": spec, "width": FRAME_W, "height": FRAME_H} for spec in specs]


class StubGateway:
    """Stands in for the remote vision model (no provider is configured on this machine)."""

    def __init__(self, label: str = "person", confidence: float = 0.93, fail: bool = False):
        self.label = label
        self.confidence = confidence
        self.fail = fail
        self.calls = 0
        self.prompts = []

    def complete(self, ctx, requirement, messages, max_tokens=0):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        if self.fail:
            raise RuntimeError("no vision-capable provider is configured")

        class _Completion:
            text = ('{"label": "%s", "confidence": %s}' % (self.label, self.confidence))
            provider_id = "stub-vision"
            model = "stub"

        return _Completion()


def service_with(db, *, gateway=None, camera_frames=None, config=None) -> PerceptionService:
    default_sequence = [EMPTY] + walking(20, 24, 28, 32, 36, 40, 44)
    provider = FrameSourceCameraProvider(frames(*(camera_frames or default_sequence)),
                                         indicator=IndicatorKind.VISIBLE.value)
    return PerceptionService(db, camera_provider=provider, gateway=gateway,
                             config={"cloud_camera_consent": True, **(config or {})})


def workshop_ready(db, **kw) -> PerceptionService:
    """A workshop camera enabled and activated — the golden #12 setup."""
    svc = service_with(db, **kw)
    svc.set_policy("workshop", camera=True, motion=True, owner_enabled=True)
    opened = svc.activate_camera("workshop")
    assert opened["ok"] is True, opened
    return svc


# ======================================================================= privacy
def test_every_builtin_zone_exists(app):
    svc = PerceptionService(app.db)
    ids = {z.zone_id for z in svc.zones()}
    assert {"office", "workshop", "living", "kitchen", "bedroom", "bathroom"} <= ids


def test_private_zones_have_the_camera_off_by_default(app):
    """The spec's headline privacy requirement."""
    svc = PerceptionService(app.db)
    for zone_id in PRIVATE_ZONE_KINDS:
        zone = svc.zone(zone_id)
        assert zone.policy.camera is False, f"{zone_id} must not have a camera on by default"
        assert zone.policy.microphone == MicPolicy.WAKE_ONLY.value
        assert zone.policy.owner_enabled is False
        assert zone.private is True


def test_a_bedroom_camera_cannot_be_activated_without_the_owner_enabling_it(app):
    svc = PerceptionService(app.db)
    result = svc.activate_camera("bedroom")
    assert result["ok"] is False
    assert result["error_code"] == "policy_denied"
    assert "owner must enable" in result["detail"]


def test_the_owner_can_enable_a_bedroom_camera_explicitly(app):
    svc = PerceptionService(app.db)
    svc.set_policy("bedroom", camera=True, owner_enabled=True, retention_s=60)
    assert svc.zone("bedroom").policy.camera is True
    # and with the policy on, the only remaining obstacle is the missing hardware
    result = svc.activate_camera("bedroom")
    assert result["error_code"] in ("camera_unavailable", "indicator_unavailable") or result["ok"]


def test_policies_persist_across_restarts(app):
    svc = PerceptionService(app.db)
    svc.set_policy("bedroom", camera=True, owner_enabled=True, retention_s=45)
    fresh = PerceptionService(app.db)
    policy = fresh.zone("bedroom").policy
    assert policy.camera is True and policy.retention_s == 45


def test_an_unknown_microphone_policy_is_refused(app):
    svc = PerceptionService(app.db)
    result = svc.set_policy("office", microphone="always_listening_forever")
    assert result["ok"] is False


def test_policy_changes_are_audited(app):
    svc = PerceptionService(app.db, audit=app.audit)
    svc.set_policy("workshop", camera=True)
    entries = [e for e in app.audit.tail(50) if e.get("action") == "perception.policy"]
    assert entries


def test_private_zone_ids_cover_bedroom_and_bathroom():
    assert "bedroom" in PRIVATE_ZONE_KINDS and "bathroom" in PRIVATE_ZONE_KINDS


# =================================================================== fail closed
def test_a_camera_without_an_indicator_does_not_activate(app):
    """A silent camera is worse than no camera, so activation fails closed."""
    provider = FrameSourceCameraProvider(frames(EMPTY, PERSON), indicator=IndicatorKind.NONE.value)
    svc = PerceptionService(app.db, camera_provider=provider)
    svc.set_policy("workshop", camera=True, owner_enabled=True)
    result = svc.activate_camera("workshop")
    assert result["ok"] is False
    assert result["error_code"] == "indicator_unavailable"
    assert provider.indicator_failures == 1
    assert svc.camera_status()["active_sessions"] == [], "no session may exist"


def test_the_indicator_is_turned_on_and_off_with_the_session(app):
    provider = FrameSourceCameraProvider(frames(EMPTY, PERSON))
    svc = PerceptionService(app.db, camera_provider=provider)
    svc.set_policy("workshop", camera=True, owner_enabled=True)
    svc.activate_camera("workshop")
    assert provider.indicator_on is True
    svc.close_camera("workshop")
    assert provider.indicator_on is False


def test_no_camera_device_is_reported_honestly(app):
    svc = PerceptionService(app.db, camera_provider=NullCameraProvider())
    svc.set_policy("workshop", camera=True, owner_enabled=True)
    result = svc.activate_camera("workshop")
    assert result["error_code"] == "camera_unavailable"
    assert svc.camera_status()["available"] is False


# ================================================================= motion detection
def test_a_static_scene_produces_no_motion():
    detector = MotionDetector()
    detector.observe(EMPTY, FRAME_W, FRAME_H)
    for _ in range(5):
        assert detector.observe(EMPTY, FRAME_W, FRAME_H).motion is False


def test_a_single_changed_frame_is_not_enough_to_confirm_motion():
    """Hysteresis exists so one noisy frame cannot raise an event."""
    detector = MotionDetector(start_frames=2)
    detector.observe(EMPTY, FRAME_W, FRAME_H)
    assert detector.observe(PERSON, FRAME_W, FRAME_H).motion is False


def test_real_movement_confirms_motion():
    """A person walking produces consecutive changed frames, and that is what confirms motion."""
    detector = MotionDetector(start_frames=2)
    detector.observe(EMPTY, FRAME_W, FRAME_H)
    detector.observe(person_at(20), FRAME_W, FRAME_H)
    assert detector.observe(person_at(24), FRAME_W, FRAME_H).motion is True


def test_motion_stops_after_consecutive_still_frames():
    """Motion ends when the scene stops changing — not when it changes back."""
    detector = MotionDetector(start_frames=2, stop_frames=2)
    detector.observe(EMPTY, FRAME_W, FRAME_H)
    detector.observe(person_at(20), FRAME_W, FRAME_H)
    detector.observe(person_at(24), FRAME_W, FRAME_H)
    assert detector.active is True
    still = person_at(24)
    detector.observe(still, FRAME_W, FRAME_H)
    assert detector.active is True, "one still frame must not stop motion"
    assert detector.observe(still, FRAME_W, FRAME_H).motion is False


def test_the_detector_does_real_arithmetic():
    assert frame_difference([0, 0, 0], [0, 0, 0]) == 0.0
    assert frame_difference([0, 0, 0], [255, 255, 255]) == 1.0
    assert 0.0 < frame_difference(EMPTY, PERSON) < 1.0


def test_an_empty_frame_is_handled():
    assert frame_difference([], []) == 0.0
    assert frame_difference([1], []) == 0.0


# ================================================ golden #12: no 24/7 streaming
def test_golden_person_enters_workshop_produces_a_structured_event(app):
    gateway = StubGateway(label="person", confidence=0.93)
    svc = workshop_ready(app.db, gateway=gateway)

    # a baseline frame, then a person walks in
    assert svc.observe("workshop")["motion"] is False
    svc.observe("workshop")                       # hysteresis
    result = svc.observe("workshop")

    assert result["motion"] is True
    event = result["event"]
    assert event["type"] == EventType.PERSON_ENTERED.value
    assert event["zone_id"] == "workshop"
    assert 0.0 < event["confidence"] <= 1.0
    assert event["ts"] > 0
    assert result["streaming"] is False


def test_golden_no_continuous_streaming_happens(app):
    """The vision model is consulted for an *event*, never continuously."""
    gateway = StubGateway()
    # a still scene first, then a person walking in
    sequence = [EMPTY] * 20 + walking(20, 24, 28, 32, 36, 40, 44, 48)
    svc = workshop_ready(app.db, gateway=gateway, camera_frames=sequence)

    for _ in range(20):
        svc.observe("workshop")
    assert gateway.calls == 0, "a static scene must never reach a vision provider"

    # someone walks in: the model is consulted, but not once per frame
    calls_after_motion = None
    for _ in range(6):
        svc.observe("workshop")
        if calls_after_motion is None and gateway.calls:
            calls_after_motion = gateway.calls
    assert gateway.calls >= 1, "a real event should reach the vision provider"
    assert gateway.calls <= 2, (
        f"motion must not become a stream of model calls (got {gateway.calls})")


def test_a_static_scene_never_calls_the_vision_model(app):
    gateway = StubGateway()
    svc = service_with(app.db, gateway=gateway, camera_frames=[EMPTY] * 30)
    svc.set_policy("workshop", camera=True, owner_enabled=True)
    svc.activate_camera("workshop")
    for _ in range(10):
        outcome = svc.observe("workshop")
        assert outcome["motion"] is False
        assert outcome.get("event") is None
    assert gateway.calls == 0


def test_the_session_is_bounded_in_frames(app):
    gateway = StubGateway()
    svc = workshop_ready(app.db, gateway=gateway,
                         camera_frames=[EMPTY] + [person_at(20 + (i % 20) * 2)
                                                  for i in range(400)])
    from perception.camera import MAX_FRAMES_PER_SESSION
    exhausted = None
    for _ in range(MAX_FRAMES_PER_SESSION + 5):
        outcome = svc.observe("workshop")
        if outcome.get("error_code") == "session_exhausted":
            exhausted = outcome
            break
    assert exhausted is not None, "a camera session must not be able to run unbounded"
    assert svc.camera_status()["active_sessions"] == []


def test_without_a_vision_provider_the_event_stays_honest(app):
    """No model configured → a motion event, not a fabricated `person_entered`."""
    svc = workshop_ready(app.db, gateway=None)
    svc.observe("workshop")
    svc.observe("workshop")
    result = svc.observe("workshop")
    assert result["motion"] is True
    assert result["event"]["type"] == EventType.MOTION_DETECTED.value
    assert "vision_skipped" in result["event"]["evidence"]


def test_a_failing_vision_call_does_not_lose_the_event(app):
    svc = workshop_ready(app.db, gateway=StubGateway(fail=True))
    svc.observe("workshop")
    svc.observe("workshop")
    result = svc.observe("workshop")
    assert result["motion"] is True
    assert result["event"]["type"] == EventType.MOTION_DETECTED.value
    assert result["event"]["evidence"].get("vision_error")


def test_a_non_person_classification_is_reported_as_activity(app):
    svc = workshop_ready(app.db, gateway=StubGateway(label="object", confidence=0.6))
    svc.observe("workshop")
    svc.observe("workshop")
    result = svc.observe("workshop")
    assert result["event"]["type"] == EventType.ACTIVITY_CHANGED.value


def test_a_sensing_disabled_zone_cannot_be_observed(app):
    svc = PerceptionService(app.db, camera_provider=FrameSourceCameraProvider(frames(EMPTY)))
    svc.set_policy("bedroom", camera=False, motion=False, owner_enabled=True)
    result = svc.observe("bedroom")
    assert result["ok"] is False
    assert result["error_code"] == "policy_denied"


def test_observing_without_a_session_is_refused(app):
    svc = service_with(app.db)
    svc.set_policy("workshop", camera=True, owner_enabled=True)
    result = svc.observe("workshop")
    assert result["error_code"] == "no_session"


def test_an_unknown_zone_is_refused(app):
    svc = PerceptionService(app.db)
    assert svc.observe("dungeon")["error_code"] == "unknown_zone"
    assert svc.activate_camera("dungeon")["error_code"] == "unknown_zone"


# ==================================================================== presence
def test_presence_needs_evidence(app):
    engine = PresenceEngine()
    estimate = engine.estimate("workshop")
    assert estimate.present is False
    assert estimate.confidence == 0.0
    assert "no evidence" in estimate.to_dict()["summary"]


def test_presence_rises_with_evidence(app):
    engine = PresenceEngine()
    engine.observe("workshop", "camera", 0.93, "person_entered")
    estimate = engine.estimate("workshop")
    assert estimate.present is True
    assert estimate.confidence > 0.5
    assert "workshop" in estimate.to_dict()["summary"]


def test_independent_signals_combine(app):
    """Noisy-OR: two independent weak signals should beat either alone."""
    camera_only = PresenceEngine()
    camera_only.observe("office", "camera", 0.6)
    combined = PresenceEngine()
    combined.observe("office", "camera", 0.6)
    combined.observe("office", "device", 0.6)
    assert combined.estimate("office").confidence > camera_only.estimate("office").confidence


def test_confidence_never_exceeds_one(app):
    engine = PresenceEngine()
    for _ in range(20):
        engine.observe("office", "camera", 1.0)
    assert engine.estimate("office").confidence <= 1.0


def test_signals_decay_over_time(app):
    engine = PresenceEngine()
    engine.observe("office", "camera", 1.0)
    now = time.time()
    fresh = engine.estimate("office", now).confidence
    later = engine.estimate("office", now + 600).confidence
    assert later < fresh, "an old observation must not count as present now"
    assert engine.estimate("office", now + 10_000).confidence == 0.0


def test_a_stale_signal_no_longer_counts_as_presence(app):
    engine = PresenceEngine()
    engine.observe("office", "camera", 1.0)
    assert engine.estimate("office", time.time() + 3600).present is False


def test_zones_are_independent(app):
    engine = PresenceEngine()
    engine.observe("workshop", "camera", 0.9)
    assert engine.estimate("workshop").present is True
    assert engine.estimate("office").present is False


def test_the_best_guess_picks_the_strongest_zone(app):
    engine = PresenceEngine()
    engine.observe("hallway", "device", 0.3)
    engine.observe("workshop", "camera", 0.95)
    best = engine.best_guess()
    assert best is not None and best.zone_id == "workshop"


def test_camera_evidence_outweighs_a_mouse_moving():
    assert KIND_WEIGHTS["camera"] > KIND_WEIGHTS["device"] > KIND_WEIGHTS["calendar"]


def test_presence_can_be_cleared_for_a_zone(app):
    engine = PresenceEngine()
    engine.observe("bedroom", "camera", 0.9)
    assert engine.clear_zone("bedroom") == 1
    assert engine.estimate("bedroom").present is False


def test_a_perception_event_feeds_presence(app):
    svc = workshop_ready(app.db, gateway=StubGateway())
    svc.observe("workshop")
    svc.observe("workshop")
    svc.observe("workshop")
    assert svc.presence_for("workshop")["present"] is True


# ====================================================================== fusion
def test_fusion_degrades_gracefully(app):
    """Missing sensors must not stop fusion — and must be reported."""
    from perception.fusion import CameraSensor
    svc = PerceptionService(app.db, camera_provider=NullCameraProvider())
    svc.fusion.add_sensor(_screen_sensor(app))
    svc.fusion.add_sensor(CameraSensor(perception=svc))
    state = svc.environment("workshop")
    assert state["degraded"] is True
    assert "camera" in state["unavailable"], "a missing sensor must be named"
    assert "summary" in state


def test_fusion_produces_a_usable_state_with_no_sensors(app):
    svc = PerceptionService(app.db)
    state = svc.environment("workshop")
    assert state["zone_id"] == "workshop"
    assert isinstance(state["presence"], bool)
    assert state["ts"] > 0


def test_fusion_reports_available_sensors(app):
    svc = PerceptionService(app.db, computer=app.computer)
    svc.fusion.add_sensor(_screen_sensor(app))
    state = svc.environment("workshop")
    screen = [s for s in state["signals"] if s["name"] == "screen"]
    assert screen and screen[0]["available"] is True


def test_fusion_includes_the_time_signal(app):
    from perception.fusion import CalendarSensor
    svc = PerceptionService(app.db)
    svc.fusion.add_sensor(CalendarSensor())
    state = svc.environment("office")
    time_signal = [s for s in state["signals"] if s["kind"] == "time"]
    assert time_signal and time_signal[0]["available"] is True


def test_fusion_survives_a_broken_sensor(app):
    class Exploding:
        name = "exploding"

        def sample(self, ctx=None):
            raise RuntimeError("sensor on fire")

    svc = PerceptionService(app.db)
    svc.fusion.add_sensor(Exploding())
    state = svc.environment("office")
    assert "exploding" in state["unavailable"]
    assert state["degraded"] is True


def _screen_sensor(app):
    from perception.fusion import ScreenSensor
    return ScreenSensor(computer=app.computer)


# ===================================================== retention, purge, status
def test_events_carry_confidence_zone_and_time(app):
    svc = workshop_ready(app.db, gateway=StubGateway())
    svc.observe("workshop")
    svc.observe("workshop")
    svc.observe("workshop")
    events = svc.recent_events()
    assert events
    event = events[0]
    assert {"event_id", "type", "zone_id", "confidence", "source", "ts"} <= set(event)


def test_retention_drops_old_events(app):
    svc = PerceptionService(app.db)
    svc.set_policy("office", retention_s=1, motion=True)
    svc._emit("office", EventType.MOTION_DETECTED.value, "camera", 0.6, {})
    assert len(svc.recent_events()) == 1
    # age the event beyond its retention window
    svc._events[0].ts = int((time.time() - 10) * 1000)
    assert svc.recent_events() == []


def test_purge_removes_derived_data(app):
    svc = PerceptionService(app.db)
    svc._emit("office", EventType.MOTION_DETECTED.value, "camera", 0.6, {})
    svc._emit("workshop", EventType.MOTION_DETECTED.value, "camera", 0.6, {})
    removed = svc.purge("office")
    assert removed["removed"] == 1
    assert [e["zone_id"] for e in svc.recent_events()] == ["workshop"]
    assert svc.purge()["removed"] == 1


def test_purge_also_clears_presence(app):
    svc = PerceptionService(app.db)
    svc.presence.observe("bedroom", "camera", 0.9)
    svc.purge("bedroom")
    assert svc.presence_for("bedroom")["present"] is False


def test_status_never_reports_streaming(app):
    svc = workshop_ready(app.db, gateway=StubGateway())
    status = svc.status()
    assert status["streaming"] is False
    assert "bedroom" in status["private_zones"]


def test_the_event_bus_sees_perception_events(app):
    from core.events import get_bus
    seen = []
    get_bus().subscribe("PERCEPTION_EVENT", lambda e: seen.append(e))
    svc = PerceptionService(app.db)
    svc._emit("office", EventType.MOTION_DETECTED.value, "camera", 0.6, {})
    deadline = time.time() + 3
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    assert seen and seen[0].payload["zone_id"] == "office"


# ======================================================= context integration
def test_the_environment_reaches_the_context(app):
    """§6.11: the fused state is consumed by the Context Engine."""
    svc = PerceptionService(app.db)
    app.orchestrator.perception = svc
    hint = app.orchestrator._context_hint(CallContext(person_id="owner"))
    assert "environment" in hint
    assert "summary" in hint["environment"]


def test_a_perception_failure_does_not_break_a_turn(app):
    class Broken:
        def environment(self, zone_id=""):
            raise RuntimeError("perception down")

    app.orchestrator.perception = Broken()
    hint = app.orchestrator._context_hint(CallContext(person_id="owner"))
    assert hint["person"] == "owner", "a broken sensor must never break a turn"


def test_the_select_helper_never_picks_the_test_backend():
    assert select_provider().backend != "frame-source"
    assert select_provider({"backend": "frame-source"}).backend == "frame-source"
