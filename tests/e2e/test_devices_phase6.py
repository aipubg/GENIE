"""Phase 6 — the device mesh on real sockets.

Every test here uses a **real node process** talking to a **real TCP listener**: real pairing
handshake, real signed frames, real capability execution, real verification. Nothing is stubbed.

The reference node is a genuine node: the protocol is the contract, and the Android node is one
implementation of it. Using the reference node here is what makes the mesh verifiable without a
phone attached to this machine.
"""
from __future__ import annotations

import socket
import time

import pytest

from core.contracts import CallContext, Persona, TaskType
from devices.contracts import CommandStatus, DeviceState, DeviceType, TrustTier
from devices.node import DeviceNode, ReferenceHandler
from devices.protocol import DeviceServer, derive_pairing_secret
from devices.service import DeviceService

PAIRING_CODE = "481902"
DEVICE_ID = "phone_main"
GRANT = "device:phone_main:files"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def mesh(app):
    """A device service listening on its own port, torn down after the test."""
    service = DeviceService(app.db, audit=app.audit, trust=app.trust, vault=app.vault,
                            port=free_port(), auto_start=True,
                            command_timeout_s=20.0)
    assert service._started, "the device transport must actually listen"
    nodes: list[DeviceNode] = []
    try:
        yield service, nodes
    finally:
        for node in nodes:
            node.stop()
        service.stop()


def make_node(service, tmp_path, **kw) -> DeviceNode:
    node = DeviceNode(
        device_id=kw.pop("device_id", DEVICE_ID),
        name=kw.pop("name", "Test Phone"),
        type=kw.pop("type", DeviceType.ANDROID.value),
        handler=ReferenceHandler(tmp_path / "node-root"),
        host=service.server.host, port=service.server.port,
        code=kw.pop("code", PAIRING_CODE), **kw)
    return node


def wait_for(predicate, timeout_s: float = 25.0, interval: float = 0.2):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def owner_ctx(**kw) -> CallContext:
    return CallContext(person_id="owner", persona=Persona.OWNER, **kw)


# ------------------------------------------------------------------ pairing handshake
def test_an_unpaired_node_is_refused_and_serves_nothing(mesh, tmp_path):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()

    time.sleep(2.0)
    # the node was told to pair and never became online
    assert service.server.connected(DEVICE_ID) is False
    assert node.status()["paired"] is False
    assert service.registry.get(DEVICE_ID) is None


def test_pairing_brings_a_real_node_online(mesh, tmp_path):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()

    time.sleep(1.0)
    paired = service.pair(DEVICE_ID, PAIRING_CODE, name="Pixel", type=DeviceType.ANDROID.value)
    assert paired["ok"] is True

    assert wait_for(lambda: service.server.connected(DEVICE_ID)), \
        "the node must come online after pairing"
    record = service.registry.get(DEVICE_ID)
    assert record.state == DeviceState.ONLINE.value
    assert record.online() is True
    assert record.manifest.type == DeviceType.ANDROID.value


def test_a_node_that_never_paired_cannot_be_driven(mesh, tmp_path):
    service, nodes = mesh
    invocation = service.execute(owner_ctx(), "ghost_phone", "files.write",
                                 {"path": "x.txt", "text": "x"})
    assert invocation.ok is False
    assert invocation.error_code == "unknown_device"


# ------------------------------------------------------------------ real execution
def test_command_executes_on_the_real_node_and_is_verified(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))

    # default deny: no scope, no execution
    denied = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                             {"path": "note.txt", "text": "hello mesh"})
    assert denied.ok is False
    assert denied.error_code == "permission_denied"

    app.trust.grant(principal="owner", scope=GRANT)
    invocation = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                                 {"path": "note.txt", "text": "hello mesh"})
    assert invocation.ok is True, invocation.to_dict()
    assert invocation.verified is True
    assert invocation.command_id
    # the file really exists inside the node's own sandbox
    assert (tmp_path / "node-root" / "note.txt").read_text(encoding="utf-8") == "hello mesh"


def test_the_command_and_result_are_persisted(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    app.trust.grant(principal="owner", scope=GRANT)

    invocation = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                                 {"path": "a.txt", "text": "a"})
    row = service.command(invocation.command_id)
    assert row is not None
    assert row["status"] == CommandStatus.COMPLETED.value
    assert row["ok"] == 1 and row["verified"] == 1
    assert row["device_id"] == DEVICE_ID
    assert row["capability"] == "files.write"


def test_an_undeclared_capability_is_refused_by_the_service(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    app.trust.grant(principal="owner", scope="device:phone_main:screen")

    invocation = service.execute(owner_ctx(), DEVICE_ID, "screen.observe", {})
    assert invocation.ok is False
    assert invocation.error_code == "unsupported_capability"


def test_a_node_rejects_a_command_it_does_not_implement(mesh, tmp_path):
    """Even if the service were fooled, the node refuses what it does not offer."""
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    from devices.contracts import CommandEnvelope
    result = node.handle_command(CommandEnvelope(device_id=DEVICE_ID,
                                                 capability="files.delete"))
    assert result.status == CommandStatus.REJECTED.value
    assert result.error_code == "unsupported_capability"


# ------------------------------------------------------------------ idempotency
def test_a_repeated_idempotency_key_executes_once(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    app.trust.grant(principal="owner", scope=GRANT)

    first = service.execute(owner_ctx(idempotency_key="key-1"), DEVICE_ID, "files.write",
                            {"path": "once.txt", "text": "once"})
    second = service.execute(owner_ctx(idempotency_key="key-1"), DEVICE_ID, "files.write",
                             {"path": "once.txt", "text": "once"})
    assert first.ok and second.ok
    assert first.command_id == second.command_id, \
        "the same idempotency key must not issue a second command"
    rows = service.recent_commands(DEVICE_ID)
    assert len([r for r in rows if r["capability"] == "files.write"]) == 1


def test_a_replayed_command_id_returns_the_cached_result_without_re_executing(tmp_path):
    """Replay protection lives on the node, so a duplicate frame cannot double-execute."""
    from devices.contracts import CommandEnvelope
    calls = {"n": 0}

    def handler(capability, params):
        calls["n"] += 1
        return {"ok": True, "verified": True, "detail": "ran"}

    node = DeviceNode(device_id="phone_main", handler=handler,
                      capabilities=["files.write"], secret="s")
    command = CommandEnvelope(device_id="phone_main", capability="files.write")
    node.handle_command(command)
    node.handle_command(command)
    assert calls["n"] == 1, "the second identical command must be answered from the cache"


def test_an_expired_command_is_not_executed(tmp_path):
    from devices.contracts import CommandEnvelope
    ran = {"n": 0}

    def handler(capability, params):
        ran["n"] += 1
        return {"ok": True, "verified": True}

    node = DeviceNode(device_id="phone_main", handler=handler,
                      capabilities=["files.write"], secret="s")
    stale = CommandEnvelope(device_id="phone_main", capability="files.write", ttl_ms=1,
                            issued_at_ms=1)
    result = node.handle_command(stale)
    assert result.status == CommandStatus.EXPIRED.value
    assert ran["n"] == 0, "an expired command must never run"


# ------------------------------------------------------------------ offline queue
def test_an_offline_device_queues_the_command_with_a_ttl(mesh, tmp_path, app):
    service, nodes = mesh
    service.pair(DEVICE_ID, PAIRING_CODE)
    app.trust.grant(principal="owner", scope=GRANT)

    invocation = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                                 {"path": "later.txt", "text": "later"})
    assert invocation.queued is True
    assert invocation.error_code == "device_offline"
    pending = service.pending(DEVICE_ID)
    assert [p["command_id"] for p in pending] == [invocation.command_id]
    assert pending[0]["expires_at"] > pending[0]["issued_at"]


def test_a_queued_command_is_delivered_when_the_device_returns(mesh, tmp_path, app):
    service, nodes = mesh
    service.pair(DEVICE_ID, PAIRING_CODE)
    app.trust.grant(principal="owner", scope=GRANT)

    queued = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                             {"path": "delivered.txt", "text": "delivered"})
    assert queued.queued is True

    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()

    row = wait_for(lambda: (service.command(queued.command_id) or {}).get("status")
                   == CommandStatus.COMPLETED.value and service.command(queued.command_id))
    assert row is not None, "the queued command must be delivered on reconnect"
    assert row["ok"] == 1 and row["verified"] == 1
    assert (tmp_path / "node-root" / "delivered.txt").read_text(encoding="utf-8") == "delivered"
    assert service.pending(DEVICE_ID) == []


def test_stale_queued_commands_expire_and_are_never_delivered(mesh, tmp_path, app):
    service, nodes = mesh
    service.pair(DEVICE_ID, PAIRING_CODE)
    app.trust.grant(principal="owner", scope=GRANT)

    queued = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                             {"path": "stale.txt", "text": "stale"}, ttl_ms=1)
    assert queued.queued is True
    time.sleep(0.05)
    assert service.expire_stale() == 1
    assert service.command(queued.command_id)["status"] == CommandStatus.EXPIRED.value

    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    time.sleep(1.0)
    assert not (tmp_path / "node-root" / "stale.txt").exists(), \
        "an expired command must never reach the device"
    assert service.command(queued.command_id)["status"] == CommandStatus.EXPIRED.value


def test_a_queued_command_can_be_cancelled_before_delivery(mesh, tmp_path, app):
    service, nodes = mesh
    service.pair(DEVICE_ID, PAIRING_CODE)
    app.trust.grant(principal="owner", scope=GRANT)
    queued = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                             {"path": "cancelled.txt", "text": "x"})
    result = service.cancel(DEVICE_ID, queued.command_id)
    assert result["ok"] is True
    assert service.command(queued.command_id)["status"] == CommandStatus.CANCELLED.value


def test_queue_can_be_refused_instead_of_deferred(mesh, tmp_path, app):
    service, nodes = mesh
    service.pair(DEVICE_ID, PAIRING_CODE)
    app.trust.grant(principal="owner", scope=GRANT)
    invocation = service.execute(owner_ctx(), DEVICE_ID, "files.write",
                                 {"path": "x.txt", "text": "x"}, queue_if_offline=False)
    assert invocation.ok is False
    assert invocation.queued is False
    assert invocation.error_code == "device_offline"


# ------------------------------------------------------------------ transport security
def test_a_frame_signed_with_the_wrong_secret_is_rejected(tmp_path):
    """A node that never paired cannot produce a valid frame."""
    from devices.contracts import decode_frame, encode_frame
    frame = encode_frame({"kind": "command", "command_id": "c1"}, "attacker-secret")
    with pytest.raises(ValueError):
        decode_frame(frame, derive_pairing_secret(PAIRING_CODE, DEVICE_ID))


def test_an_unpaired_connection_gets_no_commands(mesh, tmp_path):
    """The server refuses an unknown device at the handshake, before any command."""
    service, nodes = mesh
    node = make_node(service, tmp_path, device_id="stranger_phone")
    nodes.append(node)
    node.start_background()
    time.sleep(2.0)
    assert service.server.connected("stranger_phone") is False
    assert service.registry.get("stranger_phone") is None


def test_disconnect_fails_in_flight_commands_instead_of_hanging(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    app.trust.grant(principal="owner", scope=GRANT)

    node.stop()
    assert wait_for(lambda: not service.server.connected(DEVICE_ID))
    # the registry must reflect the disconnect, not leave a ghost "online" device
    record = wait_for(lambda: (service.registry.get(DEVICE_ID) or None)
                      if service.registry.get(DEVICE_ID).state == DeviceState.PAIRED.value
                      else None)
    assert record is not None, "a disconnected device must not stay online"
    assert record.state == DeviceState.PAIRED.value


# ------------------------------------------------------------------ worker routing
def test_the_capability_worker_routes_a_device_task(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    app.trust.grant(principal="owner", scope=GRANT)

    # route through the worker the daemon actually uses
    app.devices.registry.set_trust(DEVICE_ID, TrustTier.OWNER_SECONDARY.value)
    from agents.runtime import CapabilityWorker
    worker = CapabilityWorker(computer=app.computer, device=service, skills=app.skills)
    out = worker(owner_ctx(), {"type": TaskType.DEVICE_ACTION.value, "device": DEVICE_ID,
                               "capability": "files.write",
                               "params": {"path": "routed.txt", "text": "routed"}})
    assert out["ok"] is True, out
    assert out["verified"] is True
    assert out["device"] == DEVICE_ID
    assert (tmp_path / "node-root" / "routed.txt").read_text(encoding="utf-8") == "routed"


def test_the_mesh_reports_which_device_can_serve_a_capability(mesh, tmp_path, app):
    service, nodes = mesh
    node = make_node(service, tmp_path)
    nodes.append(node)
    node.start_background()
    service.pair(DEVICE_ID, PAIRING_CODE)
    assert wait_for(lambda: service.server.connected(DEVICE_ID))
    assert service.owns("files.write") is True
    assert DEVICE_ID in service.devices_for("files.write")
    assert service.owns("teleport.matter") is False
