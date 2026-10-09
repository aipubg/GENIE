"""Isolation model and declared sensor sources (roadmap §19-§20).

The dangerous failure here is overclaiming: saying "sandboxed" when GENIE is
actually on the owner's desktop, or implying a workspace directory is a jail.
These tests pin the honest behaviour — `unknown` where nothing can be
established, and an explicit list of what each level does NOT confine.
"""
from __future__ import annotations

import json

import pytest

from core import isolation
from core.isolation import IsolationKind


def detected(value: bool, why=("evidence",)):
    return isolation._evidence(value, list(why))


# -------------------------------------------------------------- desktop (§19)

def test_an_isolated_desktop_is_unknown_until_declared():
    out = isolation.detect_isolated_desktop()
    assert out["detected"] is None
    assert out["state"] == "unknown"
    assert "cannot be detected" in " ".join(out["evidence"])


def test_a_declared_isolated_desktop_is_reported_as_such():
    isolation.declare_isolated_desktop(True)
    try:
        out = isolation.detect_isolated_desktop()
        assert out["detected"] is True
        assert out["state"] == "detected"
    finally:
        isolation.declare_isolated_desktop(False)


def test_declaring_no_isolated_desktop_is_not_the_same_as_unknown():
    isolation.declare_isolated_desktop(False)
    try:
        out = isolation.detect_isolated_desktop()
        assert out["detected"] is False
        assert out["state"] == "not_detected"
    finally:
        isolation.declare_isolated_desktop(False)


# ------------------------------------------------------------------- probing

def test_container_and_vm_probes_never_raise_and_always_carry_evidence():
    for probe in (isolation.detect_container, isolation.detect_vm):
        out = probe()                      # real machine, whatever it is
        assert set(out) >= {"detected", "evidence", "state"}
        assert isinstance(out["evidence"], list)
        assert out["state"] in ("detected", "not_detected", "unknown")


def test_a_negative_probe_is_not_detected_rather_than_false_confidence():
    # "not detected" must never be dressed up as proof of bare metal.
    out = isolation._evidence(False, [])
    assert out["state"] == "not_detected"
    assert out["detected"] is False


# -------------------------------------------------------------------- scopes

def test_workspace_scope_without_a_root_is_unknown():
    out = isolation.workspace_scope(None)
    assert out["state"] == "unknown"
    assert out["kind"] == IsolationKind.WORKSPACE_DIR.value


def test_workspace_scope_states_what_it_does_not_confine(tmp_path):
    out = isolation.workspace_scope(str(tmp_path))
    assert out["state"] == "active"
    # The point of the module: a workspace directory is a convention, not a jail.
    assert "network access" in out["does_not_confine"]
    assert "process execution" in out["does_not_confine"]
    assert "the owner's desktop session" in out["does_not_confine"]


def test_workspace_scope_survives_a_broken_root():
    out = isolation.workspace_scope("")     # falsy -> unknown, never an exception
    assert out["state"] == "unknown"


def test_browser_profile_scope_without_a_directory_is_unknown():
    out = isolation.browser_scope(None)
    assert out["state"] == "unknown"
    assert out["kind"] == IsolationKind.BROWSER_PROFILE.value


def test_browser_profile_confines_state_but_not_the_network(tmp_path):
    out = isolation.browser_scope(str(tmp_path))
    assert out["state"] == "active"
    assert "cookies" in out["confines"]
    # A separate profile is not a separate identity on the wire.
    assert "network egress" in out["does_not_confine"]
    assert "the IP the site sees" in out["does_not_confine"]


def test_a_configured_but_missing_profile_says_so(tmp_path):
    out = isolation.browser_scope(str(tmp_path / "absent"))
    assert out["state"] == "configured_but_absent"
    assert "absent" in out["evidence"][0]


# ------------------------------------------------------------------- sensors

def test_sensors_list_devices_but_declare_none():
    sensors = isolation.sensor_sources()
    for group in ("cameras", "microphones"):
        assert sensors[group]["declared"] is None
        assert isinstance(sensors[group]["devices"], list)
    # Enumerating devices is not the same as saying which one is in use.
    assert "none is" in sensors["cameras"]["note"]
    assert "none is listening" in sensors["microphones"]["note"]


# -------------------------------------------------------------------- report

def test_report_defaults_to_host_when_nothing_is_confined(monkeypatch):
    monkeypatch.setattr(isolation, "detect_container", lambda: detected(False))
    monkeypatch.setattr(isolation, "detect_vm", lambda: detected(False))
    isolation.declare_isolated_desktop(False)
    out = isolation.report()
    assert out["effective"] == IsolationKind.HOST.value
    assert out["caveats"]                      # the caveats are part of the answer


def test_a_detected_vm_outranks_a_workspace_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation, "detect_container", lambda: detected(False))
    monkeypatch.setattr(isolation, "detect_vm", lambda: detected(True, ["vbox"]))
    isolation.declare_isolated_desktop(False)
    out = isolation.report(workspace_root=str(tmp_path),
                           browser_profile=str(tmp_path))
    assert out["effective"] == IsolationKind.VM.value


def test_a_declared_isolated_desktop_outranks_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation, "detect_container", lambda: detected(True, ["docker"]))
    monkeypatch.setattr(isolation, "detect_vm", lambda: detected(True, ["qemu"]))
    isolation.declare_isolated_desktop(True)
    try:
        out = isolation.report(workspace_root=str(tmp_path))
        assert out["effective"] == IsolationKind.ISOLATED_DESKTOP.value
    finally:
        isolation.declare_isolated_desktop(False)


def test_an_active_workspace_outranks_the_bare_host(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation, "detect_container", lambda: detected(False))
    monkeypatch.setattr(isolation, "detect_vm", lambda: detected(False))
    isolation.declare_isolated_desktop(False)
    out = isolation.report(workspace_root=str(tmp_path))
    assert out["effective"] == IsolationKind.WORKSPACE_DIR.value


def test_report_is_serialisable(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation, "detect_container", lambda: detected(False))
    monkeypatch.setattr(isolation, "detect_vm", lambda: detected(False))
    payload = isolation.report(workspace_root=str(tmp_path))
    round_trip = json.loads(json.dumps(payload))
    assert round_trip["effective"] == payload["effective"]
    assert set(round_trip["levels"]) == {"workspace", "browser_profile", "container",
                                         "vm", "isolated_desktop"}
