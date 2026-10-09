"""Semantic routing guard — regression tests for the NEDLE2 mis-route.

The router answered "mera latest project continue karo" with `media.play` at ~0.99 confidence.
High confidence must not override an incompatible intent, so routing is validated after the
model against the semantics of the request.

Required cases (owner-specified):
  * "mera latest project continue karo"   -> must NOT become media
  * "kal wala project resume karo"        -> must NOT become media
  * "continue the previous mission"       -> must NOT become media
  * "Spotify ka song continue karo"       -> media allowed
  * "video resume karo"                   -> media allowed
"""
from __future__ import annotations

import time

import pytest

from core.contracts import CallContext, Persona, TaskType
from director.base import DirectorDecision, DirectorTask
from director.semantic_guard import (GuardVerdict, guard_decision, has_continuation_semantics,
                                     has_media_evidence, is_media_capability)

MUST_NOT_BE_MEDIA = [
    "mera latest project continue karo",
    "kal wala project resume karo",
    "continue the previous mission",
]

MAY_BE_MEDIA = [
    "Spotify ka song continue karo",
    "video resume karo",
    "phone ka next song",
    "gaana bajao",
    "play some music",
    "pause the video",
]


def media_decision(capability: str = "media.play", device: str = "pc_main",
                   confidence: float = 0.99) -> DirectorDecision:
    """A decision shaped exactly like the one NEDLE2 produced for the bad case."""
    return DirectorDecision(
        tasks=[DirectorTask(type=TaskType.COMPUTER_ACTION, device=device,
                            capability=capability)],
        confidence=confidence, source="needle",
        raw={"engine": "needle", "reason": "accepted"})


# ------------------------------------------------------------------ capability family
def test_media_capability_detection():
    assert is_media_capability("media.play")
    assert is_media_capability("media.next")
    assert is_media_capability("plugin.media.pause")
    assert is_media_capability("plugin.spotify.play")
    assert is_media_capability("phone.media.next")
    assert not is_media_capability("system.volume.set")
    assert not is_media_capability("files.write")
    assert not is_media_capability("")


# ------------------------------------------------------------------ semantics detection
@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA + [
    "resume task",
    "latest project",
    "previous mission",
    "wahi se continue karo",
    "kal wala kaam",
    "continue from where I left off",
    "pichhla project aage badhao",
    "mera kaam continue karo",
])
def test_continuation_semantics_detected(text):
    assert has_continuation_semantics(text) is True, text


@pytest.mark.parametrize("text", MAY_BE_MEDIA + [
    "next song",
    "spotify kholo",
    "youtube par video chalao",
])
def test_continuation_semantics_not_detected_for_media_requests(text):
    assert has_continuation_semantics(text) is False, text


@pytest.mark.parametrize("text", MAY_BE_MEDIA)
def test_media_evidence_detected(text):
    assert has_media_evidence(text) is True, text


@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA)
def test_no_media_evidence_in_continuation_requests(text):
    assert has_media_evidence(text) is False, text


# ------------------------------------------------------------------ the guard itself
@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA)
def test_continuation_never_becomes_a_media_action(text):
    decision = media_decision()
    verdict = guard_decision(text, decision)
    assert verdict.allowed is False
    assert verdict.action == "reroute"
    assert verdict.blocked_capabilities == ["media.play"]
    assert decision.tasks == [], "the media task must be withdrawn"


@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA)
def test_high_confidence_does_not_override_semantics(text):
    """The whole point: 0.99 confidence is not evidence."""
    decision = media_decision(confidence=0.99)
    guard_decision(text, decision)
    assert decision.tasks == []
    assert decision.raw["semantic_guard"]["original_confidence"] == 0.99


@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA)
def test_blocked_route_escalates_instead_of_failing(text):
    decision = media_decision()
    guard_decision(text, decision)
    assert decision.reasoning_required is True
    assert decision.provider_category == "reasoning"
    assert decision.memory_query, "memory/mission retrieval must be attempted"


def test_blocked_route_is_recorded_for_audit():
    decision = media_decision()
    guard_decision("mera latest project continue karo", decision)
    guard = decision.raw["semantic_guard"]
    assert guard["allowed"] is False
    assert "continuation semantics without media grounding" in guard["reason"]
    assert decision.raw["guard_blocked_tasks"] == [
        {"capability": "media.play", "device": "pc_main"}]


@pytest.mark.parametrize("text", MAY_BE_MEDIA)
def test_grounded_media_requests_are_untouched(text):
    decision = media_decision("media.next", device="phone_main")
    verdict = guard_decision(text, decision)
    assert verdict.allowed is True
    assert len(decision.tasks) == 1
    assert decision.tasks[0].capability == "media.next"
    assert "semantic_guard" not in decision.raw


def test_context_media_session_is_valid_grounding():
    """A live media session is real grounding, so the same words may act on media."""
    decision = media_decision("media.pause")
    verdict = guard_decision("continue karo", decision,
                             context={"media_session": {"title": "some track"}})
    assert verdict.allowed is True
    assert len(decision.tasks) == 1


def test_without_media_session_a_continuation_request_is_refused():
    decision = media_decision("media.pause")
    verdict = guard_decision("mera kaam continue karo", decision)
    assert verdict.allowed is False
    assert decision.tasks == []


def test_a_bare_continue_is_deliberately_not_continuation():
    """ "continue karo" names no work, so it is ambiguous rather than a continuation request.

    Treating it as continuation would block legitimate playback control ("continue karo"
    while a track is paused), so the guard requires a work noun or an explicit phrase.
    """
    assert has_continuation_semantics("continue karo") is False
    decision = media_decision("media.pause")
    assert guard_decision("continue karo", decision).allowed is True


def test_non_media_tasks_are_never_touched_by_the_guard():
    decision = DirectorDecision(tasks=[
        DirectorTask(type=TaskType.FILE_ACTION, capability="files.write",
                     params={"path": "x.txt"}),
        DirectorTask(type=TaskType.COMPUTER_ACTION, capability="media.play"),
    ], confidence=0.9, source="needle", raw={})
    guard_decision("mera latest project continue karo", decision)
    assert [t.capability for t in decision.tasks] == ["files.write"]


def test_a_decision_without_media_tasks_is_untouched():
    decision = DirectorDecision(tasks=[
        DirectorTask(type=TaskType.FILE_ACTION, capability="files.write")],
        confidence=0.9, source="needle", raw={})
    verdict = guard_decision("mera latest project continue karo", decision)
    assert verdict.allowed is True
    assert "semantic_guard" not in decision.raw


def test_guard_is_safe_on_empty_input_and_missing_tasks():
    assert guard_decision("", media_decision()).allowed is True
    empty = DirectorDecision(tasks=[], confidence=0.5, source="needle", raw={})
    assert guard_decision("continue project", empty).allowed is True


def test_guard_is_idempotent():
    decision = media_decision()
    first = guard_decision("mera latest project continue karo", decision)
    second = guard_decision("mera latest project continue karo", decision)
    assert first.allowed is False
    assert second.allowed is True, "nothing left to block on a second pass"
    assert decision.tasks == []


def test_verdict_serialises_for_the_ui():
    verdict = guard_decision("kal wala project resume karo", media_decision())
    payload = verdict.to_dict()
    assert payload["action"] == "reroute"
    assert payload["continuation"] is True
    assert payload["media_evidence"] is False


# --------------------------------------------------- wired into the real turn loop
def _needle_like_director(capability: str = "media.play", confidence: float = 0.99):
    """Stand in for NEDLE2: always confident, sometimes semantically wrong."""
    class StubDirector:
        name = "needle-stub"

        def classify(self, text, ctx, context_hint=None):
            return media_decision(capability, confidence=confidence)

        def available(self):
            return True

    return StubDirector()


@pytest.mark.parametrize("text", MUST_NOT_BE_MEDIA)
def test_turn_loop_refuses_the_media_route(app, text, monkeypatch):
    monkeypatch.setattr(app.orchestrator, "director", _needle_like_director())
    out = app.orchestrator.handle_text(text, app.ctx(person_id="owner"))
    decision = out["decision"]
    assert decision["tasks"] == [], "no media action may reach execution"
    assert decision["reasoning_required"] is True, "must escalate instead of acting"
    assert decision["semantic_guard"]["allowed"] is False


def test_turn_loop_allows_a_grounded_media_route(app, monkeypatch):
    monkeypatch.setattr(app.orchestrator, "director",
                        _needle_like_director("media.next"))
    out = app.orchestrator.handle_text("phone ka next song", app.ctx(person_id="owner"))
    assert out["decision"]["tasks"], "a grounded media request must still route"


def test_turn_loop_records_the_refusal_in_the_audit_log(app, monkeypatch):
    monkeypatch.setattr(app.orchestrator, "director", _needle_like_director())
    app.orchestrator.handle_text("mera latest project continue karo",
                                 app.ctx(person_id="owner"))
    entries = [e for e in app.audit.tail(50)
               if e.get("action") == "route.semantic_guard"]
    assert entries, "a refused route must be audited"
    assert entries[0]["result"] == "blocked"


def test_turn_loop_publishes_a_blocked_event(app, monkeypatch):
    from core.events import get_bus
    seen = []
    get_bus().subscribe("POLICY_VIOLATION_BLOCKED", lambda e: seen.append(e))
    monkeypatch.setattr(app.orchestrator, "director", _needle_like_director())
    app.orchestrator.handle_text("continue the previous mission",
                                 app.ctx(person_id="owner"))
    # the bus is asynchronous: wait for delivery instead of assuming it already happened
    deadline = time.time() + 5
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    assert seen, "the refusal must be observable on the event bus"
    assert seen[0].payload.get("kind") == "semantic_route_guard"
