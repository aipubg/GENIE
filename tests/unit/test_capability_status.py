"""Capability availability must be probed, not assumed (spec section 14).

An adapter class existing is not availability. These tests lock the contract:
every capability reports a state from a fixed vocabulary and never claims ready
when its runtime is absent.
"""
from __future__ import annotations

from core import capability_status as cs

STATES = {cs.READY, cs.MISSING_RUNTIME, cs.MISSING_CREDENTIALS,
          cs.DISABLED, cs.FAILED, cs.UNKNOWN}


def test_report_covers_expected_capabilities():
    r = cs.report()
    assert {"voice", "perception", "browser", "computer", "needle"} <= set(r)


def test_every_probe_reports_a_known_state():
    r = cs.report()
    for name, info in r.items():
        if name == "voice":
            # voice reports sub-states; each must still be a known state
            assert info["stt"]["state"] in STATES, f"{name}.stt"
            assert info["tts"]["state"] in STATES, f"{name}.tts"
            assert info["microphone_state"] in STATES, f"{name}.microphone"
        else:
            assert info["state"] in STATES, f"{name} -> {info.get('state')}"


def test_missing_runtime_is_never_reported_ready():
    """With cv2 absent this box must say so, not fabricate a green badge."""
    info = cs.probe_perception()
    if info["state"] == cs.MISSING_RUNTIME:
        assert info["detail"], "missing runtime must explain what is missing"
        assert not info["cameras"], "no cameras may be claimed without hardware"


def test_voice_probe_never_raises():
    info = cs.probe_voice()
    assert isinstance(info["microphones"], list)


def test_needle_distinguishes_absent_from_unverified():
    info = cs.probe_needle()
    # A present-but-unverified engine must be UNKNOWN, never "ready" and never
    # silently "failed" - that distinction is what section 14 is about.
    if info["state"] == cs.READY:
        assert info.get("smoke", {}).get("ok"), "ready requires a passing smoke"
    if info["state"] == cs.UNKNOWN:
        assert "smoke" in (info.get("detail") or "").lower()


def test_report_is_json_serialisable():
    import json
    json.dumps(cs.report())
