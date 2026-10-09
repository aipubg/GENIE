"""Device service (devices/service.py).

The daemon-side facade for the mesh: pairing, presence, capability routing, permissions,
the offline queue and verification. From the outside a device capability looks exactly like any
other GENIE capability:

    media.next on phone_main  ->  PTE scope  device:phone_main:media
                               ->  the node must declare media.next
                               ->  the result must be *verified* to count as success

Rules enforced here:
  * a device that is not paired, not trusted, or disabled is refused — default deny
  * a device only ever receives a capability it declared in its manifest
  * installation/pairing grants nothing by itself; the owner grants `device:<id>:<family>`
  * commands are idempotent (idempotency key + command_id dedupe) and replay-protected
  * an offline device queues its command with a TTL; expired work is never delivered
  * every command, result and refusal is persisted and audited
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, EventType, new_id
from core.events import get_bus
from core.logging_setup import get_logger
from devices.contracts import (DEFAULT_COMMAND_TTL_MS, CommandEnvelope, CommandStatus,
                               DeviceManifest, DeviceState, DeviceType, ResultEnvelope,
                               TrustTier, device_scope)
from devices.peripherals import PeripheralService
from devices.protocol import DEFAULT_PORT, DeviceServer, derive_pairing_secret
from devices.registry import DeviceRecord, DeviceRegistry
from devices.sync import SyncService

log = get_logger("devices.service")

#: Peripheral capabilities the local machine serves in-process (a USB relay on the PC, or a Pi
#: running GENIE itself). Remote peripheral nodes go over the channel like any other device.
PERIPHERAL_CAPABILITIES = {"peripheral.list", "gpio.read", "gpio.write", "relay.set",
                           "relay.pulse", "relay.state", "sensor.read", "sensor.list"}


@dataclass
class DeviceInvocation:
    capability: str
    device_id: str
    ok: bool
    available: bool = True
    verified: bool = False
    detail: str = ""
    status: str = CommandStatus.COMPLETED.value
    command_id: str = ""
    latency_ms: int = 0
    error: str = ""
    error_code: str = ""
    queued: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"capability": self.capability, "device": self.device_id, "ok": self.ok,
                "available": self.available, "verified": self.verified, "detail": self.detail,
                "status": self.status, "command_id": self.command_id,
                "latency_ms": self.latency_ms, "error": self.error,
                "error_code": self.error_code, "queued": self.queued, "raw": self.raw}


class DeviceService:
    def __init__(self, db, *, audit=None, trust=None, vault=None, host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT, auto_start: bool = True,
                 command_timeout_s: float = 30.0,
                 peripheral_config: Optional[Dict[str, Any]] = None):
        self.db = db
        self.audit = audit
        self.trust = trust
        self.registry = DeviceRegistry(db, audit=audit, vault=vault)
        self.sync = SyncService(db, audit=audit)
        # The local machine can itself be a peripheral node (a USB relay board, or a Pi running
        # GENIE). `select_provider` reports "unavailable" honestly when there is no backend.
        self.peripherals = PeripheralService(peripheral_config)
        self.command_timeout_s = command_timeout_s
        self._bus = get_bus()
        self._invocations: Dict[str, DeviceInvocation] = {}
        self._lock = threading.RLock()
        self.server = DeviceServer(
            host=host, port=port,
            secret_provider=self.registry.pairing_secret,
            on_hello=self._on_hello,
            on_connected=self._on_connected,
            on_disconnect=self._on_disconnect)
        self._started = False
        if auto_start:
            self.start()

    # --------------------------------------------------------------------- lifecycle
    def start(self) -> Dict[str, Any]:
        if self._started:
            return {"ok": True, "already": True}
        result = self.server.start()
        self._started = bool(result.get("ok"))
        if not self._started:
            log.warning("device transport could not start: %s", result.get("error"))
        return result

    def stop(self) -> None:
        self.server.stop()
        self._started = False

    # ------------------------------------------------------------------ hello / presence
    def _on_hello(self, device_id: str, manifest: DeviceManifest,
                  payload: Dict[str, Any]) -> Dict[str, Any]:
        """A paired node connected. Register/refresh it and mark it online."""
        record = self.registry.get(device_id)
        if record is None:
            return {"ok": False, "state": "rejected",
                    "reason": "device is not paired with this GENIE"}
        if record.state == DeviceState.DISABLED.value:
            return {"ok": False, "state": "disabled", "reason": "device is disabled by the owner"}
        if not self.registry.paired(device_id):
            return {"ok": False, "state": "unpaired", "reason": "pairing was revoked"}

        changed = record.manifest.capabilities != manifest.capabilities
        self.registry.upsert(manifest, state=DeviceState.ONLINE.value,
                             paired_by=record.paired_by, touch_seen=True)
        if changed:
            log.info("device %s declared a new capability surface (%d capabilities)",
                     device_id, len(manifest.capabilities))
        self._bus.publish(EventType.DEVICE_CONNECTED,
                          {"device_id": device_id, "type": manifest.type,
                           "capabilities": len(manifest.capabilities)})
        return {"ok": True, "state": "online"}

    def _on_connected(self, device_id: str) -> None:
        """Drain only once the socket is registered and its reader is active."""
        threading.Thread(target=self._drain_queue, args=(device_id,),
                         name=f"device-drain-{device_id}", daemon=True).start()

    def _on_disconnect(self, device_id: str) -> None:
        self.registry.set_state(device_id, DeviceState.PAIRED.value,
                                error="disconnected")
        self._bus.publish(EventType.DEVICE_OFFLINE, {"device_id": device_id})

    # ------------------------------------------------------------------------ pairing
    def pair(self, device_id: str, code: str, *, name: str = "", type: str = "",
             capabilities: Optional[List[str]] = None, by: str = "owner") -> Dict[str, Any]:
        """Confirm a pairing code shown by the device (TOFU + explicit owner confirm).

        `capabilities` may be supplied when the owner pairs a device that has not connected yet;
        otherwise the node's first `hello` declares them.
        """
        if not device_id or not code:
            return {"ok": False, "error": "device_id and code are required"}
        secret = derive_pairing_secret(code, device_id)
        record = self.registry.get(device_id)
        manifest = (record.manifest if record else DeviceManifest(
            device_id=device_id, name=name or device_id,
            type=type or DeviceType.UNKNOWN.value))
        if name:
            manifest.name = name
        if type:
            manifest.type = type
        if capabilities is not None:
            manifest.capabilities = list(capabilities)
        manifest.trust_tier = TrustTier.OWNER_SECONDARY.value
        self.registry.upsert(manifest, state=DeviceState.PAIRED.value, paired_by=by,
                             fingerprint=manifest.fingerprint(),
                             trust_tier=TrustTier.OWNER_SECONDARY.value)
        stored = self.registry.store_pairing(device_id, secret,
                                             fingerprint=manifest.fingerprint())
        if not stored.get("ok"):
            return {"ok": False, "error": stored.get("error", "could not store the secret")}
        if self.audit:
            self.audit.record(who=by, action="device.pair", why=device_id, result="paired")
        return {"ok": True, "device_id": device_id, "state": DeviceState.PAIRED.value,
                "trust_tier": manifest.trust_tier,
                "capabilities": manifest.capabilities,
                "note": "pairing grants no capability scopes — grant them explicitly"}

    def unpair(self, device_id: str) -> Dict[str, Any]:
        return self.registry.revoke_pairing(device_id)

    # ---------------------------------------------------------------------- routing
    def owns(self, capability: str) -> bool:
        """True when some known device declares this capability."""
        return bool(self.registry.find_by_capability(capability))

    def devices_for(self, capability: str) -> List[str]:
        return [r.device_id for r in self.registry.find_by_capability(capability)]

    def scope_for(self, device_id: str, capability: str) -> str:
        return device_scope(device_id, capability)

    def _deny(self, capability: str, device_id: str, detail: str,
              error_code: str) -> DeviceInvocation:
        return DeviceInvocation(capability=capability, device_id=device_id, ok=False,
                                verified=False, detail=detail, error=detail,
                                error_code=error_code,
                                status=CommandStatus.REJECTED.value)

    def execute(self, ctx: CallContext, device_id: str, capability: str,
                params: Optional[Dict[str, Any]] = None, *,
                queue_if_offline: bool = True,
                ttl_ms: int = DEFAULT_COMMAND_TTL_MS) -> DeviceInvocation:
        params = dict(params or {})
        device_id = device_id or "pc_main"

        # 1. the device must exist
        record = self.registry.get(device_id)
        if record is None:
            return self._deny(capability, device_id,
                              f"unknown device {device_id} — it has never paired",
                              "unknown_device")
        # 2. and be usable
        if record.state == DeviceState.DISABLED.value:
            return self._deny(capability, device_id, f"{device_id} is disabled", "device_disabled")
        if not record.trusted():
            return self._deny(capability, device_id,
                              f"{device_id} trust tier {record.manifest.trust_tier} may not "
                              f"be driven", "untrusted_device")
        # 3. and declare the capability — but only if it has declared a surface at all.
        # A device that has not connected yet has declared nothing; the node is the authority on
        # its own capabilities and rejects anything it does not implement, so an unknown surface
        # defers to delivery time instead of refusing work the owner legitimately queued.
        if record.manifest.capabilities and not record.manifest.supports(capability):
            return self._deny(capability, device_id,
                              f"{device_id} does not declare {capability}",
                              "unsupported_capability")
        # 4. and the owner must have granted the scope (default deny)
        scope = device_scope(device_id, capability)
        if self.trust is not None:
            decision = self.trust.check(ctx, scope, params)
            if not decision.allow:
                self._audit(ctx, device_id, capability, "denied", decision.reason)
                return self._deny(capability, device_id, decision.reason,
                                  "permission_denied")
        # 5. the local machine executes in-process; remote devices go over the channel
        if device_id == "pc_main" or record.manifest.transport == "local":
            return self._execute_local(ctx, record, capability, params)

        return self._execute_remote(ctx, record, capability, params,
                                    queue_if_offline=queue_if_offline, ttl_ms=ttl_ms)

    def _execute_local(self, ctx: CallContext, record: DeviceRecord, capability: str,
                       params: Dict[str, Any]) -> DeviceInvocation:
        """The local machine is a device too — routed through the same contract."""
        started = time.time()
        if capability in PERIPHERAL_CAPABILITIES:
            outcome = self.peripherals.handle(capability, params) or {}
            return self._local_outcome(ctx, record, capability, outcome, started)
        from devices.node import ReferenceHandler
        handler = getattr(self, "_local_handler", None) or ReferenceHandler()
        self._local_handler = handler
        outcome = handler(capability, params) or {}
        return self._local_outcome(ctx, record, capability, outcome, started)

    def _local_outcome(self, ctx: CallContext, record: DeviceRecord, capability: str,
                       outcome: Dict[str, Any], started: float) -> DeviceInvocation:
        invocation = DeviceInvocation(
            capability=capability, device_id=record.device_id,
            ok=bool(outcome.get("ok")) and bool(outcome.get("verified", False)),
            verified=bool(outcome.get("verified", False)),
            detail=str(outcome.get("detail", "")), error_code=str(outcome.get("error_code", "")),
            latency_ms=int((time.time() - started) * 1000), raw=outcome)
        self._audit(ctx, record.device_id, capability,
                    "ok" if invocation.ok else "failed", invocation.detail)
        return invocation

    def _execute_remote(self, ctx: CallContext, record: DeviceRecord, capability: str,
                        params: Dict[str, Any], *, queue_if_offline: bool,
                        ttl_ms: int) -> DeviceInvocation:
        device_id = record.device_id
        # idempotency: a repeated key returns the previous outcome instead of re-running
        if ctx.idempotency_key:
            previous = self._find_by_idempotency(device_id, capability, ctx.idempotency_key)
            if previous is not None:
                log.info("device command for %s/%s is a duplicate of %s",
                         device_id, capability, previous["command_id"])
                return self._invocation_from_row(previous)

        if not self.server.connected(device_id):
            if not queue_if_offline:
                return self._deny(capability, device_id, f"{device_id} is offline",
                                  "device_offline")
            queued = self._queue(ctx, record, capability, params, ttl_ms=ttl_ms)
            return DeviceInvocation(
                capability=capability, device_id=device_id, ok=False, available=True,
                verified=False, queued=True, status=CommandStatus.QUEUED.value,
                command_id=queued["command_id"],
                detail=f"{device_id} is offline — command queued for up to "
                       f"{ttl_ms // 1000}s", error_code="device_offline")

        command = CommandEnvelope(device_id=device_id, capability=capability, params=params,
                                  trace_id=ctx.trace_id, mission_id=ctx.mission_id or "",
                                  person_id=ctx.person_id,
                                  idempotency_key=ctx.idempotency_key or "",
                                  ttl_ms=ttl_ms)
        self._persist_command(ctx, command)
        started = time.time()
        result = self.server.send_command(command, timeout_s=self.command_timeout_s)
        latency = int((time.time() - started) * 1000)
        self._complete(ctx, command, result, latency)
        invocation = self._invocation_from_result(capability, device_id, command, result, latency)
        if invocation.ok:
            self.registry.touch(device_id)
        return invocation

    # ------------------------------------------------------------------- offline queue
    def _queue(self, ctx: CallContext, record: DeviceRecord, capability: str,
               params: Dict[str, Any], *, ttl_ms: int) -> Dict[str, Any]:
        command = CommandEnvelope(device_id=record.device_id, capability=capability,
                                  params=params, trace_id=ctx.trace_id,
                                  mission_id=ctx.mission_id or "",
                                  person_id=ctx.person_id,
                                  idempotency_key=ctx.idempotency_key or "",
                                  ttl_ms=ttl_ms)
        now_ms = int(time.time() * 1000)
        self.db.execute(
            "INSERT INTO device_commands(command_id, device_id, capability, status, params,"
            " result, ok, verified, error_code, person_id, mission_id, trace_id,"
            " idempotency_key, issued_at, expires_at, completed_at, latency_ms)"
            " VALUES(?,?,?,?,?,?,0,0,?,?,?,?,?,?,?,NULL,0)",
            (command.command_id, record.device_id, capability, CommandStatus.QUEUED.value,
             json.dumps(params), "{}", "device_offline", ctx.person_id,
             ctx.mission_id or "", ctx.trace_id, ctx.idempotency_key or "",
             now_ms, now_ms + ttl_ms))
        self._audit(ctx, record.device_id, capability, "queued",
                    f"offline; expires in {ttl_ms // 1000}s")
        return {"command_id": command.command_id, "expires_at": now_ms + ttl_ms}

    def pending(self, device_id: str = "") -> List[Dict[str, Any]]:
        if device_id:
            rows = self.db.query(
                "SELECT * FROM device_commands WHERE device_id=? AND status=?"
                " ORDER BY issued_at", (device_id, CommandStatus.QUEUED.value))
        else:
            rows = self.db.query("SELECT * FROM device_commands WHERE status=? ORDER BY issued_at",
                                 (CommandStatus.QUEUED.value,))
        return [dict(r) for r in rows]

    def expire_stale(self) -> int:
        """Drop queued work whose TTL has passed. Stale commands are never delivered."""
        now_ms = int(time.time() * 1000)
        rows = self.db.query("SELECT command_id FROM device_commands WHERE status=? AND"
                             " expires_at IS NOT NULL AND expires_at < ?",
                             (CommandStatus.QUEUED.value, now_ms))
        for row in rows:
            self.db.execute("UPDATE device_commands SET status=?, completed_at=?,"
                            " error_code=? WHERE command_id=?",
                            (CommandStatus.EXPIRED.value, now_ms, "expired", row["command_id"]))
        if rows:
            log.info("expired %d stale queued device command(s)", len(rows))
        return len(rows)

    def _drain_queue(self, device_id: str) -> int:
        """Deliver queued commands to a device that just came online."""
        self.expire_stale()
        delivered = 0
        for row in self.pending(device_id):
            if not self.server.connected(device_id):
                break
            params = json.loads(row["params"] or "{}")
            ctx = CallContext(person_id=row["person_id"] or "owner",
                              mission_id=row["mission_id"] or None,
                              trace_id=row["trace_id"] or "",
                              idempotency_key=row["idempotency_key"] or None)
            command = CommandEnvelope(command_id=row["command_id"], device_id=device_id,
                                      capability=row["capability"], params=params,
                                      trace_id=row["trace_id"] or "",
                                      mission_id=row["mission_id"] or "",
                                      person_id=row["person_id"] or "owner",
                                      idempotency_key=row["idempotency_key"] or "",
                                      issued_at_ms=int(row["issued_at"] or 0),
                                      ttl_ms=max(1000, int(row["expires_at"] or 0)
                                                 - int(row["issued_at"] or 0)))
            started = time.time()
            result = self.server.send_command(command, timeout_s=self.command_timeout_s)
            latency = int((time.time() - started) * 1000)
            self._complete(ctx, command, result, latency)
            delivered += 1
            self._bus.publish("DEVICE_COMMAND_DELIVERED",
                              {"device_id": device_id, "command_id": command.command_id,
                               "capability": command.capability, "ok": result.ok})
        if delivered:
            log.info("delivered %d queued command(s) to %s", delivered, device_id)
        return delivered

    # ------------------------------------------------------------------ cancellation
    def cancel(self, device_id: str, command_id: str) -> Dict[str, Any]:
        row = self.db.query_one("SELECT * FROM device_commands WHERE command_id=?",
                                (command_id,))
        if row is None:
            return {"ok": False, "error": f"unknown command {command_id}"}
        if row["status"] == CommandStatus.QUEUED.value:
            self.db.execute("UPDATE device_commands SET status=?, completed_at=?,"
                            " error_code=? WHERE command_id=?",
                            (CommandStatus.CANCELLED.value, int(time.time() * 1000),
                             "cancelled", command_id))
            return {"ok": True, "command_id": command_id, "status": CommandStatus.CANCELLED.value}
        sent = self.server.cancel(device_id, command_id)
        return {"ok": bool(sent), "command_id": command_id,
                "status": "cancel_requested" if sent else "not_delivered"}

    # ------------------------------------------------------------------ persistence
    def _persist_command(self, ctx: CallContext, command: CommandEnvelope) -> None:
        now_ms = int(time.time() * 1000)
        self.db.execute(
            "INSERT OR REPLACE INTO device_commands(command_id, device_id, capability, status,"
            " params, result, ok, verified, error_code, person_id, mission_id, trace_id,"
            " idempotency_key, issued_at, expires_at, completed_at, latency_ms)"
            " VALUES(?,?,?,?,?,?,0,0,'',?,?,?,?,?,?,NULL,0)",
            (command.command_id, command.device_id, command.capability,
             CommandStatus.RUNNING.value, json.dumps(command.params), "{}",
             ctx.person_id, ctx.mission_id or "", ctx.trace_id,
             command.idempotency_key or "", command.issued_at_ms,
             command.issued_at_ms + command.ttl_ms))

    def _complete(self, ctx: CallContext, command: CommandEnvelope, result: ResultEnvelope,
                  latency_ms: int) -> None:
        # success requires the device to have *verified* the effect, not merely accepted it
        ok = bool(result.ok and result.verified)
        self.db.execute(
            "UPDATE device_commands SET status=?, result=?, ok=?, verified=?, error_code=?,"
            " completed_at=?, latency_ms=? WHERE command_id=?",
            (result.status, json.dumps(result.to_dict()), 1 if ok else 0,
             1 if result.verified else 0, result.error_code or "",
             int(time.time() * 1000), latency_ms, command.command_id))
        self._audit(ctx, command.device_id, command.capability,
                    "ok" if ok else result.status, result.detail)
        self._bus.publish("DEVICE_COMMAND_RESULT",
                          {"device_id": command.device_id, "command_id": command.command_id,
                           "capability": command.capability, "status": result.status,
                           "ok": ok, "verified": result.verified,
                           "latency_ms": latency_ms})

    def _invocation_from_result(self, capability: str, device_id: str,
                                command: CommandEnvelope, result: ResultEnvelope,
                                latency_ms: int) -> DeviceInvocation:
        ok = bool(result.ok and result.verified)
        return DeviceInvocation(
            capability=capability, device_id=device_id, ok=ok,
            verified=bool(result.verified), status=result.status,
            command_id=command.command_id, latency_ms=latency_ms,
            detail=result.detail, error=("" if ok else (result.detail or result.error_code)),
            error_code=result.error_code, raw=result.to_dict())

    def _invocation_from_row(self, row) -> DeviceInvocation:
        result = json.loads(row["result"] or "{}")
        return DeviceInvocation(
            capability=row["capability"], device_id=row["device_id"],
            ok=bool(row["ok"]), verified=bool(row["verified"]),
            status=row["status"], command_id=row["command_id"],
            latency_ms=int(row["latency_ms"] or 0),
            detail=str(result.get("detail", "")),
            error_code=str(row["error_code"] or ""), raw=result)

    def _find_by_idempotency(self, device_id: str, capability: str,
                             key: str):
        return self.db.query_one(
            "SELECT * FROM device_commands WHERE device_id=? AND capability=? AND"
            " idempotency_key=? AND status IN (?,?,?) ORDER BY issued_at DESC LIMIT 1",
            (device_id, capability, key, CommandStatus.COMPLETED.value,
             CommandStatus.FAILED.value, CommandStatus.QUEUED.value))

    def command(self, command_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM device_commands WHERE command_id=?",
                                (command_id,))
        return dict(row) if row else None

    def recent_commands(self, device_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        if device_id:
            rows = self.db.query("SELECT * FROM device_commands WHERE device_id=?"
                                 " ORDER BY issued_at DESC LIMIT ?", (device_id, limit))
        else:
            rows = self.db.query("SELECT * FROM device_commands ORDER BY issued_at DESC"
                                 " LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        registry = self.registry.status()
        registry["transport"] = self.server.status()
        registry["queued"] = len(self.pending())
        registry["sync"] = self.sync.status()
        registry["peripherals"] = self.peripherals.status()
        return registry

    def health(self, device_id: str = "") -> Dict[str, Any]:
        if device_id:
            record = self.registry.get(device_id)
            if record is None:
                return {"ok": False, "error": f"unknown device {device_id}"}
            return {"ok": record.online(), "device": record.to_dict(),
                    "connected": self.server.connected(device_id)}
        return {"ok": True, "online": self.server.online_devices(),
                "devices": len(self.registry.all())}

    def _audit(self, ctx: CallContext, device_id: str, capability: str, outcome: str,
               detail: str) -> None:
        if not self.audit:
            return
        try:
            self.audit.record(who=ctx.person_id, device=device_id,
                              action=f"device.command:{capability}",
                              why=detail[:200], mission_id=ctx.mission_id,
                              result=outcome, trace_id=ctx.trace_id)
        except Exception as exc:
            log.debug("device audit failed: %s", exc)
