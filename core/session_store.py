"""Owner session store (core/session_store).

Phase 0 closure — restart-safe owner-session continuity.

Recent exact turns are persisted (bounded) so a backend restart does not lose
conversational context. This is SEPARATE from the long-term MemoryService:
it is ephemeral session state, never promoted to permanent memory.

Design:
  * session_turns  — bounded exact (user, genie) turns per session_id
  * session_checkpoints — one compact summary of turns older than the bound
  * no unlimited transcript growth: turns beyond the bound arecompacted into
    the checkpoint row
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.session_store")

DEFAULT_BOUND = 20
CHECKPOINT_TRIGGER = 20  # compact once history exceeds this


class SessionStore:
    def __init__(self, db, session_id: str = "owner", bound: int = DEFAULT_BOUND):
        self.db = db
        self.session_id = session_id
        self.bound = bound

    # ------------------------------------------------------------- write
    def append(self, user_text: str, genie_reply: str) -> None:
        if not genie_reply:
            return
        now = int(time.time() * 1000)
        try:
            def archive(conn):
                conn.executemany(
                    "INSERT INTO conversation_messages(session_id, role, text, created_at) VALUES (?,?,?,?)",
                    [(self.session_id, "user", (user_text or "").strip(), now),
                     (self.session_id, "genie", genie_reply.strip(), now)])
            self.db.in_transaction(archive)
            seq = self._next_seq()
            self.db.execute(
                "INSERT OR REPLACE INTO session_turns "
                "(turn_id, session_id, role, text, created_at, seq) VALUES (?,?,?,?,?,?)",
                (new_id(), self.session_id, "user", (user_text or "").strip(), now, seq))
            seq2 = self._next_seq()
            self.db.execute(
                "INSERT OR REPLACE INTO session_turns "
                "(turn_id, session_id, role, text, created_at, seq) VALUES (?,?,?,?,?,?)",
                (new_id(), self.session_id, "genie", (genie_reply or "").strip(), now, seq2))
            self._maybe_compact()
        except Exception as exc:  # persistence must never break a turn
            log.warning("session append failed: %s", exc)

    # ------------------------------------------------------------- read
    def recent(self, limit: Optional[int] = None) -> List[tuple[str, str]]:
        """Return (user, genie) tuples in chronological order."""
        lim = limit or self.bound
        try:
            rows = self.db.query(
                "SELECT role, text FROM session_turns "
                "WHERE session_id=? ORDER BY seq ASC", (self.session_id,))
            out: List[tuple[str, str]] = []
            buf: Dict[str, str] = {}
            # pair user->genie by sequence
            pairs: List[tuple[str, str]] = []
            pending_user: Optional[str] = None
            for r in rows:
                if r["role"] == "user":
                    pending_user = r["text"]
                else:
                    pairs.append((pending_user or "", r["text"]))
                    pending_user = None
            return pairs[-lim:]
        except Exception as exc:
            log.debug("session recent failed: %s", exc)
            return []

    def history(self, limit: int = 100, before: int = 0) -> list[dict]:
        """Durable transcript, independent of the bounded model context."""
        rows = self.db.query(
            "SELECT id, role, text, created_at FROM conversation_messages "
            "WHERE session_id=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?",
            (self.session_id, before, before, max(1, min(100, limit))))
        return [dict(row) for row in reversed(rows)]

    def checkpoint(self) -> Optional[str]:
        try:
            rows = self.db.query(
                "SELECT summary FROM session_checkpoints WHERE session_id=?",
                (self.session_id,))
            return rows[0]["summary"] if rows else None
        except Exception:
            return None

    def clear(self) -> None:
        try:
            self.db.execute("DELETE FROM conversation_messages WHERE session_id=?",
                            (self.session_id,))
            self.db.execute("DELETE FROM session_turns WHERE session_id=?",
                            (self.session_id,))
            self.db.execute("DELETE FROM session_checkpoints WHERE session_id=?",
                            (self.session_id,))
        except Exception as exc:
            log.debug("session clear failed: %s", exc)

    # ------------------------------------------------------------- internals
    def _next_seq(self) -> int:
        rows = self.db.query(
            "SELECT COALESCE(MAX(seq),0) AS m FROM session_turns WHERE session_id=?",
            (self.session_id,))
        return (rows[0]["m"] + 1) if rows else 1

    def _maybe_compact(self) -> None:
        total = self.db.query(
            "SELECT COUNT(*) AS c FROM session_turns WHERE session_id=?",
            (self.session_id,))
        count = total[0]["c"] if total else 0
        if count <= self.bound + CHECKPOINT_TRIGGER:
            return
        # Keep the most recent `bound` turns; compact the rest into a checkpoint.
        keep_rows = self.db.query(
            "SELECT seq FROM session_turns WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (self.session_id, self.bound))
        keep_seqs = [r["seq"] for r in keep_rows]
        if not keep_seqs:
            return
        old_rows = self.db.query(
            "SELECT role, text FROM session_turns WHERE session_id=? AND seq < ? "
            "ORDER BY seq ASC", (self.session_id, min(keep_seqs)))
        summary_lines = []
        pending_user: Optional[str] = None
        for r in old_rows:
            if r["role"] == "user":
                pending_user = r["text"]
            else:
                summary_lines.append(f"- Owner: {pending_user or ''} | GENIE: {r['text']}")
                pending_user = None
        summary = "Earlier in this session:\n" + "\n".join(summary_lines)
        now = int(time.time() * 1000)
        min_keep = min(keep_seqs)
        self.db.execute("DELETE FROM session_turns WHERE session_id=? AND seq < ?",
                        (self.session_id, min_keep))
        self.db.execute(
            "INSERT OR REPLACE INTO session_checkpoints "
            "(session_id, summary, updated_at, turn_seq) VALUES (?,?,?,?)",
            (self.session_id, summary, now, min_keep))
        log.info("session compacted %d turns into checkpoint", len(old_rows))


def new_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"
