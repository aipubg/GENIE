"""Device sync v1 (devices/sync.py).

Keeps a shared record set consistent when the same key is edited on two devices while one of them
is offline — the case golden task #17 names: *"Phone + PC offline edits → conflict resolved per
policy, UI shown"*.

Design choices, and why:

* **Per-record logical version, not a global clock.** A record carries its own `updated_at_ms` and
  `version`, so one device being offline cannot hold back another's unrelated edits.
* **A conflict is only a conflict when there is no causal order.** If one change is strictly newer,
  it simply wins — that is not a conflict, and recording it as one would train the owner to ignore
  the conflict list. Equal timestamps with divergent values on different devices is the real
  conflict case.
* **Resolution is a declared policy, never a guess.** `last-write-wins`, `device-priority`,
  `merge-union`, `manual`. The policy is stored per namespace so behaviour is reproducible.
* **Nothing is silently lost.** Every resolution is persisted, and `manual` keeps both sides until
  the owner decides.
* **Deleted is a value, not an absence.** A tombstone propagates, so an offline device cannot
  resurrect a record it never saw deleted.
"""
from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("devices.sync")


class SyncPolicy(str, enum.Enum):
    LAST_WRITE_WINS = "last-write-wins"
    DEVICE_PRIORITY = "device-priority"
    MERGE_UNION = "merge-union"
    MANUAL = "manual"


DEFAULT_POLICY = SyncPolicy.LAST_WRITE_WINS.value

#: Which device wins a tie under `device-priority`. The daemon is the reference.
DEFAULT_PRIORITY = ("pc_main", "laptop_main", "phone_main", "rpi_main")


@dataclass
class Change:
    namespace: str
    key: str
    value: Any = None
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    device_id: str = "pc_main"
    deleted: bool = False
    version: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"namespace": self.namespace, "key": self.key, "value": self.value,
                "updated_at_ms": self.updated_at_ms, "device_id": self.device_id,
                "deleted": self.deleted, "version": self.version}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Change":
        return cls(namespace=str(raw.get("namespace", "default")),
                   key=str(raw.get("key", "")),
                   value=raw.get("value"),
                   updated_at_ms=int(raw.get("updated_at_ms", 0) or 0),
                   device_id=str(raw.get("device_id", "pc_main")),
                   deleted=bool(raw.get("deleted", False)),
                   version=int(raw.get("version", 0) or 0))


@dataclass
class Conflict:
    """Two concurrent edits that could not be ordered.

    The two **sides** are always kept explicitly (`incoming` and `stored`). `chosen` records which
    side the policy wrote, or is empty when the policy is `manual` and the owner has not decided.
    Naming the sides rather than "winner/loser" matters: for `manual` there is no winner yet, and
    pretending otherwise is exactly what made the owner's resolution apply the wrong side.
    """

    namespace: str
    key: str
    incoming: Dict[str, Any]
    stored: Dict[str, Any]
    policy: str
    reason: str
    chosen: str = ""                      # "incoming" | "stored" | "" (undecided)
    conflict_id: str = ""
    resolved: bool = False
    resolved_by: str = ""
    resolved_at: int = 0
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        payload = {"conflict_id": self.conflict_id, "namespace": self.namespace, "key": self.key,
                   "incoming": self.incoming, "stored": self.stored, "chosen": self.chosen,
                   "policy": self.policy, "reason": self.reason, "resolved": self.resolved,
                   "resolved_by": self.resolved_by, "resolved_at": self.resolved_at,
                   "ts": self.ts, "needs_owner": self.chosen == ""}
        # convenience for a UI that only wants to show who won
        if self.chosen:
            payload["winner"] = self.incoming if self.chosen == "incoming" else self.stored
            payload["loser"] = self.stored if self.chosen == "incoming" else self.incoming
        else:
            payload["winner"] = None
            payload["loser"] = None
        return payload


def _priority_rank(device_id: str, priority: List[str]) -> int:
    try:
        return priority.index(device_id)
    except ValueError:
        return len(priority)


def merge_values(left: Any, right: Any) -> Optional[Any]:
    """Union two values when they can be merged; `None` means "not mergeable"."""
    if isinstance(left, list) and isinstance(right, list):
        merged: List[Any] = []
        for item in list(left) + list(right):
            if item not in merged:
                merged.append(item)
        return merged
    if isinstance(left, dict) and isinstance(right, dict):
        return {**left, **right}
    return None


class SyncService:
    """Daemon-side sync: apply a device's changes, resolve, and hand back the resolved set."""

    def __init__(self, db, *, audit=None, priority: Optional[List[str]] = None):
        self.db = db
        self.audit = audit
        self.priority = list(priority or DEFAULT_PRIORITY)
        self._bus = get_bus()

    # ------------------------------------------------------------------- policies
    def policy_for(self, namespace: str) -> str:
        row = self.db.query_one("SELECT policy FROM sync_policies WHERE namespace=?", (namespace,))
        return row["policy"] if row else DEFAULT_POLICY

    def set_policy(self, namespace: str, policy: str, *, by: str = "owner") -> Dict[str, Any]:
        if policy not in {p.value for p in SyncPolicy}:
            return {"ok": False, "error": f"unknown policy {policy}"}
        self.db.execute("INSERT OR REPLACE INTO sync_policies(namespace, policy, updated_at)"
                        " VALUES(?,?,?)", (namespace, policy, int(time.time() * 1000)))
        self._audit(by, f"sync.policy:{namespace}", policy)
        return {"ok": True, "namespace": namespace, "policy": policy}

    # ----------------------------------------------------------------------- read
    def record(self, namespace: str, key: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM sync_records WHERE namespace=? AND key=?",
                                (namespace, key))
        return self._row_to_dict(row) if row else None

    def records(self, namespace: str = "", *, since_cursor: int = 0) -> List[Dict[str, Any]]:
        if namespace:
            rows = self.db.query("SELECT * FROM sync_records WHERE namespace=? AND seq>?"
                                 " ORDER BY seq", (namespace, since_cursor))
        else:
            rows = self.db.query("SELECT * FROM sync_records WHERE seq>? ORDER BY seq",
                                 (since_cursor,))
        return [self._row_to_dict(r) for r in rows]

    def cursor(self) -> int:
        row = self.db.query_one("SELECT MAX(seq) AS c FROM sync_records")
        return int(row["c"] or 0) if row else 0

    def conflicts(self, *, unresolved_only: bool = False,
                  namespace: str = "") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM sync_conflicts WHERE 1=1"
        params: List[Any] = []
        if unresolved_only:
            sql += " AND resolved=0"
        if namespace:
            sql += " AND namespace=?"
            params.append(namespace)
        sql += " ORDER BY ts DESC LIMIT 200"
        return [self._conflict_from_row(r) for r in self.db.query(sql, tuple(params))]

    # ---------------------------------------------------------------------- write
    def apply(self, ctx: CallContext, changes: List[Dict[str, Any]],
              *, policy: str = "") -> Dict[str, Any]:
        """Apply a batch of changes from one device and resolve what cannot be ordered."""
        results: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        for raw in changes:
            change = Change.from_dict(raw)
            if not change.key or not change.namespace:
                results.append({"key": change.key, "applied": False,
                                "reason": "namespace and key are required"})
                continue
            outcome = self.apply_one(ctx, change, policy=policy)
            results.append(outcome)
            if outcome.get("conflict"):
                conflicts.append(outcome["conflict"])
        return {"ok": True, "applied": len([r for r in results if r.get("applied")]),
                "rejected": len([r for r in results if not r.get("applied")]),
                "results": results, "conflicts": conflicts,
                "cursor": self.cursor()}

    def apply_one(self, ctx: CallContext, change: Change, *, policy: str = "") -> Dict[str, Any]:
        effective = policy or self.policy_for(change.namespace)
        existing = self.record(change.namespace, change.key)
        if existing is None:
            self._store(change)
            return {"key": change.key, "applied": True, "action": "created",
                    "version": change.version}

        # identical value: nothing to do (and definitely not a conflict)
        if existing["value"] == change.value and existing["deleted"] == change.deleted:
            return {"key": change.key, "applied": False, "action": "unchanged",
                    "version": existing["version"]}

        # strictly newer wins outright — a causal order means there is no conflict
        if change.updated_at_ms > existing["updated_at_ms"]:
            self._store(change)
            return {"key": change.key, "applied": True, "action": "updated",
                    "version": change.version}
        if change.updated_at_ms < existing["updated_at_ms"]:
            return {"key": change.key, "applied": False, "action": "stale",
                    "version": existing["version"]}

        # same timestamp, different value: genuinely concurrent
        return self._resolve_concurrent(ctx, change, existing, effective)

    def _resolve_concurrent(self, ctx: CallContext, incoming: Change,
                            existing: Dict[str, Any], policy: str) -> Dict[str, Any]:
        winner_is_incoming = False
        reason = ""
        merged: Any = None

        if policy == SyncPolicy.DEVICE_PRIORITY.value:
            incoming_rank = _priority_rank(incoming.device_id, self.priority)
            existing_rank = _priority_rank(existing["device_id"], self.priority)
            winner_is_incoming = incoming_rank < existing_rank
            reason = (f"device-priority: {incoming.device_id}({incoming_rank}) vs "
                      f"{existing['device_id']}({existing_rank})")
        elif policy == SyncPolicy.MERGE_UNION.value:
            merged = merge_values(existing["value"], incoming.value)
            if merged is not None:
                change = Change(namespace=incoming.namespace, key=incoming.key, value=merged,
                                updated_at_ms=incoming.updated_at_ms + 1,
                                device_id=incoming.device_id,
                                version=max(existing["version"], incoming.version) + 1)
                self._store(change)
                self._audit(ctx.person_id, f"sync.merge:{incoming.namespace}/{incoming.key}",
                            "merge-union")
                return {"key": incoming.key, "applied": True, "action": "merged",
                        "version": change.version}
            winner_is_incoming = False
            reason = "merge-union: values are not mergeable, kept the stored value"
        else:
            # last-write-wins and manual both fall back to a deterministic tie-break
            incoming_rank = _priority_rank(incoming.device_id, self.priority)
            existing_rank = _priority_rank(existing["device_id"], self.priority)
            winner_is_incoming = (incoming_rank < existing_rank) or (
                incoming_rank == existing_rank and incoming.device_id < existing["device_id"])
            reason = (f"{policy}: equal timestamps, deterministic tie-break by device "
                      f"({incoming.device_id} vs {existing['device_id']})")

        conflict = self._record_conflict(
            incoming, existing, policy, reason,
            chosen=("incoming" if winner_is_incoming else "stored")
            if policy != SyncPolicy.MANUAL.value else "")

        if policy == SyncPolicy.MANUAL.value:
            # keep what is stored until the owner decides; the conflict carries both sides
            return {"key": incoming.key, "applied": False, "action": "awaiting_owner",
                    "version": existing["version"], "conflict": conflict}

        if winner_is_incoming:
            self._store(incoming)
            return {"key": incoming.key, "applied": True, "action": "conflict_incoming_won",
                    "version": incoming.version, "conflict": conflict}
        return {"key": incoming.key, "applied": False, "action": "conflict_stored_won",
                "version": existing["version"], "conflict": conflict}

    def resolve_conflict(self, conflict_id: str, *, winner: str, by: str = "owner") -> Dict[str, Any]:
        """Owner decision for a `manual` conflict: `winner` is `incoming` or `stored`."""
        row = self.db.query_one("SELECT * FROM sync_conflicts WHERE conflict_id=?", (conflict_id,))
        if row is None:
            return {"ok": False, "error": f"unknown conflict {conflict_id}"}
        if winner not in ("incoming", "stored"):
            return {"ok": False, "error": "winner must be 'incoming' or 'stored'"}
        payload = json.loads(row["incoming"] if winner == "incoming" else row["stored"])
        change = Change.from_dict(payload)
        change.updated_at_ms = int(time.time() * 1000)
        change.version = max(int(row["incoming"] and json.loads(row["incoming"]).get("version", 0) or 0),
                             int(json.loads(row["stored"]).get("version", 0) or 0)) + 1
        self._store(change)
        self.db.execute("UPDATE sync_conflicts SET resolved=1, resolved_by=?, resolved_at=?,"
                        " chosen=? WHERE conflict_id=?",
                        (by, int(time.time() * 1000), winner, conflict_id))
        self._audit(by, f"sync.resolve:{change.namespace}/{change.key}", winner)
        return {"ok": True, "conflict_id": conflict_id, "winner": winner,
                "value": change.value, "version": change.version}

    # -------------------------------------------------------------------- storage
    def _store(self, change: Change) -> None:
        now_ms = int(time.time() * 1000)
        existing = self.db.query_one("SELECT version FROM sync_records WHERE namespace=? AND key=?",
                                     (change.namespace, change.key))
        version = change.version or (int(existing["version"]) + 1 if existing else 1)
        self.db.execute(
            "INSERT INTO sync_records(namespace, key, value, deleted, device_id, updated_at_ms,"
            " version, updated_at) VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value,"
            " deleted=excluded.deleted, device_id=excluded.device_id,"
            " updated_at_ms=excluded.updated_at_ms, version=excluded.version,"
            " updated_at=excluded.updated_at",
            (change.namespace, change.key, json.dumps(change.value), 1 if change.deleted else 0,
             change.device_id, change.updated_at_ms, version, now_ms))
        self._bus.publish("SYNC_RECORD_UPDATED",
                          {"namespace": change.namespace, "key": change.key,
                           "device_id": change.device_id, "version": version})

    def _record_conflict(self, incoming: Change, existing: Dict[str, Any], policy: str,
                         reason: str, *, chosen: str = "") -> Dict[str, Any]:
        from core.contracts import new_id
        conflict_id = new_id("conf")
        conflict = Conflict(namespace=incoming.namespace, key=incoming.key,
                            incoming=incoming.to_dict(), stored=existing,
                            policy=policy, reason=reason, chosen=chosen,
                            conflict_id=conflict_id)
        self.db.execute(
            "INSERT INTO sync_conflicts(conflict_id, namespace, key, incoming, stored, chosen,"
            " policy, reason, resolved, resolved_by, resolved_at, ts)"
            " VALUES(?,?,?,?,?,?,?,?,0,'',0,?)",
            (conflict_id, conflict.namespace, conflict.key, json.dumps(conflict.incoming),
             json.dumps(conflict.stored), chosen, policy, reason, conflict.ts))
        self._audit("system", f"sync.conflict:{conflict.namespace}/{conflict.key}",
                    f"{policy} — {reason}")
        log.info("sync conflict on %s/%s (%s)", conflict.namespace, conflict.key, policy)
        return conflict.to_dict()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {"namespace": row["namespace"], "key": row["key"],
                "value": json.loads(row["value"]) if row["value"] is not None else None,
                "deleted": bool(row["deleted"]), "device_id": row["device_id"],
                "updated_at_ms": int(row["updated_at_ms"] or 0),
                "version": int(row["version"] or 0), "seq": int(row["seq"] or 0)}

    @staticmethod
    def _conflict_from_row(row) -> Dict[str, Any]:
        incoming = json.loads(row["incoming"]) if row["incoming"] else {}
        stored = json.loads(row["stored"]) if row["stored"] else {}
        chosen = row["chosen"] or ""
        payload = {"conflict_id": row["conflict_id"], "namespace": row["namespace"],
                   "key": row["key"], "incoming": incoming, "stored": stored,
                   "chosen": chosen, "policy": row["policy"], "reason": row["reason"],
                   "resolved": bool(row["resolved"]),
                   "resolved_by": row["resolved_by"] or "",
                   "resolved_at": int(row["resolved_at"] or 0), "ts": int(row["ts"] or 0),
                   "needs_owner": chosen == ""}
        if chosen:
            payload["winner"] = incoming if chosen == "incoming" else stored
            payload["loser"] = stored if chosen == "incoming" else incoming
        else:
            payload["winner"] = None
            payload["loser"] = None
        return payload

    def _audit(self, who: str, action: str, detail: str) -> None:
        if not self.audit:
            return
        try:
            self.audit.record(who=who, action=action, why=detail[:200], result="ok")
        except Exception as exc:
            log.debug("sync audit failed: %s", exc)

    def status(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT COUNT(*) AS n FROM sync_records")
        unresolved = self.db.query_one("SELECT COUNT(*) AS n FROM sync_conflicts WHERE resolved=0")
        policies = {r["namespace"]: r["policy"]
                    for r in self.db.query("SELECT namespace, policy FROM sync_policies")}
        return {"records": int(rows[0]["n"] or 0) if rows else 0,
                "cursor": self.cursor(),
                "unresolved_conflicts": int(unresolved["n"] or 0) if unresolved else 0,
                "policies": policies, "default_policy": DEFAULT_POLICY,
                "priority": self.priority}
