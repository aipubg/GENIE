"""Peripheral contract (devices/peripherals.py) — GPIO, relay and sensors.

The Raspberry Pi is a *node* like any other (docs/DEVICES.md): it declares peripherals as
capabilities and the mesh routes to it. This module owns the peripheral layer so the Pi node, a
USB relay board and a future ESP32 all speak the same thing.

Backends:
  * `SysfsGpioProvider`   — Linux/Pi GPIO through `/sys/class/gpio` (real hardware path)
  * `SerialRelayProvider` — USB relay boards over a serial line (real hardware path)
  * `NullProvider`        — honest "this platform has no peripheral backend"
  * `MemoryBoardProvider` — the **test backend**, explicitly labelled as such. It is a test
                            double, not hardware, and is never selected automatically.

Safety semantics that matter more than the API shape:

  * **`relay.set` is idempotent.** Writing the same state twice must NOT toggle a relay. A
    `toggle` would be a dangerous primitive to expose: a retried command would flip a physical
    switch. Idempotence is what makes the mesh's retry/queue behaviour safe for real hardware.
  * **`pulse` is explicit and bounded.** A pulse is inherently not idempotent, so it must be asked
    for by name and carries a hard maximum duration.
  * **Reads are verified, writes are verified only when the backend can read back.** A backend
    that cannot observe its own output reports `verified = false` rather than claiming success.
"""
from __future__ import annotations

import enum
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("devices.peripherals")

#: A pulse longer than this is refused: an unattended relay held on is a hazard, not a feature.
MAX_PULSE_MS = 10_000


class PeripheralKind(str, enum.Enum):
    GPIO = "gpio"
    RELAY = "relay"
    SENSOR = "sensor"
    PWM = "pwm"
    SERIAL = "serial"


class PeripheralBackend(str, enum.Enum):
    SYSFS = "sysfs"
    SERIAL = "serial"
    MEMORY = "memory"          # test backend — never auto-selected
    NONE = "none"


@dataclass
class PeripheralInfo:
    peripheral_id: str
    kind: str = PeripheralKind.GPIO.value
    name: str = ""
    pin: Optional[int] = None
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    readable: bool = True
    writable: bool = False
    board: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"peripheral_id": self.peripheral_id, "kind": self.kind, "name": self.name,
                "pin": self.pin, "unit": self.unit, "min": self.minimum, "max": self.maximum,
                "readable": self.readable, "writable": self.writable, "board": self.board}


class PeripheralProvider:
    """The contract every peripheral backend satisfies."""

    backend = PeripheralBackend.NONE.value

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        return "no peripheral backend on this platform"

    def list(self) -> List[PeripheralInfo]:
        return []

    def read(self, peripheral_id: str) -> Dict[str, Any]:
        return {"ok": False, "verified": False, "error_code": "unsupported",
                "detail": f"this backend cannot read {peripheral_id}"}

    def write(self, peripheral_id: str, value: Any) -> Dict[str, Any]:
        return {"ok": False, "verified": False, "error_code": "unsupported",
                "detail": f"this backend cannot write {peripheral_id}"}

    def pulse(self, peripheral_id: str, ms: int) -> Dict[str, Any]:
        return {"ok": False, "verified": False, "error_code": "unsupported",
                "detail": "this backend cannot pulse"}

    def _unknown(self, peripheral_id: str) -> Dict[str, Any]:
        return {"ok": False, "verified": False, "error_code": "unknown_peripheral",
                "detail": f"{peripheral_id} is not present on this node"}


# --------------------------------------------------------------------------- Linux / Pi
class SysfsGpioProvider(PeripheralProvider):
    """Real GPIO on Linux/Raspberry Pi through the kernel sysfs interface."""

    backend = PeripheralBackend.SYSFS.value

    def __init__(self, root: str = "/sys/class/gpio", pins: Optional[List[int]] = None):
        self.root = Path(root)
        self._pins = list(pins or [])

    def available(self) -> bool:
        return os.name == "posix" and (self.root / "export").exists()

    def reason(self) -> str:
        if os.name != "posix":
            return "GPIO sysfs is only available on Linux (this host is not Linux)"
        return f"{self.root}/export is missing — GPIO is not exposed by this kernel"

    def list(self) -> List[PeripheralInfo]:
        if not self.available():
            return []
        pins = self._pins or self._discover_exported()
        return [PeripheralInfo(peripheral_id=f"gpio{pin}", kind=PeripheralKind.GPIO.value,
                               name=f"GPIO {pin}", pin=pin, readable=True, writable=True,
                               board="sysfs") for pin in pins]

    def _discover_exported(self) -> List[int]:
        found: List[int] = []
        try:
            for entry in self.root.iterdir():
                if entry.name.startswith("gpio") and entry.name[4:].isdigit():
                    found.append(int(entry.name[4:]))
        except OSError:
            return []
        return sorted(found)

    def _pin_dir(self, peripheral_id: str) -> Optional[Path]:
        if not peripheral_id.startswith("gpio"):
            return None
        pin = peripheral_id[4:]
        if not pin.isdigit():
            return None
        path = self.root / f"gpio{pin}"
        if not path.exists():
            try:
                (self.root / "export").write_text(pin)
                path = self.root / f"gpio{pin}"
            except OSError as exc:
                log.warning("could not export %s: %s", peripheral_id, exc)
                return None
        return path if path.exists() else None

    def read(self, peripheral_id: str) -> Dict[str, Any]:
        path = self._pin_dir(peripheral_id)
        if path is None:
            return self._unknown(peripheral_id)
        try:
            value = int((path / "value").read_text().strip())
            return {"ok": True, "verified": True, "value": value,
                    "detail": f"{peripheral_id} = {value}",
                    "data": {"peripheral_id": peripheral_id, "value": value}}
        except (OSError, ValueError) as exc:
            return {"ok": False, "verified": False, "detail": str(exc),
                    "error_code": "io_error"}

    def write(self, peripheral_id: str, value: Any) -> Dict[str, Any]:
        path = self._pin_dir(peripheral_id)
        if path is None:
            return self._unknown(peripheral_id)
        level = 1 if str(value).lower() in ("1", "on", "high", "true") else 0
        try:
            (path / "direction").write_text("out")
            (path / "value").write_text(str(level))
            # sysfs can read an output pin back, so the write is genuinely verified
            observed = int((path / "value").read_text().strip())
            return {"ok": observed == level, "verified": observed == level,
                    "value": observed,
                    "detail": f"{peripheral_id} set to {level} (read back {observed})",
                    "data": {"peripheral_id": peripheral_id, "value": observed}}
        except OSError as exc:
            return {"ok": False, "verified": False, "detail": str(exc),
                    "error_code": "io_error"}


# --------------------------------------------------------------------------- USB relay
class SerialRelayProvider(PeripheralProvider):
    """USB relay boards driven over a serial line.

    Most hobby boards use a short ASCII/hex frame per channel. The frame template is a manifest
    concern (`on_frame`/`off_frame` with `{channel}`), so a new board is configuration, not code.
    """

    backend = PeripheralBackend.SERIAL.value

    def __init__(self, port: str = "", channels: int = 4, baudrate: int = 9600,
                 on_frame: str = "A0{channel:02d}01A1", off_frame: str = "A0{channel:02d}00A2"):
        self.port = port
        self.channels = channels
        self.baudrate = baudrate
        self.on_frame = on_frame
        self.off_frame = off_frame
        self._serial = None

    def available(self) -> bool:
        if not self.port:
            return False
        try:
            import serial                                   # noqa: F401
        except ImportError:
            return False
        return Path(self.port).exists() if os.name == "posix" else True

    def reason(self) -> str:
        if not self.port:
            return "no relay board port configured (devices.relay.port)"
        try:
            import serial                                   # noqa: F401
        except ImportError:
            return "pyserial is not installed"
        return f"{self.port} is not present"

    def _connection(self):
        if self._serial is not None:
            return self._serial
        import serial
        self._serial = serial.Serial(self.port, self.baudrate, timeout=2)
        return self._serial

    def list(self) -> List[PeripheralInfo]:
        if not self.available():
            return []
        return [PeripheralInfo(peripheral_id=f"relay{n}", kind=PeripheralKind.RELAY.value,
                               name=f"Relay {n}", pin=n, readable=False, writable=True,
                               board=f"serial:{self.port}")
                for n in range(1, self.channels + 1)]

    def _frame(self, channel: int, on: bool) -> bytes:
        template = self.on_frame if on else self.off_frame
        return bytes.fromhex(template.format(channel=channel))

    def write(self, peripheral_id: str, value: Any) -> Dict[str, Any]:
        if not peripheral_id.startswith("relay"):
            return self._unknown(peripheral_id)
        if not peripheral_id[5:].isdigit():
            return self._unknown(peripheral_id)
        channel = int(peripheral_id[5:])
        if not 1 <= channel <= self.channels:
            return self._unknown(peripheral_id)
        on = str(value).lower() in ("1", "on", "true", "high")
        try:
            self._connection().write(self._frame(channel, on))
        except Exception as exc:
            return {"ok": False, "verified": False, "detail": str(exc),
                    "error_code": "serial_error"}
        # These boards do not report state back, so the write cannot be verified.
        return {"ok": True, "verified": False,
                "detail": f"{peripheral_id} set to {'on' if on else 'off'} "
                          f"(board gives no read-back)",
                "data": {"peripheral_id": peripheral_id, "value": 1 if on else 0}}

    def read(self, peripheral_id: str) -> Dict[str, Any]:
        return {"ok": False, "verified": False, "error_code": "unsupported",
                "detail": "this relay board cannot report its state"}

    def pulse(self, peripheral_id: str, ms: int) -> Dict[str, Any]:
        ms = max(1, min(int(ms), MAX_PULSE_MS))
        on = self.write(peripheral_id, 1)
        if not on.get("ok"):
            return on
        time.sleep(ms / 1000.0)
        off = self.write(peripheral_id, 0)
        return {"ok": bool(off.get("ok")), "verified": False,
                "detail": f"{peripheral_id} pulsed for {ms}ms",
                "data": {"peripheral_id": peripheral_id, "pulse_ms": ms}}


# --------------------------------------------------------------------------- test backend
class MemoryBoardProvider(PeripheralProvider):
    """A virtual board used **only by tests**.

    It is a test double, not hardware, and is never selected automatically: it must be passed in
    explicitly. It exists so the peripheral *contract* (idempotence, bounds, verification
    honesty, audit) can be exercised without a Pi on the desk.
    """

    backend = PeripheralBackend.MEMORY.value

    def __init__(self, *, relays: int = 4, gpio_pins: Optional[List[int]] = None,
                 sensors: Optional[Dict[str, Dict[str, Any]]] = None):
        self._state: Dict[str, Any] = {}
        self._sensors = dict(sensors or {})
        self._info: List[PeripheralInfo] = []
        for n in range(1, relays + 1):
            self._info.append(PeripheralInfo(peripheral_id=f"relay{n}",
                                             kind=PeripheralKind.RELAY.value,
                                             name=f"Relay {n}", pin=n,
                                             writable=True, readable=True, board="memory"))
            self._state[f"relay{n}"] = 0
        for pin in (gpio_pins or []):
            self._info.append(PeripheralInfo(peripheral_id=f"gpio{pin}",
                                             kind=PeripheralKind.GPIO.value,
                                             name=f"GPIO {pin}", pin=pin,
                                             readable=True, writable=True, board="memory"))
            self._state[f"gpio{pin}"] = 0
        for sensor_id, spec in self._sensors.items():
            self._info.append(PeripheralInfo(
                peripheral_id=sensor_id, kind=PeripheralKind.SENSOR.value,
                name=spec.get("name", sensor_id), unit=spec.get("unit", ""),
                minimum=spec.get("min"), maximum=spec.get("max"),
                readable=True, writable=False, board="memory"))

    def available(self) -> bool:
        return True

    def reason(self) -> str:
        return "virtual test board (not hardware)"

    def list(self) -> List[PeripheralInfo]:
        return list(self._info)

    def read(self, peripheral_id: str) -> Dict[str, Any]:
        if peripheral_id in self._sensors:
            value = self._sensors[peripheral_id].get("value", 0)
            return {"ok": True, "verified": True, "value": value,
                    "detail": f"{peripheral_id} = {value}",
                    "data": {"peripheral_id": peripheral_id, "value": value,
                             "unit": self._sensors[peripheral_id].get("unit", "")}}
        if peripheral_id not in self._state:
            return self._unknown(peripheral_id)
        value = self._state[peripheral_id]
        return {"ok": True, "verified": True, "value": value,
                "detail": f"{peripheral_id} = {value}",
                "data": {"peripheral_id": peripheral_id, "value": value}}

    def write(self, peripheral_id: str, value: Any) -> Dict[str, Any]:
        if peripheral_id not in self._state:
            return self._unknown(peripheral_id)
        level = 1 if str(value).lower() in ("1", "on", "high", "true") else 0
        previous = self._state[peripheral_id]
        self._state[peripheral_id] = level
        return {"ok": True, "verified": True, "value": level,
                "detail": f"{peripheral_id} set to {level} (was {previous})",
                "data": {"peripheral_id": peripheral_id, "value": level,
                         "previous": previous, "changed": previous != level}}

    def pulse(self, peripheral_id: str, ms: int) -> Dict[str, Any]:
        ms = max(1, min(int(ms), MAX_PULSE_MS))
        on = self.write(peripheral_id, 1)
        if not on.get("ok"):
            return on
        time.sleep(ms / 1000.0)
        self.write(peripheral_id, 0)
        return {"ok": True, "verified": True, "detail": f"{peripheral_id} pulsed for {ms}ms",
                "data": {"peripheral_id": peripheral_id, "pulse_ms": ms}}


# --------------------------------------------------------------------------- selection
def select_provider(config: Optional[Dict[str, Any]] = None) -> PeripheralProvider:
    """Pick a backend from configuration, honestly falling back to "unavailable".

    Never selects the test backend: a virtual board must be requested explicitly, or a real
    deployment could silently believe it controlled hardware.
    """
    config = config or {}
    kind = str(config.get("backend", "auto")).lower()
    if kind == PeripheralBackend.MEMORY.value:
        return MemoryBoardProvider()
    if kind == PeripheralBackend.SERIAL.value or config.get("relay", {}).get("port"):
        return SerialRelayProvider(port=str(config.get("relay", {}).get("port", "")),
                                   channels=int(config.get("relay", {}).get("channels", 4)))
    if kind == PeripheralBackend.SYSFS.value:
        return SysfsGpioProvider()
    gpio = SysfsGpioProvider()
    if gpio.available():
        return gpio
    return NullProvider()


class NullProvider(PeripheralProvider):
    """No peripheral backend. Says so instead of pretending."""

    backend = PeripheralBackend.NONE.value

    def __init__(self, reason: str = ""):
        self._reason = reason or "no peripheral backend available on this host"

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        return self._reason


# --------------------------------------------------------------------------- capabilities
def peripheral_capabilities(provider: PeripheralProvider) -> List[str]:
    """The capabilities a node with this provider can honestly declare."""
    if not provider.available():
        return []
    caps: List[str] = []
    info = provider.list()
    if any(i.kind == PeripheralKind.GPIO.value for i in info):
        caps += ["gpio.read", "gpio.write"]
    if any(i.kind == PeripheralKind.RELAY.value for i in info):
        caps += ["relay.set", "relay.pulse", "relay.state"]
    if any(i.kind == PeripheralKind.SENSOR.value for i in info):
        caps += ["sensor.read", "sensor.list"]
    if info:
        caps.append("peripheral.list")
    return sorted(set(caps))


class PeripheralService:
    """Executes peripheral capabilities against a provider, with bounds and audit-friendly detail."""

    def __init__(self, provider: Optional[PeripheralProvider] = None):
        self.provider = provider or select_provider()

    def handle(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.provider.available():
            return {"ok": False, "verified": False, "error_code": "peripherals_unavailable",
                    "detail": self.provider.reason()}
        if capability == "peripheral.list":
            items = [i.to_dict() for i in self.provider.list()]
            return {"ok": True, "verified": True, "detail": f"{len(items)} peripheral(s)",
                    "data": {"peripherals": items, "backend": self.provider.backend}}
        if capability == "sensor.list":
            items = [i.to_dict() for i in self.provider.list()
                     if i.kind == PeripheralKind.SENSOR.value]
            return {"ok": True, "verified": True, "detail": f"{len(items)} sensor(s)",
                    "data": {"sensors": items}}
        if capability in ("gpio.read", "relay.state"):
            return self._with_backend(self.provider.read(str(params.get("peripheral_id", ""))))
        if capability == "sensor.read":
            return self._with_backend(self.provider.read(str(params.get("peripheral_id", ""))))
        if capability in ("gpio.write", "relay.set"):
            return self._with_backend(
                self.provider.write(str(params.get("peripheral_id", "")),
                                    params.get("value", params.get("state", 1))))
        if capability == "relay.pulse":
            ms = int(params.get("ms", 500))
            if ms > MAX_PULSE_MS:
                return {"ok": False, "verified": False, "error_code": "pulse_too_long",
                        "detail": f"a pulse longer than {MAX_PULSE_MS}ms is refused"}
            return self._with_backend(
                self.provider.pulse(str(params.get("peripheral_id", "")), ms))
        return {"ok": False, "verified": False, "error_code": "unsupported_capability",
                "detail": f"unknown peripheral capability {capability}"}

    def _with_backend(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(outcome.get("data") or {})
        data["backend"] = self.provider.backend
        return {**outcome, "data": data}

    def status(self) -> Dict[str, Any]:
        return {"available": self.provider.available(), "backend": self.provider.backend,
                "reason": self.provider.reason() if not self.provider.available() else "",
                "peripherals": [i.to_dict() for i in self.provider.list()],
                "capabilities": peripheral_capabilities(self.provider)}
