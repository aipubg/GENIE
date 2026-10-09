"""Append-only, hash-chained audit log (security/audit) — contract C16.

Format: WHO · WHEN · DEVICE · ACTION · WHY · MISSION · RESULT
Tamper evidence: each row stores prev_hash + hash(prev|row).
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, List, Optional

from core.contracts import now_ms


def _hash(prev: str, payload: str) -> str:
    return hashlib.sha256((prev + "|" + payload).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()

    def record(self, who: str, action: str, result: str = "", *, device: str = "pc_main",
               why: str = "", mission_id: Optional[str] = None,
               trace_id: Optional[str] = None) -> str:
        ts = now_ms()
        with self._lock:
            last = self.db.query_one("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1")
            prev = last["hash"] if last else "0" * 64
            payload = json.dumps([who, ts, device, action, why, mission_id, result], sort_keys=True)
            h = _hash(prev, payload)
            cur = self.db.execute(
                "INSERT INTO audit_log(ts, who, device, action, why, mission_id, result,"
                " trace_id, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts, who, device, action, why, mission_id, result, trace_id, prev, h))
            return str(cur.lastrowid)

    def tail(self, limit: int = 50, mission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if mission_id:
            rows = self.db.query("SELECT * FROM audit_log WHERE mission_id=? ORDER BY seq DESC LIMIT ?",
                                 (mission_id, limit))
        else:
            rows = self.db.query("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def verify(self) -> bool:
        rows = self.db.query("SELECT * FROM audit_log ORDER BY seq ASC")
        prev = "0" * 64
        for r in rows:
            payload = json.dumps([r["who"], r["ts"], r["device"], r["action"], r["why"],
                                  r["mission_id"], r["result"]], sort_keys=True)
            if r["prev_hash"] != prev or r["hash"] != _hash(prev, payload):
                return False
            prev = r["hash"]
        return True


_AUDIT: Optional[AuditLog] = None


def get_audit(db=None) -> AuditLog:
    global _AUDIT
    if _AUDIT is None:
        if db is None:
            from core.db import get_db
            db = get_db()
        _AUDIT = AuditLog(db)
    return _AUDIT
