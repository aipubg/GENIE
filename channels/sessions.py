"""Persistent session store — the continuity half of the OpenClaw gap.

A session is identified by (channel, external_key) so that the same person in
the same channel resumes the same conversation, and by a stable session_id for
internal use. Messages are appended with their role so prior turns can be
replayed as context.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("channels.sessions")


def _now() -> int:
    return int(time.time())


class SessionStore:
    """SQLite-backed conversation sessions."""

    def __init__(self, db) -> None:
        self.db = db

    # ------------------------------------------------------------- create
    def create(self, channel: str, external_key: str, *, title: str = "",
               person_id: str = "") -> Dict[str, Any]:
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO channel_sessions(session_id, channel, external_key, "
            "person_id, title, created_at, updated_at, message_count) "
            "VALUES(?,?,?,?,?,?,?,0)",
            (sid, channel, external_key, person_id, title, now, now))
        return {"session_id": sid, "channel": channel, "external_key": external_key,
                "person_id": person_id, "title": title, "created_at": now,
                "updated_at": now, "message_count": 0}

    # ---------------------------------------------------------------- read
    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one(
            "SELECT * FROM channel_sessions WHERE session_id=?", (session_id,))
        return dict(row) if row else None

    def get_by_key(self, channel: str, external_key: str) -> Optional[Dict[str, Any]]:
        """Resume the conversation for this channel+identity (continuity)."""
        row = self.db.query_one(
            "SELECT * FROM channel_sessions WHERE channel=? AND external_key=? "
            "ORDER BY updated_at DESC LIMIT 1", (channel, external_key))
        return dict(row) if row else None

    def get_or_create(self, channel: str, external_key: str, *,
                      person_id: str = "") -> Dict[str, Any]:
        existing = self.get_by_key(channel, external_key)
        if existing:
            return existing
        return self.create(channel, external_key, person_id=person_id)

    # ---------------------------------------------------------------- write
    def append(self, session_id: str, role: str, text: str,
               metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        mid = f"msg_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO channel_messages(message_id, session_id, role, text, "
            "metadata, created_at) VALUES(?,?,?,?,?,?)",
            (mid, session_id, role, text, json.dumps(metadata or {}), now))
        self.db.execute(
            "UPDATE channel_sessions SET updated_at=?, message_count="
            "(SELECT COUNT(*) FROM channel_messages WHERE session_id=?) "
            "WHERE session_id=?", (now, session_id, session_id))
        return {"message_id": mid, "session_id": session_id, "role": role,
                "text": text, "metadata": metadata or {}, "created_at": now}

    def touch(self, session_id: str) -> None:
        self.db.execute("UPDATE channel_sessions SET updated_at=? WHERE session_id=?",
                        (_now(), session_id))

    # ------------------------------------------------------------- history
    def history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT role, text, metadata, created_at FROM channel_messages "
            "WHERE session_id=? ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (session_id, int(limit)))
        out = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"] or "{}")
            except Exception:  # noqa: BLE001
                meta = {}
            out.append({"role": r["role"], "text": r["text"],
                        "metadata": meta, "created_at": r["created_at"]})
        return out

    def list_sessions(self, channel: Optional[str] = None,
                      limit: int = 50) -> List[Dict[str, Any]]:
        if channel:
            rows = self.db.query(
                "SELECT * FROM channel_sessions WHERE channel=? "
                "ORDER BY updated_at DESC LIMIT ?", (channel, int(limit)))
        else:
            rows = self.db.query(
                "SELECT * FROM channel_sessions ORDER BY updated_at DESC LIMIT ?",
                (int(limit),))
        return [dict(r) for r in rows]
