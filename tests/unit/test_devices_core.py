"""Phase 6 — device contracts + registry.

Deterministic: no sockets, no real nodes. The transport and mesh routing are exercised by
`tests/e2e/test_devices_phase6.py` against a real node over real TCP.
"""
from __future__ import annotations

import json
import time

import pytest

from devices.contracts import (CommandEnvelope, CommandStatus, DeviceManifest, DeviceState,
                               DeviceType, ReplayWindow, ResultEnvelope, TrustTier,
                               canonical_capability, decode_frame, device_scope, encode_frame,
                               sign_payload, verify_signature)
from devices.protocol import derive_pairing_secret, generate_pairing_code
from devices.registry import DeviceRegistry


# ------------------------------------------------------------------ contracts
def test_manifest_normalises_capabilities():
    manifest = DeviceManifest(device_id="phone_main", name="Phone",
                              capabilities=["Media.Next", "media.next", " SCREEN.OBSERVE "])
    manifest.normalised()
    assert manifest.capabilities == ["media.next", "screen.observe"]


def test_manifest_supports_checks_capability():
    manifest = DeviceManifest(device_id="p", capabilities=["media.next"]).normalised()
    assert manifest.supports("media.next") is True
    assert manifest.supports("media.pause") is False


def test_manifest_roundtrip():
    manifest = DeviceManifest(device_id="phone_main", name="Phone", type=DeviceType.ANDROID.value,
                              trust_tier=TrustTier.OWNER_SECONDARY.value,
                              capabilities=["media.next"], platform="android").normalised()
    assert DeviceManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()


def test_manifest_fingerprint_changes_with_capabilities():
    a = DeviceManifest(device_id="p", capabilities=["media.next"]).normalised()
    b = DeviceManifest(device_id="p", capabilities=["media.next", "media.pause"]).normalised()
    assert a.fingerprint() != b.fingerprint()


def test_capability_canonicalisation():
    assert canonical_capability("  Media.Next ") == "media.next"
    assert canonical_capability("") == ""


def test_device_scope_names_the_capability_family():
    assert device_scope("phone_main", "media.next") == "device:phone_main:media"
    assert device_scope("phone_main", "screen.observe") == "device:phone_main:screen"


# ------------------------------------------------------------------ signing
def test_signature_verifies_only_with_the_right_secret():
    payload = {"kind": "command", "command_id": "c1", "capability": "media.next"}
    signature = sign_payload(payload, "secret-a")
    assert verify_signature(payload, "secret-a", signature) is True
    assert verify_signature(payload, "secret-b", signature) is False


def test_tampered_payload_fails_verification():
    payload = {"kind": "command", "capability": "media.next"}
    signature = sign_payload(payload, "s")
    payload["capability"] = "files.delete"
    assert verify_signature(payload, "s", signature) is False


def test_frame_roundtrip_and_forgery_rejection():
    payload = {"kind": "hello", "device_id": "phone_main"}
    frame = encode_frame(payload, "s")
    assert decode_frame(frame, "s") == payload
    with pytest.raises(ValueError):
        decode_frame(frame, "wrong-secret")


def test_malformed_frame_is_rejected():
    with pytest.raises(ValueError):
        decode_frame(b"not json\n", "s")
    with pytest.raises(ValueError):
        decode_frame(b'{"payload": {}}\n', "s")


# ------------------------------------------------------------------ pairing secrets
def test_pairing_secret_is_bound_to_the_device():
    code = "123456"
    assert derive_pairing_secret(code, "phone_main") == \
        derive_pairing_secret(code, "phone_main")
    assert derive_pairing_secret(code, "phone_main") != \
        derive_pairing_secret(code, "laptop_main")
    assert derive_pairing_secret("000000", "phone_main") != \
        derive_pairing_secret(code, "phone_main")


def test_pairing_codes_are_six_digits():
    code = generate_pairing_code()
    assert len(code) == 6 and code.isdigit()


# ------------------------------------------------------------------ conformance
def test_protocol_conformance_vector():
    """Pin the exact canonical JSON + HMAC so any other implementation must match byte for byte.

    The Android node (`android/genie-node/.../GenieProtocol.kt`) re-implements this in Kotlin.
    If its canonicalisation or HMAC ever drifts, the handshake fails loudly instead of silently
    mis-authenticating, and this vector is what a build is checked against.
    """
    payload = {"kind": "command", "command_id": "cmd_fixed", "device_id": "phone_main",
               "capability": "media.next", "counter": 7, "cancel": False}
    canonical = ('{"cancel":false,"capability":"media.next","command_id":"cmd_fixed",'
                 '"counter":7,"device_id":"phone_main","kind":"command"}')
    assert json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) == canonical
    assert sign_payload(payload, "conformance-secret") == \
        "956522c8b8920aa8581e5da25b8e5c985cee55b6dc728aa4e56bef49deb59283"
    # and the PBKDF2 pairing secret is pinned too
    assert derive_pairing_secret("123456", "phone_main") == \
        "a18b3ded0c6ecad1195eb34dd0bb99fe4ec2e61c271082313a509a6de7baef6c"


# ------------------------------------------------------------------ replay window
def test_replay_window_rejects_a_repeated_or_older_counter():
    window = ReplayWindow()
    assert window.accept_counter(5) is True
    assert window.accept_counter(5) is False, "the same counter is a replay"
    assert window.accept_counter(4) is False, "an older counter is a replay"
    assert window.accept_counter(6) is True


def test_replay_window_dedupes_commands_and_is_bounded():
    window = ReplayWindow(window=3)
    for index in range(5):
        window.remember(f"cmd{index}", {"n": index})
    assert window.seen("cmd4") is not None
    assert window.seen("cmd0") is None, "the dedupe cache must stay bounded"


# ------------------------------------------------------------------ envelopes
def test_command_envelope_expires_after_its_ttl():
    command = CommandEnvelope(ttl_ms=1000)
    assert command.expired() is False
    assert command.expired(now_ms=command.issued_at_ms + 1001) is True


def test_command_and_result_roundtrip():
    command = CommandEnvelope(device_id="phone_main", capability="media.next",
                              params={"device": "phone"}, counter=3)
    assert CommandEnvelope.from_dict(command.to_dict()).to_dict() == command.to_dict()
    result = ResultEnvelope(command_id="c1", device_id="phone_main",
                            status=CommandStatus.COMPLETED.value, ok=True, verified=True)
    assert ResultEnvelope.from_dict(result.to_dict()).to_dict() == result.to_dict()


def test_result_terminal_statuses():
    assert ResultEnvelope(status=CommandStatus.COMPLETED.value).terminal() is True
    assert ResultEnvelope(status=CommandStatus.ACCEPTED.value).terminal() is False


# ------------------------------------------------------------------ registry
def test_local_machine_is_registered_and_trusted(app):
    record = app.devices.registry.get("pc_main")
    assert record is not None
    assert record.manifest.trust_tier == TrustTier.OWNER_PRIMARY.value
    assert record.trusted() is True
    assert record.manifest.supports("files.write")


def test_upsert_registers_a_new_device(app):
    manifest = DeviceManifest(device_id="phone_main", name="Pixel",
                              type=DeviceType.ANDROID.value, capabilities=["media.next"])
    record = app.devices.registry.upsert(manifest, state=DeviceState.PAIRED.value,
                                         paired_by="owner")
    assert record.device_id == "phone_main"
    assert app.devices.registry.get("phone_main").manifest.name == "Pixel"


def test_untrusted_device_is_not_trusted(app):
    manifest = DeviceManifest(device_id="guest_phone", capabilities=["media.next"])
    app.devices.registry.upsert(manifest, state=DeviceState.PAIRED.value)
    assert app.devices.registry.get("guest_phone").trusted() is False


def test_trust_tier_can_be_raised_by_the_owner(app):
    app.devices.registry.upsert(DeviceManifest(device_id="guest_phone",
                                               capabilities=["media.next"]))
    assert app.devices.registry.set_trust("guest_phone",
                                          TrustTier.OWNER_SECONDARY.value)["ok"] is True
    assert app.devices.registry.get("guest_phone").trusted() is True


def test_unknown_trust_tier_is_refused(app):
    app.devices.registry.upsert(DeviceManifest(device_id="x_phone"))
    assert app.devices.registry.set_trust("x_phone", "superuser")["ok"] is False


def test_a_device_cannot_promote_itself_by_reconnecting(app):
    """A node's own manifest must never decide its trust tier.

    `hello` carries whatever the node believes; a manifest defaults to `untrusted`, so
    accepting it would let any node reset its own trust by simply reconnecting.
    """
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]),
                                trust_tier=TrustTier.OWNER_SECONDARY.value)
    assert app.devices.registry.get("phone_main").manifest.trust_tier == \
        TrustTier.OWNER_SECONDARY.value
    # the node reconnects and claims to be untrusted (or anything else)
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"],
                                               trust_tier=TrustTier.UNTRUSTED.value),
                                state=DeviceState.ONLINE.value)
    assert app.devices.registry.get("phone_main").manifest.trust_tier == \
        TrustTier.OWNER_SECONDARY.value, "trust must survive a reconnect"


def test_a_device_cannot_promote_itself_to_owner(app):
    app.devices.registry.upsert(DeviceManifest(device_id="guest_phone",
                                               capabilities=["media.next"]),
                                trust_tier=TrustTier.GUEST.value)
    app.devices.registry.upsert(DeviceManifest(device_id="guest_phone",
                                               trust_tier=TrustTier.OWNER_PRIMARY.value),
                                state=DeviceState.ONLINE.value)
    assert app.devices.registry.get("guest_phone").manifest.trust_tier == \
        TrustTier.GUEST.value
    assert app.devices.registry.get("guest_phone").trusted() is False


def test_reconnect_keeps_the_owner_known_name_and_type(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main", name="Pixel",
                                               type=DeviceType.ANDROID.value),
                                trust_tier=TrustTier.OWNER_SECONDARY.value)
    # a node that reports neither name nor type must not blank them out
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"), state=DeviceState.ONLINE.value)
    record = app.devices.registry.get("phone_main")
    assert record.manifest.name == "Pixel"
    assert record.manifest.type == DeviceType.ANDROID.value


def test_state_transitions_and_online_detection(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]),
                                state=DeviceState.PAIRED.value)
    assert app.devices.registry.get("phone_main").online() is False
    app.devices.registry.set_state("phone_main", DeviceState.ONLINE.value)
    assert app.devices.registry.get("phone_main").online() is True
    app.devices.registry.set_state("phone_main", DeviceState.DISABLED.value)
    assert app.devices.registry.get("phone_main").online() is False
    assert app.devices.registry.get("phone_main").trusted() is False


def test_online_state_times_out_without_a_heartbeat(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"),
                                state=DeviceState.ONLINE.value)
    record = app.devices.registry.get("phone_main")
    # pretend the last heartbeat was long ago
    assert record.online(now_ms=record.last_seen + 10 * 60 * 1000) is False


def test_heartbeat_brings_a_paired_device_online(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"),
                                state=DeviceState.PAIRED.value)
    app.devices.registry.touch("phone_main")
    assert app.devices.registry.get("phone_main").state == DeviceState.ONLINE.value


def test_the_local_machine_is_always_reachable(app):
    """The local device is this process — it cannot be 'unreachable' for want of a heartbeat.

    Without this the mesh summary reported the local machine as offline while the per-device
    line called it online, which is exactly the kind of contradiction that hides real outages.
    """
    record = app.devices.registry.get("pc_main")
    assert record.manifest.transport == "local"
    assert record.online(now_ms=record.last_seen + 24 * 60 * 60 * 1000) is True
    assert app.devices.status()["online"] >= 1


def test_capabilities_can_be_updated(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]))
    app.devices.registry.update_capabilities("phone_main", ["media.next", "screen.observe"])
    assert app.devices.registry.capabilities("phone_main") == ["media.next", "screen.observe"]


def test_find_by_capability(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]))
    app.devices.registry.upsert(DeviceManifest(device_id="laptop_main",
                                               capabilities=["screen.observe"]))
    found = {r.device_id for r in app.devices.registry.find_by_capability("media.next")}
    assert "phone_main" in found
    assert "laptop_main" not in found
    assert app.devices.registry.supports("laptop_main", "screen.observe") is True
    assert app.devices.registry.supports("laptop_main", "media.next") is False


def test_find_by_capability_can_exclude_devices(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]))
    found = app.devices.registry.find_by_capability("media.next", exclude=["pc_main"])
    assert [r.device_id for r in found] == ["phone_main"]


def test_the_local_machine_cannot_be_forgotten(app):
    assert app.devices.registry.forget("pc_main")["ok"] is False
    assert app.devices.registry.forget("laptop_main")["ok"] is True


# ------------------------------------------------------------------ pairings
def test_pairing_stores_the_secret_in_the_vault_not_the_database(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"))
    code = "654321"
    result = app.devices.pair("phone_main", code, name="Pixel", type=DeviceType.ANDROID.value)
    assert result["ok"] is True
    assert app.devices.registry.paired("phone_main") is True
    # the secret is resolvable, and the raw code never appears in the database
    assert app.devices.registry.pairing_secret("phone_main") == \
        derive_pairing_secret(code, "phone_main")
    rows = app.db.query("SELECT token_ref FROM device_pairings WHERE device_id=?",
                        ("phone_main",))
    assert rows and rows[0]["token_ref"].startswith("secret://")
    assert code not in str([dict(r) for r in rows])


def test_pairing_grants_no_capability_scopes(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]))
    app.devices.pair("phone_main", "123456")
    assert app.trust.check(app.ctx(person_id="owner"),
                           "device:phone_main:media").allow is False, \
        "pairing must not silently grant device scopes"


def test_unpair_revokes_the_pairing(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"))
    app.devices.pair("phone_main", "123456")
    app.devices.unpair("phone_main")
    assert app.devices.registry.paired("phone_main") is False
    assert app.devices.registry.pairing_secret("phone_main") == ""


def test_pairing_requires_a_code(app):
    assert app.devices.pair("phone_main", "")["ok"] is False


def test_registry_status_reports_counts(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main"),
                                state=DeviceState.ONLINE.value)
    status = app.devices.status()
    assert status["count"] >= 2
    assert status["online"] >= 1
    assert "transport" in status


# ------------------------------------------------------------------ routing refusals
def test_executing_on_an_unknown_device_is_refused(app):
    invocation = app.devices.execute(app.ctx(), "phone_main", "media.next")
    assert invocation.ok is False
    assert invocation.error_code == "unknown_device"


def test_executing_an_undeclared_capability_is_refused(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]),
                                state=DeviceState.ONLINE.value)
    app.devices.registry.set_trust("phone_main", TrustTier.OWNER_SECONDARY.value)
    invocation = app.devices.execute(app.ctx(), "phone_main", "files.delete")
    assert invocation.ok is False
    assert invocation.error_code == "unsupported_capability"


def test_trust_is_checked_before_capabilities(app):
    """An untrusted principal learns nothing about a device's capability surface."""
    app.devices.registry.upsert(DeviceManifest(device_id="guest_phone",
                                               capabilities=["media.next"]),
                                state=DeviceState.ONLINE.value)
    invocation = app.devices.execute(app.ctx(), "guest_phone", "files.delete")
    assert invocation.error_code == "untrusted_device"


def test_executing_on_a_disabled_device_is_refused(app):
    app.devices.registry.upsert(DeviceManifest(device_id="phone_main",
                                               capabilities=["media.next"]),
                                state=DeviceState.DISABLED.value)
    invocation = app.devices.execute(app.ctx(), "phone_main", "media.next")
    assert invocation.ok is False
    assert invocation.error_code == "device_disabled"


def test_executing_on_an_untrusted_device_is_refused(app):
    app.devices.registry.upsert(DeviceManifest(device_id="guest_phone",
                                               capabilities=["media.next"]),
                                state=DeviceState.ONLINE.value)
    invocation = app.devices.execute(app.ctx(), "guest_phone", "media.next")
    assert invocation.ok is False
    assert invocation.error_code == "untrusted_device"


def test_local_capability_runs_through_the_device_contract(app, tmp_path):
    """The local machine is a device too — the same contract, in-process."""
    app.trust.grant(principal="owner", scope="device:pc_main:files")
    target = tmp_path / "device-note.txt"
    invocation = app.devices.execute(app.ctx(person_id="owner"), "pc_main", "files.write",
                                     {"path": str(target), "text": "hello device"})
    assert invocation.ok is True
    assert invocation.verified is True
    assert target.read_text(encoding="utf-8") == "hello device"


def test_local_capability_requires_its_scope(app, tmp_path):
    """Default deny applies to the local device as well."""
    target = tmp_path / "denied.txt"
    invocation = app.devices.execute(app.ctx(person_id="owner"), "pc_main", "files.write",
                                     {"path": str(target), "text": "x"})
    assert invocation.ok is False
    assert invocation.error_code == "permission_denied"
    assert not target.exists()
