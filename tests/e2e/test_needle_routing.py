"""Real NEDLE2 (Cactus Needle 2) routing tests.

These run against the ACTUAL engine — no mocks. If the runtime is not provisioned the
tests skip (they never fake a pass). Provision with:

    python genie.py needle-setup
"""
from __future__ import annotations

import pytest

from core.contracts import CallContext
from director.needle_runtime import get_runtime

runtime = get_runtime()
_RUNTIME_READY = runtime.package_installed() and runtime.verify().get("ok", False)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_READY,
    reason="Needle 2 runtime not provisioned — run: python genie.py needle-setup")


@pytest.fixture(scope="module")
def director():
    from director.nedle2 import NeedleDirector
    d = NeedleDirector()
    if not d.available():
        pytest.skip("needle director unavailable")
    yield d
    d.close()


def route(director, text: str):
    return director.classify(text, CallContext())


# --------------------------------------------------------------- runtime health
def test_runtime_is_verified_and_versioned():
    status = runtime.status()
    assert status["package_installed"] is True
    assert status["library_present"] is True
    assert status["verified"] is True
    assert status["sha256"]
    assert status["engine_version"]


def test_director_reports_real_engine_not_fallback(director):
    status = director.status()
    assert status["engine"] == "needle"
    assert status["fallback_active"] is False
    assert status["runtime"] in ("python", "http", "cli")
    assert status["tool_count"] >= 10


# ------------------------------------------------------------ required routing
def test_volume_english(director):
    d = route(director, "volume 30")
    caps = {t.capability for t in d.tasks}
    assert "system.volume.set" in caps
    assert d.tasks[0].params.get("level") == 30
    assert d.raw["reason"] == "accepted"


def test_volume_hinglish(director):
    d = route(director, "awaz 30")
    assert "system.volume.set" in {t.capability for t in d.tasks}


def test_open_application(director):
    d = route(director, "Chrome kholo")
    assert "application.open" in {t.capability for t in d.tasks}
    assert any(t.target == "chrome" for t in d.tasks)


def test_device_media_next_with_target(director):
    d = route(director, "phone ka next song")
    caps = {t.capability for t in d.tasks}
    assert "media.next" in caps
    assert any(t.device == "phone_main" for t in d.tasks)


def test_project_request_is_not_a_device_action(director):
    """'mera latest project continue karo' must never become a device/media action."""
    d = route(director, "mera latest project continue karo")
    routed = {t.capability for t in d.tasks}
    assert not routed & {"media.next", "media.play", "media.pause", "application.open"}
    # acceptable outcomes: memory retrieval, or escalation to a remote model
    assert d.memory_query or d.reasoning_required


# ------------------------------------------------------------------- refusals
def test_malformed_input_is_not_routed(director):
    d = route(director, "asdkjhasd qwerty ???")
    assert d.tasks == []


def test_ambiguous_input_is_not_routed(director):
    d = route(director, "kuch karo")
    assert d.tasks == []


def test_unsupported_destructive_request_is_not_routed(director):
    d = route(director, "delete all my files right now")
    assert d.tasks == []


def test_negated_request_is_not_routed(director):
    d = route(director, "don't open chrome")
    assert d.tasks == []


def test_below_threshold_escalates_instead_of_guessing(director):
    d = route(director, "mera latest project continue karo")
    if d.confidence < director.confidence_threshold:
        assert d.tasks == []
        assert d.reasoning_required is True
        assert d.raw.get("escalated") is True


def test_owner_local_commands_never_need_a_remote_model(director):
    """Phase 2 requirement: these must route locally (no cloud LLM)."""
    expectations = {
        "volume 30": "system.volume.set",
        "awaz 30": "system.volume.set",
        "mute": "system.volume.mute",
        "Chrome kholo": "application.open",
        "Notepad kholo": "application.open",
        "Spotify pause": "media.pause",
        "phone ka next song": "media.next",
    }
    for query, capability in expectations.items():
        d = route(director, query)
        caps = {t.capability for t in d.tasks}
        assert capability in caps, f"{query!r} -> {caps} (expected {capability})"
        assert d.reasoning_required is False, f"{query!r} escalated to a remote model"


def test_web_address_uses_the_deterministic_fast_path(director):
    d = route(director, "open youtube.com/@JoshuaBardwell")
    assert "browser.navigate" in {t.capability for t in d.tasks}
    assert d.raw.get("reason") == "fast-path:web-address"
    assert d.tasks[0].params.get("url") == "youtube.com/@JoshuaBardwell"


def test_hinglish_verb_is_normalised_before_routing(director):
    d = route(director, "notepad kholo")
    assert "application.open" in {t.capability for t in d.tasks}
    assert "open notepad" in (d.raw.get("model_input") or "")


def test_needle_never_claims_completion(director):
    """NEDLE2 is a router: it must not report task results or completion."""
    for text in ("volume 30", "Chrome kholo", "phone ka next song"):
        d = route(director, text)
        assert "completed" not in str(d.raw).lower()
        assert "success" not in str(d.reasoning if hasattr(d, "reasoning") else "").lower()


# ------------------------------------------------------- large tool catalogue
def test_large_tool_catalogue_still_routes(director):
    result = director.large_catalogue_probe(filler=60)
    assert result["tool_count"] >= 70
    assert result["ok"] is True


# ------------------------------------------------------------ smoke test suite
def test_full_smoke_suite_passes(director):
    result = director.smoke_test()
    assert result["total"] >= 12
    assert result["ok"] is True, result
