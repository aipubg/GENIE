"""Device registry (devices/registry.py).

The authoritative list of devices: what exists, what it can do, how much it is trusted, and
whether it is reachable. `KNOWN_DEVICES` in the tool catalogue is only a *hint* for the router;
this registry is the truth (the same rule as "NEDLE2 decides, services own truth").

Pairing secrets never live here — only a vault reference plus a fingerprint, so a database copy
cannot be used to impersonate a device.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.contracts import new_id
from core.logging_setup import get_logger
from devices.contracts import (HEARTBEAT_TIMEOUT_MS, TRUSTED_TIERS, DeviceManifest, DeviceState,
                               DeviceType, TrustTier, canonical_capability)

log = get_logger("devices.registry")


@dataclass
class DeviceRecord:
    manifest: DeviceManifest
    state: str = DeviceState.UNPAIRED.value
    address: str = ""
    fingerprint: str = ""
    paired_at: int = 0
    paired_by: str = ""
    last_seen: int = 0
    last_error: str = ""
    created_at: int = 0
    updated_at: int = 0

    @property
    def device_id(self) -> str:
        return self.manifest.device_id

    def trusted(self) -> bool:
        return (self.state != DeviceState.DISABLED.value
                and self.manifest.trust_tier in TRUSTED_TIERS)

    def online(self, now_ms: Optional[int] = None) -> bool:
        if self.state != DeviceState.ONLINE.value:
            return False
        # The local machine is this process: it cannot be "unreachable", so it does not depend
        # on a heartbeat that nothing sends. Without this it reported itself offline and made
        # the mesh summary contradict the per-device line.
        if self.manifest.transport == "local":
            return True
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        return (now - self.last_seen) <= HEARTBEAT_TIMEOUT_MS

    def to_dict(self) -> Dict[str, Any]:
        payload = self.manifest.to_dict()
        payload.update({"state": self.state, "address": self.address,
                        "fingerprint": self.fingerprint, "paired_at": self.paired_at,
                        "paired_by": self.paired_by, "last_seen": self.last_seen,
                        "last_error": self.last_error, "trusted": self.trusted(),
                        "online": self.online()})
        return payload


class DeviceRegistry:
    def __init__(self, db, audit=None, vault=None):
        self.db = db
        self.audit = audit
        self.vault = vault
        self._ensure_local_pc()

    # ------------------------------------------------------------------- bootstrap
    def _ensure_local_pc(self) -> None:
        """The machine GENIE runs on is always a device, and is always paired."""
        if self.get("pc_main") is not None:
            return
        manifest = DeviceManifest(
            device_id="pc_main", name="Main PC", type=DeviceType.PC.value,
            trust_tier=TrustTier.OWNER_PRIMARY.value, platform="windows",
            capabilities=["application.open", "application.close", "window.list",
                          "window.focus", "window.move", "input.type_text", "input.key",
                          "input.hotkey", "files.read", "files.write", "files.list",
                          "system.volume.set", "system.volume.up", "system.volume.down",
                          "system.volume.mute", "system.audio.state", "clipboard.get",
                          "clipboard.set", "screen.capture", "processes.list", "shell.run",
                          "uia.find", "uia.invoke", "uia.set_value", "browser.navigate",
                          "skill.execute", "skill.search", "media.play", "media.pause",
                          "media.next", "media.previous"],
            node_id="local", transport="local").normalised()
        self.upsert(manifest, state=DeviceState.ONLINE.value, paired_by="system",
                    fingerprint=manifest.fingerprint())

    # ------------------------------------------------------------------------ write
    def upsert(self, manifest: DeviceManifest, *, state: Optional[str] = None,
               address: str = "", paired_by: str = "", fingerprint: str = "",
               touch_seen: bool = False,
               trust_tier: Optional[str] = None) -> DeviceRecord:
        """Register or refresh a device.

        A device's own manifest never decides its trust. `hello` carries whatever the node
        believes, so accepting `manifest.trust_tier` would let any node promote itself simply by
        reconnecting. The stored tier is therefore authoritative and only an explicit
        `trust_tier=` (owner action) or `set_trust()` may change it.
        """
        manifest = manifest.normalised()
        existing = self.get(manifest.device_id)
        now_ms = int(time.time() * 1000)
        if existing is not None:
            record = existing
            if trust_tier:
                manifest.trust_tier = trust_tier
            else:
                manifest.trust_tier = record.manifest.trust_tier
            # keep what the owner knows when the node does not report it
            if not manifest.name:
                manifest.name = record.manifest.name
            if manifest.type in ("", DeviceType.UNKNOWN.value):
                manifest.type = record.manifest.type
            record.manifest = manifest
            if state:
                record.state = state
            if address:
                record.address = address
            record.fingerprint = fingerprint or manifest.fingerprint()
            if touch_seen:
                record.last_seen = now_ms
            self._update(record)
            return record
        if trust_tier:
            manifest.trust_tier = trust_tier
        record = DeviceRecord(
            manifest=manifest,
            state=state or DeviceState.PAIRED.value,
            address=address, fingerprint=fingerprint or manifest.fingerprint(),
            paired_at=now_ms, paired_by=paired_by or "owner",
            last_seen=now_ms if state == DeviceState.ONLINE.value else 0,
            created_at=now_ms, updated_at=now_ms)
        self._insert(record)
        self._audit(f"device.register:{record.device_id}",
                    f"type={manifest.type} tier={manifest.trust_tier} state={record.state}")
        return record

    def _insert(self, record: DeviceRecord) -> None:
        self.db.execute(
            "INSERT INTO devices(device_id, name, type, trust_tier, state, capabilities,"
            " document, fingerprint, address, transport, paired_at, paired_by, last_seen,"
            " last_error, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.device_id, record.manifest.name, record.manifest.type,
             record.manifest.trust_tier, record.state,
             json.dumps(record.manifest.capabilities), json.dumps(record.manifest.to_dict()),
             record.fingerprint, record.address, record.manifest.transport,
             record.paired_at, record.paired_by, record.last_seen, record.last_error,
             record.created_at, record.updated_at))

    def _update(self, record: DeviceRecord) -> None:
        self.db.execute(
            "UPDATE devices SET name=?, type=?, trust_tier=?, state=?, capabilities=?,"
            " document=?, fingerprint=?, address=?, transport=?, paired_at=?, paired_by=?,"
            " last_seen=?, last_error=?, updated_at=? WHERE device_id=?",
            (record.manifest.name, record.manifest.type, record.manifest.trust_tier,
             record.state, json.dumps(record.manifest.capabilities),
             json.dumps(record.manifest.to_dict()), record.fingerprint, record.address,
             record.manifest.transport, record.paired_at, record.paired_by, record.last_seen,
             record.last_error, int(time.time() * 1000), record.device_id))

    def set_state(self, device_id: str, state: str, *, error: str = "") -> Dict[str, Any]:
        record = self.get(device_id)
        if record is None:
            return {"ok": False, "error": f"unknown device {device_id}"}
        record.state = state
        record.last_error = error
        if state == DeviceState.ONLINE.value:
            record.last_seen = int(time.time() * 1000)
        self._update(record)
        self._audit(f"device.state:{device_id}", f"{state} {error}".strip())
        return {"ok": True, "device_id": device_id, "state": state}

    def touch(self, device_id: str) -> None:
        """Heartbeat: the device is alive."""
        record = self.get(device_id)
        if record is None:
            return
        record.last_seen = int(time.time() * 1000)
        if record.state == DeviceState.PAIRED.value:
            record.state = DeviceState.ONLINE.value
        self._update(record)

    def set_trust(self, device_id: str, tier: str, *, by: str = "owner") -> Dict[str, Any]:
        if tier not in {t.value for t in TrustTier}:
            return {"ok": False, "error": f"unknown trust tier {tier}"}
        record = self.get(device_id)
        if record is None:
            return {"ok": False, "error": f"unknown device {device_id}"}
        record.manifest.trust_tier = tier
        self._update(record)
        self._audit(f"device.trust:{device_id}", f"{tier} by {by}")
        return {"ok": True, "device_id": device_id, "trust_tier": tier}

    def update_capabilities(self, device_id: str,
                            capabilities: List[str]) -> Dict[str, Any]:
        record = self.get(device_id)
        if record is None:
            return {"ok": False, "error": f"unknown device {device_id}"}
        record.manifest.capabilities = sorted({canonical_capability(c) for c in capabilities})
        record.fingerprint = record.manifest.fingerprint()
        self._update(record)
        self._audit(f"device.capabilities:{device_id}", f"{len(capabilities)} declared")
        return {"ok": True, "device_id": device_id,
                "capabilities": record.manifest.capabilities}

    def forget(self, device_id: str) -> Dict[str, Any]:
        if device_id == "pc_main":
            return {"ok": False, "error": "the local machine cannot be forgotten"}
        self.db.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
        self.db.execute("DELETE FROM device_pairings WHERE device_id=?", (device_id,))
        self._audit(f"device.forget:{device_id}", "removed by owner")
        return {"ok": True, "device_id": device_id, "forgotten": True}

    # ------------------------------------------------------------------------- read
    def get(self, device_id: str) -> Optional[DeviceRecord]:
        row = self.db.query_one("SELECT * FROM devices WHERE device_id=?", (device_id,))
        return self._row_to_record(row) if row else None

    def all(self) -> List[DeviceRecord]:
        rows = self.db.query("SELECT * FROM devices ORDER BY device_id")
        return [self._row_to_record(r) for r in rows]

    def online(self) -> List[DeviceRecord]:
        return [r for r in self.all() if r.online()]

    def capabilities(self, device_id: str) -> List[str]:
        record = self.get(device_id)
        return list(record.manifest.capabilities) if record else []

    def supports(self, device_id: str, capability: str) -> bool:
        record = self.get(device_id)
        return bool(record and record.manifest.supports(capability))

    def find_by_capability(self, capability: str, *,
                           exclude: Optional[List[str]] = None) -> List[DeviceRecord]:
        """Which devices can serve a capability (used for routing and for honest errors)."""
        skip = set(exclude or [])
        return [r for r in self.all()
                if r.device_id not in skip and r.manifest.supports(capability)]

    def known_ids(self) -> List[str]:
        return [r.device_id for r in self.all()]

    @staticmethod
    def _row_to_record(row) -> DeviceRecord:
        raw = json.loads(row["document"]) if row["document"] else {}
        if not raw:
            raw = {"device_id": row["device_id"], "name": row["name"], "type": row["type"],
                   "trust_tier": row["trust_tier"],
                   "capabilities": json.loads(row["capabilities"] or "[]")}
        manifest = DeviceManifest.from_dict(raw)
        return DeviceRecord(
            manifest=manifest, state=row["state"] or DeviceState.UNPAIRED.value,
            address=row["address"] or "", fingerprint=row["fingerprint"] or "",
            paired_at=int(row["paired_at"] or 0), paired_by=row["paired_by"] or "",
            last_seen=int(row["last_seen"] or 0), last_error=row["last_error"] or "",
            created_at=int(row["created_at"] or 0), updated_at=int(row["updated_at"] or 0))

    # -------------------------------------------------------------------- pairings
    def store_pairing(self, device_id: str, secret: str, *,
                      fingerprint: str = "") -> Dict[str, Any]:
        """Store a pairing secret in the vault and keep only the reference here."""
        if self.vault is None:
            return {"ok": False, "error": "no vault available to hold the pairing secret"}
        ref = f"secret://device/{device_id}/token"
        self.vault.store(ref, secret)
        pairing_id = new_id("pair")
        now_ms = int(time.time() * 1000)
        self.db.execute(
            "INSERT INTO device_pairings(pairing_id, device_id, token_ref, fingerprint,"
            " created_at, revoked_at, last_used) VALUES(?,?,?,?,?,NULL,0)",
            (pairing_id, device_id, ref, fingerprint or "", now_ms))
        self._audit(f"device.pair:{device_id}", f"pairing {pairing_id}")
        return {"ok": True, "device_id": device_id, "pairing_id": pairing_id, "ref": ref}

    def pairing_secret(self, device_id: str) -> str:
        row = self.db.query_one(
            "SELECT token_ref FROM device_pairings WHERE device_id=? AND revoked_at IS NULL"
            " ORDER BY created_at DESC LIMIT 1", (device_id,))
        if row is None or self.vault is None:
            return ""
        try:
            return self.vault.resolve(row["token_ref"]) or ""
        except Exception as exc:                      # a missing secret must not crash routing
            log.warning("pairing secret unavailable for %s: %s", device_id, exc)
            return ""

    def revoke_pairing(self, device_id: str) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        self.db.execute("UPDATE device_pairings SET revoked_at=? WHERE device_id=? AND"
                        " revoked_at IS NULL", (now_ms, device_id))
        self.set_state(device_id, DeviceState.UNPAIRED.value)
        self._audit(f"device.unpair:{device_id}", "pairing revoked by owner")
        return {"ok": True, "device_id": device_id, "revoked": True}

    def paired(self, device_id: str) -> bool:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM device_pairings WHERE device_id=? AND revoked_at IS NULL",
            (device_id,))
        return bool(row and row["n"])

    def status(self) -> Dict[str, Any]:
        records = self.all()
        by_state: Dict[str, int] = {}
        for record in records:
            by_state[record.state] = by_state.get(record.state, 0) + 1
        return {"count": len(records), "by_state": by_state,
                "online": len([r for r in records if r.online()]),
                "devices": [r.to_dict() for r in records]}

    def _audit(self, action: str, detail: str) -> None:
        if self.audit:
            try:
                self.audit.record(who="system", action=action, why=detail[:200], result="ok")
            except Exception as exc:
                log.debug("device audit failed: %s", exc)
