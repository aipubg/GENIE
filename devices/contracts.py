"""Device contract (devices/contracts.py) — master spec §6, docs/DEVICES.md.

One device, one manifest. GENIE is device-agnostic: it says `media.next(phone_main)` and the
platform adapter does the platform work. There is no separate architecture per platform.

This module owns the *shapes* only: what a device is, what it can do, and what a command/result
looks like on the wire. Nothing here opens sockets or touches the database.

Wire rules that the shapes enforce:
  * every command carries a `command_id` (dedupe), a monotonic `counter` (replay protection)
    and an `idempotency_key` (safe retry)
  * every command has a TTL, so an offline queue cannot deliver stale work forever
  * a result says whether the action was **verified**, never merely "accepted"
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import new_id

DEVICE_PROTOCOL_VERSION = "1.0"

#: Used only for the pre-pairing handshake (`hello` before pairing, `pair_required`).
#: There is no shared secret yet, so both sides use this well-known value; it authenticates
#: nothing and grants nothing. The moment a device is paired, its PBKDF2 secret is used instead.
UNPAIRED_SECRET = "genie-unpaired-handshake"

#: How long a queued command stays deliverable while its device is offline.
DEFAULT_COMMAND_TTL_MS = 10 * 60 * 1000

#: A device that misses this many heartbeats is considered offline.
HEARTBEAT_TIMEOUT_MS = 90 * 1000


class DeviceType(str, enum.Enum):
    PC = "pc"
    LAPTOP = "laptop"
    ANDROID = "android"
    RASPBERRY_PI = "raspberry_pi"
    CAMERA = "camera"
    HOME = "home"
    VIRTUAL = "virtual"          # a software node (used by tests and the reference node)
    UNKNOWN = "unknown"


class TrustTier(str, enum.Enum):
    """How much a device is trusted. Pairing decides this; it is never inferred."""

    OWNER_PRIMARY = "owner_primary"
    OWNER_SECONDARY = "owner_secondary"
    FAMILY = "family"
    GUEST = "guest"
    UNTRUSTED = "untrusted"


class DeviceState(str, enum.Enum):
    UNPAIRED = "unpaired"
    PAIRED = "paired"            # paired but not currently connected
    ONLINE = "online"
    SUSPENDED = "suspended"      # connected but commands are held
    DISABLED = "disabled"        # no commands at all


class CommandStatus(str, enum.Enum):
    QUEUED = "queued"            # waiting for an offline device
    ACCEPTED = "accepted"        # device acked, not finished
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"        # device refused (permission / unsupported)
    CANCELLED = "cancelled"
    EXPIRED = "expired"          # TTL passed before delivery


#: A result is only "ok" for these statuses.
TERMINAL_STATUSES = {CommandStatus.COMPLETED.value, CommandStatus.FAILED.value,
                     CommandStatus.REJECTED.value, CommandStatus.CANCELLED.value,
                     CommandStatus.EXPIRED.value}

#: Trust tiers allowed to drive a device at all.
TRUSTED_TIERS = {TrustTier.OWNER_PRIMARY.value, TrustTier.OWNER_SECONDARY.value,
                 TrustTier.FAMILY.value}

#: Tier ranking, so a policy can require "at least owner_secondary".
TIER_RANK = {TrustTier.UNTRUSTED.value: 0, TrustTier.GUEST.value: 1,
             TrustTier.FAMILY.value: 2, TrustTier.OWNER_SECONDARY.value: 3,
             TrustTier.OWNER_PRIMARY.value: 4}


def canonical_capability(capability: str) -> str:
    """Device capabilities are lowercase dotted names: `media.next`, `screen.observe`."""
    return (capability or "").strip().lower()


def device_scope(device_id: str, capability: str) -> str:
    """The PTE scope for driving a capability on a device.

    Mirrors the plugin design: `device:<device_id>:<capability family>`.
    """
    family = canonical_capability(capability).split(".")[0] or "unknown"
    return f"device:{device_id}:{family}"


# --------------------------------------------------------------------------- manifest
@dataclass
class DeviceManifest:
    """What a device is and what it can do. Produced by the device itself at pair time."""

    device_id: str
    name: str = ""
    type: str = DeviceType.UNKNOWN.value
    trust_tier: str = TrustTier.UNTRUSTED.value
    capabilities: List[str] = field(default_factory=list)
    protocol_version: str = DEVICE_PROTOCOL_VERSION
    platform: str = ""                     # "windows" | "android" | "linux"
    app_version: str = ""
    node_id: str = ""                      # stable per installation
    transport: str = "tcp"                 # tcp | relay
    address: str = ""                      # host:port as last seen
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalised(self) -> "DeviceManifest":
        self.capabilities = sorted({canonical_capability(c) for c in self.capabilities if c})
        self.type = str(self.type or DeviceType.UNKNOWN.value)
        return self

    def supports(self, capability: str) -> bool:
        return canonical_capability(capability) in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {"device_id": self.device_id, "name": self.name, "type": self.type,
                "trust_tier": self.trust_tier, "capabilities": self.capabilities,
                "protocol_version": self.protocol_version, "platform": self.platform,
                "app_version": self.app_version, "node_id": self.node_id,
                "transport": self.transport, "address": self.address,
                "metadata": self.metadata}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DeviceManifest":
        return cls(
            device_id=str(raw.get("device_id", "")),
            name=str(raw.get("name", "")),
            type=str(raw.get("type", DeviceType.UNKNOWN.value)),
            trust_tier=str(raw.get("trust_tier", TrustTier.UNTRUSTED.value)),
            capabilities=list(raw.get("capabilities") or []),
            protocol_version=str(raw.get("protocol_version", DEVICE_PROTOCOL_VERSION)),
            platform=str(raw.get("platform", "")),
            app_version=str(raw.get("app_version", "")),
            node_id=str(raw.get("node_id", "")),
            transport=str(raw.get("transport", "tcp")),
            address=str(raw.get("address", "")),
            metadata=dict(raw.get("metadata") or {}),
        ).normalised()

    def fingerprint(self) -> str:
        """Stable hash of the capability surface — lets the mesh notice a changed device."""
        payload = json.dumps({"id": self.device_id, "node": self.node_id,
                              "caps": self.capabilities, "platform": self.platform},
                             sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- envelopes
@dataclass
class CommandEnvelope:
    """A command the daemon sends to a device."""

    command_id: str = field(default_factory=lambda: new_id("cmd"))
    device_id: str = ""
    capability: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    mission_id: str = ""
    person_id: str = "owner"
    idempotency_key: str = ""
    counter: int = 0                       # monotonic per connection (replay protection)
    issued_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    ttl_ms: int = DEFAULT_COMMAND_TTL_MS
    cancel: bool = False                   # a cancellation for an earlier command_id

    def expired(self, now_ms: Optional[int] = None) -> bool:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        return (now - self.issued_at_ms) > self.ttl_ms

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "command", "protocol": DEVICE_PROTOCOL_VERSION,
                "command_id": self.command_id, "device_id": self.device_id,
                "capability": self.capability, "params": self.params,
                "trace_id": self.trace_id, "mission_id": self.mission_id,
                "person_id": self.person_id, "idempotency_key": self.idempotency_key,
                "counter": self.counter, "issued_at_ms": self.issued_at_ms,
                "ttl_ms": self.ttl_ms, "cancel": self.cancel}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CommandEnvelope":
        return cls(
            command_id=str(raw.get("command_id", "")),
            device_id=str(raw.get("device_id", "")),
            capability=str(raw.get("capability", "")),
            params=dict(raw.get("params") or {}),
            trace_id=str(raw.get("trace_id", "")),
            mission_id=str(raw.get("mission_id", "")),
            person_id=str(raw.get("person_id", "owner")),
            idempotency_key=str(raw.get("idempotency_key", "")),
            counter=int(raw.get("counter", 0) or 0),
            issued_at_ms=int(raw.get("issued_at_ms", 0) or 0),
            ttl_ms=int(raw.get("ttl_ms", DEFAULT_COMMAND_TTL_MS) or DEFAULT_COMMAND_TTL_MS),
            cancel=bool(raw.get("cancel", False)),
        )


@dataclass
class ResultEnvelope:
    """What a device reports back. `verified` is the only claim that counts."""

    command_id: str = ""
    device_id: str = ""
    status: str = CommandStatus.ACCEPTED.value
    ok: bool = False
    verified: bool = False
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    latency_ms: int = 0
    counter: int = 0
    at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "result", "protocol": DEVICE_PROTOCOL_VERSION,
                "command_id": self.command_id, "device_id": self.device_id,
                "status": self.status, "ok": self.ok, "verified": self.verified,
                "detail": self.detail, "data": self.data, "error_code": self.error_code,
                "latency_ms": self.latency_ms, "counter": self.counter, "at_ms": self.at_ms}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ResultEnvelope":
        return cls(
            command_id=str(raw.get("command_id", "")),
            device_id=str(raw.get("device_id", "")),
            status=str(raw.get("status", CommandStatus.ACCEPTED.value)),
            ok=bool(raw.get("ok", False)),
            verified=bool(raw.get("verified", False)),
            detail=str(raw.get("detail", "")),
            data=dict(raw.get("data") or {}),
            error_code=str(raw.get("error_code", "")),
            latency_ms=int(raw.get("latency_ms", 0) or 0),
            counter=int(raw.get("counter", 0) or 0),
            at_ms=int(raw.get("at_ms", 0) or 0),
        )


# --------------------------------------------------------------------------- signing
def sign_payload(payload: Dict[str, Any], secret: str) -> str:
    """HMAC over the canonical JSON of a frame.

    Every frame on the device channel is signed with the pairing secret, so a node that never
    paired cannot inject commands and a tampered frame is rejected.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(payload: Dict[str, Any], secret: str, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, str(signature))


def encode_frame(payload: Dict[str, Any], secret: str) -> bytes:
    """One frame = one line of JSON with a detached signature (line-delimited protocol)."""
    frame = {"payload": payload, "sig": sign_payload(payload, secret)}
    return (json.dumps(frame, default=str) + "\n").encode("utf-8")


def decode_frame(line: bytes, secret: str) -> Dict[str, Any]:
    """Parse + verify a frame. Raises ValueError on anything untrusted."""
    text = line.decode("utf-8").strip() if isinstance(line, (bytes, bytearray)) else str(line).strip()
    if not text:
        raise ValueError("empty frame")
    try:
        frame = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed frame: {exc}") from exc
    if not isinstance(frame, dict) or "payload" not in frame or "sig" not in frame:
        raise ValueError("frame missing payload/signature")
    if not verify_signature(frame["payload"], secret, frame["sig"]):
        raise ValueError("signature mismatch")
    return frame["payload"]


class ReplayWindow:
    """Monotonic counter + command_id dedupe (master spec §6 replay protection).

    A replayed frame is rejected; a retried command_id is answered from the dedupe cache
    instead of being executed twice.
    """

    def __init__(self, window: int = 512):
        self.window = window
        self._highest_counter = 0
        self._seen_commands: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []

    def accept_counter(self, counter: int) -> bool:
        """True when the counter advances. Equal/older counters are replays."""
        counter = int(counter or 0)
        if counter <= self._highest_counter:
            return False
        self._highest_counter = counter
        return True

    def seen(self, command_id: str) -> Optional[Dict[str, Any]]:
        return self._seen_commands.get(command_id)

    def remember(self, command_id: str, result: Dict[str, Any]) -> None:
        if not command_id:
            return
        if command_id not in self._seen_commands:
            self._order.append(command_id)
        self._seen_commands[command_id] = result
        while len(self._order) > self.window:
            self._seen_commands.pop(self._order.pop(0), None)

    def forget(self, command_id: str) -> None:
        self._seen_commands.pop(command_id, None)

    @property
    def highest_counter(self) -> int:
        return self._highest_counter

    def reset(self) -> None:
        self._highest_counter = 0
        self._seen_commands.clear()
        self._order.clear()
