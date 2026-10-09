"""Phase 7 — Pi / IoT / home.

Exit gate: *relay/sensor command idempotent + audited; home-automation bridge works.*

Two halves, both real:

* **Peripherals** (`devices/peripherals.py`): the relay/sensor/GPIO contract, exercised through the
  device mesh so idempotency and audit are proven at the mesh level, not just in the provider.
  The provider used is the explicitly-labelled virtual board — hardware backends (sysfs, serial)
  are implemented but cannot be exercised on this host, and that is stated rather than hidden.
* **Home bridge** (`plugins/installed/home/`): a real HTTP controller served by a threaded local
  server, driven through the real plugin host. Real requests, real JSON, real read-back
  verification.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.contracts import CallContext, Persona, TaskType
from devices.contracts import DeviceState, TrustTier
from devices.node import DeviceNode, PeripheralHandler
from devices.peripherals import (MAX_PULSE_MS, MemoryBoardProvider, NullProvider, PeripheralKind,
                                 PeripheralService, SerialRelayProvider, SysfsGpioProvider,
                                 peripheral_capabilities, select_provider)


# ======================================================================= peripherals
def board() -> MemoryBoardProvider:
    return MemoryBoardProvider(relays=2, gpio_pins=[17, 27],
                               sensors={"temp1": {"name": "Workshop temp", "unit": "C",
                                                  "value": 21.5, "min": -20, "max": 60},
                                        "door1": {"name": "Door contact", "unit": "",
                                                  "value": 0}})


def test_relay_set_is_idempotent_and_never_toggles():
    """The safety property that matters: a retried `relay.set` must not flip a physical switch."""
    provider = board()
    first = provider.write("relay1", 1)
    second = provider.write("relay1", 1)
    assert first["data"]["changed"] is True
    assert second["data"]["changed"] is False, "the second identical write must be a no-op"
    assert provider.read("relay1")["value"] == 1


def test_relay_off_is_idempotent_too():
    provider = board()
    provider.write("relay1", 1)
    off = provider.write("relay1", 0)
    again = provider.write("relay1", 0)
    assert off["data"]["changed"] is True
    assert again["data"]["changed"] is False
    assert provider.read("relay1")["value"] == 0


def test_accepts_common_truthy_spellings():
    provider = board()
    for spelling in ("1", "on", "ON", "true", "high"):
        provider.write("relay1", 0)
        assert provider.write("relay1", spelling)["value"] == 1, spelling


def test_a_relay_write_reports_its_previous_state():
    provider = board()
    provider.write("relay2", 1)
    outcome = provider.write("relay2", 0)
    assert outcome["data"]["previous"] == 1
    assert outcome["data"]["value"] == 0


def test_an_unknown_relay_is_refused():
    provider = board()
    outcome = provider.write("relay9", 1)
    assert outcome["ok"] is False
    assert outcome["error_code"] == "unknown_peripheral"


def test_a_gpio_write_is_read_back_and_verified():
    provider = board()
    outcome = provider.write("gpio17", 1)
    assert outcome["ok"] is True and outcome["verified"] is True
    assert provider.read("gpio17")["value"] == 1


def test_a_sensor_read_is_verified():
    provider = board()
    outcome = provider.read("temp1")
    assert outcome["ok"] is True and outcome["verified"] is True
    assert outcome["value"] == 21.5
    assert outcome["data"]["unit"] == "C"


def test_pulse_is_bounded():
    """An unattended relay held on is a hazard, so a long pulse is refused."""
    service = PeripheralService(board())
    refused = service.handle("relay.pulse", {"peripheral_id": "relay1",
                                             "ms": MAX_PULSE_MS + 1})
    assert refused["ok"] is False
    assert refused["error_code"] == "pulse_too_long"
    allowed = service.handle("relay.pulse", {"peripheral_id": "relay1", "ms": 10})
    assert allowed["ok"] is True


def test_pulse_leaves_the_relay_off():
    provider = board()
    PeripheralService(provider).handle("relay.pulse", {"peripheral_id": "relay1", "ms": 10})
    assert provider.read("relay1")["value"] == 0


def test_capabilities_are_derived_from_the_provider():
    caps = peripheral_capabilities(board())
    assert "relay.set" in caps and "relay.pulse" in caps
    assert "gpio.read" in caps and "gpio.write" in caps
    assert "sensor.read" in caps and "sensor.list" in caps
    assert "peripheral.list" in caps


def test_an_unavailable_provider_declares_nothing():
    """A node with no backend must advertise nothing rather than capabilities it cannot serve."""
    assert peripheral_capabilities(NullProvider()) == []


def test_an_unavailable_provider_reports_why():
    service = PeripheralService(NullProvider("no GPIO on this host"))
    outcome = service.handle("relay.set", {"peripheral_id": "relay1", "value": 1})
    assert outcome["ok"] is False
    assert outcome["error_code"] == "peripherals_unavailable"
    assert "no GPIO on this host" in outcome["detail"]


def test_the_test_backend_is_never_auto_selected():
    """A real deployment must never silently believe it controlled hardware."""
    provider = select_provider()
    assert provider.backend != "memory"
    assert select_provider({"backend": "memory"}).backend == "memory"


def test_sysfs_provider_is_honest_on_a_non_linux_host():
    provider = SysfsGpioProvider()
    if provider.available():
        pytest.skip("this host really has GPIO sysfs")
    assert "Linux" in provider.reason() or "sysfs" in provider.reason()
    assert provider.list() == []


def test_serial_relay_provider_is_unavailable_without_a_port():
    provider = SerialRelayProvider(port="")
    assert provider.available() is False
    assert "port" in provider.reason()


def test_serial_relay_frame_encodes_the_channel():
    provider = SerialRelayProvider(port="COM9", channels=4)
    assert provider._frame(2, True) == bytes.fromhex("A00201A1")
    assert provider._frame(2, False) == bytes.fromhex("A00200A2")


def test_peripheral_status_lists_what_exists():
    status = PeripheralService(board()).status()
    assert status["available"] is True
    assert status["backend"] == "memory"
    kinds = {p["kind"] for p in status["peripherals"]}
    assert kinds == {PeripheralKind.RELAY.value, PeripheralKind.GPIO.value,
                     PeripheralKind.SENSOR.value}


# ------------------------------------------------------------ peripherals over the mesh
@pytest.fixture()
def pi_mesh(app, tmp_path):
    """A real Pi-style node over real TCP, backed by the virtual board."""
    from devices.service import DeviceService
    from tests.e2e.test_devices_phase6 import free_port
    provider = board()
    handler = PeripheralHandler(provider)
    service = DeviceService(app.db, audit=app.audit, trust=app.trust, vault=app.vault,
                            port=free_port(), auto_start=True, command_timeout_s=20.0)
    node = DeviceNode(device_id="rpi_main", name="Workshop Pi",
                      type="raspberry_pi", handler=handler,
                      host=service.server.host, port=service.server.port, code="246810")
    try:
        node.start_background()
        time.sleep(1.0)
        service.pair("rpi_main", "246810", name="Workshop Pi", type="raspberry_pi",
                     capabilities=handler.CAPABILITIES)
        deadline = time.time() + 20
        while time.time() < deadline and not service.server.connected("rpi_main"):
            time.sleep(0.2)
        assert service.server.connected("rpi_main"), "the Pi node must come online"
        app.trust.grant(principal="owner", scope="device:rpi_main:relay")
        app.trust.grant(principal="owner", scope="device:rpi_main:sensor")
        app.trust.grant(principal="owner", scope="device:rpi_main:gpio")
        yield service, provider
    finally:
        node.stop()
        service.stop()


def owner_ctx(**kw) -> CallContext:
    return CallContext(person_id="owner", persona=Persona.OWNER, **kw)


def test_a_relay_command_reaches_the_pi_node_and_is_audited(pi_mesh, app):
    service, provider = pi_mesh
    invocation = service.execute(owner_ctx(), "rpi_main", "relay.set",
                                 {"peripheral_id": "relay1", "value": 1})
    assert invocation.ok is True, invocation.to_dict()
    assert provider.read("relay1")["value"] == 1
    # audited: the mesh records the command for the device
    row = service.command(invocation.command_id)
    assert row["device_id"] == "rpi_main"
    assert row["capability"] == "relay.set"
    entries = [e for e in app.audit.tail(50)
               if e.get("action") == "device.command:relay.set"]
    assert entries, "a relay command must be audited"


def test_a_relay_command_is_idempotent_across_the_mesh(pi_mesh):
    """The mesh's own idempotency key plus the idempotent primitive: one physical change."""
    service, provider = pi_mesh
    first = service.execute(owner_ctx(idempotency_key="relay-1-on"), "rpi_main", "relay.set",
                            {"peripheral_id": "relay1", "value": 1})
    second = service.execute(owner_ctx(idempotency_key="relay-1-on"), "rpi_main", "relay.set",
                             {"peripheral_id": "relay1", "value": 1})
    assert first.ok and second.ok
    assert first.command_id == second.command_id, \
        "the same idempotency key must not reach the device twice"
    rows = [r for r in service.recent_commands("rpi_main") if r["capability"] == "relay.set"]
    assert len(rows) == 1


def test_a_repeated_relay_command_without_a_key_is_still_safe(pi_mesh):
    """Even with no idempotency key, the primitive itself cannot toggle."""
    service, provider = pi_mesh
    service.execute(owner_ctx(), "rpi_main", "relay.set",
                    {"peripheral_id": "relay1", "value": 1})
    service.execute(owner_ctx(), "rpi_main", "relay.set",
                    {"peripheral_id": "relay1", "value": 1})
    assert provider.read("relay1")["value"] == 1


def test_a_sensor_read_reaches_the_node_and_is_verified(pi_mesh):
    service, _ = pi_mesh
    invocation = service.execute(owner_ctx(), "rpi_main", "sensor.read",
                                 {"peripheral_id": "temp1"})
    assert invocation.ok is True, invocation.to_dict()
    assert invocation.verified is True
    assert invocation.raw["data"]["value"] == 21.5


def test_a_sensor_read_is_audited(pi_mesh, app):
    service, _ = pi_mesh
    service.execute(owner_ctx(), "rpi_main", "sensor.read", {"peripheral_id": "temp1"})
    entries = [e for e in app.audit.tail(50)
               if e.get("action") == "device.command:sensor.read"]
    assert entries


def test_a_peripheral_command_still_obeys_default_deny(app, tmp_path):
    """Pairing a Pi grants nothing: the relay scope must be granted explicitly."""
    from devices.service import DeviceService
    from tests.e2e.test_devices_phase6 import free_port
    provider = board()
    handler = PeripheralHandler(provider)
    service = DeviceService(app.db, audit=app.audit, trust=app.trust, vault=app.vault,
                            port=free_port(), auto_start=True)
    node = DeviceNode(device_id="rpi_main", handler=handler, type="raspberry_pi",
                      host=service.server.host, port=service.server.port, code="111222")
    try:
        node.start_background()
        time.sleep(1.0)
        service.pair("rpi_main", "111222", capabilities=handler.CAPABILITIES)
        deadline = time.time() + 20
        while time.time() < deadline and not service.server.connected("rpi_main"):
            time.sleep(0.2)
        denied = service.execute(owner_ctx(), "rpi_main", "relay.set",
                                 {"peripheral_id": "relay1", "value": 1})
        assert denied.ok is False
        assert denied.error_code == "permission_denied"
        assert provider.read("relay1")["value"] == 0, "the relay must not have moved"
    finally:
        node.stop()
        service.stop()


def test_the_pi_node_declares_only_what_it_can_do(pi_mesh):
    service, _ = pi_mesh
    caps = service.registry.capabilities("rpi_main")
    assert "relay.set" in caps and "sensor.read" in caps
    assert "files.write" not in caps, "the peripheral node offers no file capability"


def test_a_capability_the_pi_does_not_have_is_refused(pi_mesh):
    service, _ = pi_mesh
    invocation = service.execute(owner_ctx(), "rpi_main", "files.write",
                                 {"path": "x.txt", "text": "x"})
    assert invocation.ok is False
    assert invocation.error_code == "unsupported_capability"


def test_a_device_action_routes_to_the_pi_through_the_worker(pi_mesh, app):
    service, provider = pi_mesh
    from agents.runtime import CapabilityWorker
    worker = CapabilityWorker(computer=app.computer, device=service, skills=app.skills)
    out = worker(owner_ctx(), {"type": TaskType.DEVICE_ACTION.value, "device": "rpi_main",
                               "capability": "relay.set",
                               "params": {"peripheral_id": "relay2", "value": 1}})
    assert out["ok"] is True, out
    assert provider.read("relay2")["value"] == 1


# ==================================================================== home bridge
class FakeController(BaseHTTPRequestHandler):
    """A stand-in home controller speaking the same REST shape.

    The bridge's contract *is* HTTP, so a real HTTP server is the honest way to test it — this is
    not a mock of the bridge, it is the other end of a real socket.
    """

    states = {
        "light.kitchen": {"state": "off", "attributes": {"friendly_name": "Kitchen",
                                                         "brightness_pct": 0}},
        "switch.fan": {"state": "off", "attributes": {"friendly_name": "Fan"}},
        "scene.movie": {"state": "unknown", "attributes": {"friendly_name": "Movie"}},
    }
    calls: list = []
    token_ok = True

    def log_message(self, *args):                       # keep the test output clean
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                   # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            return self._send({"message": "unauthorized"}, 401)
        if self.path == "/api/":
            return self._send({"message": "API running"})
        if self.path == "/api/states":
            return self._send([{"entity_id": k, **v} for k, v in self.states.items()])
        if self.path.startswith("/api/states/"):
            entity_id = self.path[len("/api/states/"):]
            if entity_id in self.states:
                return self._send({"entity_id": entity_id, **self.states[entity_id]})
            return self._send({"message": "not found"}, 404)
        return self._send({"message": "not found"}, 404)

    def do_POST(self):                                  # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            return self._send({"message": "unauthorized"}, 401)
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.calls.append({"path": self.path, "payload": payload})
        if self.path == "/api/services/scene/turn_on":
            entity_id = payload.get("entity_id", "")
            if entity_id in self.states:
                self.states[entity_id]["state"] = "scening"
                self.states[entity_id]["attributes"]["last_activated"] = "now"
            return self._send([])
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "services":
            entity_id = payload.get("entity_id", "")
            if entity_id not in self.states:
                return self._send({"message": "unknown entity"}, 404)
            if parts[3] == "turn_on":
                self.states[entity_id]["state"] = "on"
                if "brightness_pct" in payload:
                    self.states[entity_id]["attributes"]["brightness_pct"] = \
                        payload["brightness_pct"]
            elif parts[3] == "turn_off":
                self.states[entity_id]["state"] = "off"
            return self._send([])
        return self._send({"message": "not found"}, 404)


@pytest.fixture()
def controller():
    FakeController.states = {
        "light.kitchen": {"state": "off", "attributes": {"friendly_name": "Kitchen",
                                                         "brightness_pct": 0}},
        "switch.fan": {"state": "off", "attributes": {"friendly_name": "Fan"}},
        "scene.movie": {"state": "unknown", "attributes": {"friendly_name": "Movie"}},
    }
    FakeController.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeController)
    server.token = "test-token"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()


def bridge_for(url: str, token: str = "test-token"):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "plugins" / "installed" / "home" / "adapter.py"
    spec = spec_from_file_location("genie_home_adapter_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HomeBridge(url, token)


def test_the_bridge_lists_entities_over_real_http(controller):
    url, _ = controller
    result = bridge_for(url).entities()
    assert result["ok"] is True
    ids = {e["entity_id"] for e in result["data"]["entities"]}
    assert {"light.kitchen", "switch.fan"} <= ids


def test_the_bridge_filters_entities_by_domain(controller):
    url, _ = controller
    result = bridge_for(url).entities("scene")
    assert [e["entity_id"] for e in result["data"]["entities"]] == ["scene.movie"]


def test_the_bridge_reads_state(controller):
    url, _ = controller
    result = bridge_for(url).state("light.kitchen")
    assert result["ok"] is True and result["verified"] is True
    assert result["data"]["state"] == "off"


def test_turn_on_changes_the_controller_and_verifies(controller):
    url, server = controller
    result = bridge_for(url).set_power("light.kitchen", True)
    assert result["ok"] is True and result["verified"] is True
    assert FakeController.states["light.kitchen"]["state"] == "on"
    assert any(c["path"].endswith("/light/turn_on") for c in FakeController.calls)


def test_turn_on_is_idempotent_and_does_not_touch_the_device_again(controller):
    """Already on → no service call. That is what makes a mesh retry safe."""
    url, _ = controller
    bridge = bridge_for(url)
    bridge.set_power("light.kitchen", True)
    calls_after_first = len(FakeController.calls)
    second = bridge.set_power("light.kitchen", True)
    assert second["ok"] is True
    assert second.get("no_op") is True
    assert len(FakeController.calls) == calls_after_first, \
        "an already-on light must not be commanded again"


def test_turn_off_is_idempotent(controller):
    url, _ = controller
    bridge = bridge_for(url)
    bridge.set_power("light.kitchen", True)
    assert bridge.set_power("light.kitchen", False)["verified"] is True
    assert FakeController.states["light.kitchen"]["state"] == "off"
    before = len(FakeController.calls)
    again = bridge.set_power("light.kitchen", False)
    assert again["no_op"] is True and len(FakeController.calls) == before


def test_set_brightness_is_verified_against_the_read_back(controller):
    url, _ = controller
    result = bridge_for(url).set_brightness("light.kitchen", 40)
    assert result["ok"] is True and result["verified"] is True
    assert FakeController.states["light.kitchen"]["attributes"]["brightness_pct"] == 40


def test_a_light_that_does_not_change_is_reported_as_a_failure(controller, monkeypatch):
    """A 200 response is not proof the device changed — the read-back decides."""
    url, server = controller
    bridge = bridge_for(url)
    original = FakeController.states["light.kitchen"]["state"]
    original_get = FakeController.do_GET

    def stubborn_get(self):
        if self.path == "/api/states/light.kitchen":
            # the controller always reports the ORIGINAL state, whatever it was told to do
            return self._send({"entity_id": "light.kitchen", "state": original,
                               "attributes": {}})
        return original_get(self)

    monkeypatch.setattr(FakeController, "do_GET", stubborn_get)
    result = bridge.set_power("light.kitchen", True)
    assert result["ok"] is False
    assert result["verified"] is False
    assert result["error_code"] == "verification_failed"


def test_activate_scene_is_verified(controller):
    url, _ = controller
    result = bridge_for(url).activate_scene("scene.movie")
    assert result["ok"] is True and result["verified"] is True
    assert FakeController.states["scene.movie"]["state"] == "scening"


def test_a_non_scene_entity_cannot_be_activated_as_a_scene(controller):
    url, _ = controller
    result = bridge_for(url).activate_scene("light.kitchen")
    assert result["ok"] is False and result["error_code"] == "bad_parameter"


def test_brightness_out_of_range_is_refused(controller):
    url, _ = controller
    result = bridge_for(url).set_brightness("light.kitchen", 150)
    assert result["ok"] is False and result["error_code"] == "bad_parameter"


def test_a_bad_token_is_reported_as_an_http_error(controller):
    url, _ = controller
    result = bridge_for(url, token="wrong").state("light.kitchen")
    assert result["ok"] is False
    assert result["error_code"] == "http_401"


def test_an_unreachable_controller_is_reported_honestly():
    result = bridge_for("http://127.0.0.1:1").state("light.kitchen")
    assert result["ok"] is False
    assert result["error_code"] == "controller_unreachable"


def test_no_configured_controller_is_not_a_crash():
    result = bridge_for("").state("light.kitchen")
    assert result["ok"] is False
    assert result["error_code"] == "not_configured"


def test_an_unknown_entity_is_reported(controller):
    url, _ = controller
    result = bridge_for(url).state("light.does_not_exist")
    assert result["ok"] is False
    assert result["error_code"] == "http_404"


# ------------------------------------------------------- the plugin, through its host
def test_the_home_plugin_is_discovered_and_loads():
    from pathlib import Path
    from plugins.host import PluginHost
    from plugins.registry import PluginRegistry
    directory = Path(__file__).resolve().parents[2] / "plugins" / "installed" / "home"
    host = PluginHost(directory)
    report = host.initialize()
    assert report["ok"] is True
    assert report["plugin"] == "home"


def test_the_home_plugin_declares_no_toggle_capability():
    """A toggle cannot be retried safely, so it is deliberately absent."""
    from pathlib import Path
    from plugins.sdk import load_manifest
    directory = Path(__file__).resolve().parents[2] / "plugins" / "installed" / "home"
    manifest = load_manifest(directory)
    names = [c.name for c in manifest.capabilities]
    assert "toggle" not in names
    assert {"turn_on", "turn_off", "set_brightness", "state", "entities"} <= set(names)


def test_the_home_plugin_invokes_through_the_host(controller, monkeypatch):
    url, _ = controller
    from pathlib import Path
    from plugins.host import PluginHost
    monkeypatch.setenv("GENIE_HOME_URL", url)
    monkeypatch.setenv("GENIE_HOME_TOKEN", "test-token")
    directory = Path(__file__).resolve().parents[2] / "plugins" / "installed" / "home"
    host = PluginHost(directory)
    host.initialize()
    result = host.invoke("plugin.home.turn_on", {"entity_id": "light.kitchen"})
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert FakeController.states["light.kitchen"]["state"] == "on"
