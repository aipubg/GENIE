"""Memory Service — the truth owner for memory (contract C3, master spec §3).

Guarantees:
  * NEDLE2/agents may only *propose* writes; this service validates
  * wrong memory is never overwritten — it is superseded (new version, old linked)
  * `forget` purges from the table, FTS index and vector cache
  * reads are filtered by privacy scope and PTE read scope
  * works fully offline (FTS + structured + recency)
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from core.contracts import (
    CallContext, DataClass, EventType, MemoryHit, MemoryRecord, now_ms, new_id,
)
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("memory.service")

_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(text: str) -> str:
    return _WORD.sub(" ", (text or "").lower()).strip()


def _hash(type_: str, entity: str, value: str) -> str:
    return hashlib.sha256(f"{_norm(type_)}|{_norm(entity)}|{_norm(value)}".encode("utf-8")).hexdigest()


class MemoryService:
    def __init__(self, db, audit=None, trust=None):
        self.db = db
        self.audit = audit
        self.trust = trust
        self._bus = get_bus()
        # tiny in-process vector cache: hash -> list[float] (provider embeddings, §3.5)
        self._vector_cache: Dict[str, List[float]] = {}
        from .observations import ObservationStore
        self.observations = ObservationStore(db)
        # E60/B08: bounded observational memory over the SAME local store, so
        # Chat and Live retrieve learned context through one memory authority.
        from .observation_memory import ObservationMemory
        self.observation_memory = ObservationMemory(self.observations)

    # ------------------------------------------------------------------ write
    def write(self, ctx: CallContext, *, type: str, entity: str, value: str,
              confidence: float = 0.7, source: str = "user",
              privacy_scope: str = "private:owner",
              data_class: DataClass = DataClass.INTERNAL,
              project: str = "", metadata: Dict[str, Any] | None = None) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("empty memory value")

        # permission: writing another person's memory is not allowed
        if self.trust and privacy_scope.startswith("private:") and \
                privacy_scope.split(":", 1)[1] != ctx.person_id and ctx.persona.value != "owner":
            raise PermissionError("cannot write to another person's private memory")

        now = now_ms()
        digest = _hash(type, entity, value)

        # --- dedupe: identical (type, entity, value) already exists?
        row = self.db.query_one(
            "SELECT * FROM memory_records WHERE metadata LIKE ? AND type=? AND entity=? AND value=? AND superseded_by IS NULL",
            (f'%{digest}%', type, entity, value))

        existing = self.db.query(
            "SELECT * FROM memory_records WHERE type=? AND entity=? AND superseded_by IS NULL",
            (type, entity))

        same = next((r for r in existing if _hash(r["type"], r["entity"], r["value"]) == digest), None)
        if same:
            self.db.execute(
                "UPDATE memory_records SET updated_at=?, confidence=MAX(confidence,?), source=? WHERE record_id=?",
                (now, confidence, source, same["record_id"]))
            self._audit(ctx, "memory.dedupe", same["record_id"])
            return same["record_id"]

        # --- conflict: same type+entity but different value -> supersede
        record_id = new_id("mem")
        supersede_id: Optional[str] = None
        for r in existing:
            if r["pinned"]:
                continue
            supersede_id = r["record_id"]
            break

        meta = dict(metadata or {})
        meta["digest"] = digest

        self.db.execute(
            "INSERT INTO memory_records(record_id,type,entity,value,confidence,source,person_id,"
            "project,mission_id,privacy_scope,data_class,created_at,updated_at,version,"
            "superseded_by,pinned,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, type, entity, value, confidence, source, ctx.person_id, project,
             ctx.mission_id or "", privacy_scope, data_class.value, now, now,
             1, None, 0, json.dumps(meta)))

        self._fts_add(record_id, entity, value)

        if supersede_id:
            self.db.execute(
                "UPDATE memory_records SET superseded_by=?, updated_at=? WHERE record_id=?",
                (record_id, now, supersede_id))
            try:
                self.db.execute("DELETE FROM memory_fts WHERE record_id=?", (supersede_id,))
            except Exception:
                pass

        self._audit(ctx, "memory.write", record_id)
        self._bus.publish(EventType.MEMORY_UPDATED, {
            "record_id": record_id, "op": "write", "type": type,
            "entity": entity, "supersedes": supersede_id}, trace_id=ctx.trace_id)
        return record_id

    def correct(self, ctx: CallContext, record_id: str, *, value: str | None = None,
                confidence: float | None = None, entity: str | None = None) -> str:
        row = self._get(record_id)
        if not row:
            raise KeyError(record_id)
        return self.write(
            ctx,
            type=row["type"],
            entity=entity if entity is not None else row["entity"],
            value=value if value is not None else row["value"],
            confidence=confidence if confidence is not None else row["confidence"],
            source=f"correction:{ctx.person_id}",
            privacy_scope=row["privacy_scope"],
            data_class=DataClass(row["data_class"]),
            project=row["project"],
        )

    def forget(self, ctx: CallContext, *, record_id: str | None = None,
               entity: str | None = None, type: str | None = None,
               person_id: str | None = None) -> int:
        sql = "SELECT record_id FROM memory_records WHERE 1=1"
        params: List[Any] = []
        if record_id:
            sql += " AND record_id=?"
            params.append(record_id)
        if entity:
            sql += " AND entity=?"
            params.append(entity)
        if type:
            sql += " AND type=?"
            params.append(type)
        if person_id:
            sql += " AND person_id=?"
            params.append(person_id)
        ids = [r["record_id"] for r in self.db.query(sql, params)]
        for rid in ids:
            self.db.execute("DELETE FROM memory_records WHERE record_id=?", (rid,))
            try:
                self.db.execute("DELETE FROM memory_fts WHERE record_id=?", (rid,))
            except Exception:
                pass
            self._vector_cache.pop(rid, None)
        self._audit(ctx, "memory.forget", f"{len(ids)} records")
        self._bus.publish(EventType.MEMORY_UPDATED,
                          {"op": "forget", "count": len(ids)}, trace_id=ctx.trace_id)
        return len(ids)

    def pin(self, record_id: str, pinned: bool = True) -> None:
        self.db.execute("UPDATE memory_records SET pinned=? WHERE record_id=?",
                        (1 if pinned else 0, record_id))

    def history(self, record_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM memory_records WHERE record_id=? OR superseded_by=? ORDER BY created_at",
            (record_id, record_id))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------- read
    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        row = self._get(record_id)
        return dict(row) if row else None

    def observational_context(self, ctx: CallContext, text: str, *,
                              limit: int = 4) -> List[Dict[str, Any]]:
        """Bounded, structured observational memory relevant to *text*.

        Never returns screenshots or raw event streams; only summarized rows
        with provenance, confidence and timestamps. A single sighting is not a
        preference, and an explicit owner correction outranks inference.
        """
        try:
            return self.observation_memory.retrieve(text, limit=limit)
        except Exception:
            return []

    def query(self, ctx: CallContext, text: str, *, limit: int = 10,
              type: str | None = None, project: str | None = None,
              include_superseded: bool = False) -> List[MemoryHit]:
        limit = max(1, min(limit, 100))
        hits: Dict[str, float] = {}

        # 1) FTS (offline-capable, no embedding needed)
        fts_rows = self._fts_search(text, limit * 3)
        for rid, rank in fts_rows:
            hits[rid] = hits.get(rid, 0.0) + max(0.0, 1.0 + rank)

        # 2) exact entity match
        norm = _norm(text)
        for r in self.db.query("SELECT record_id, entity, value FROM memory_records"
                               " WHERE superseded_by IS NULL"):
            if r["entity"] and _norm(r["entity"]) and _norm(r["entity"]) in norm:
                hits[r["record_id"]] = hits.get(r["record_id"], 0.0) + 2.0
            for token in norm.split():
                if len(token) > 3 and token in _norm(r["value"]):
                    hits[r["record_id"]] = hits.get(r["record_id"], 0.0) + 0.4

        # 3) recency boost
        out: List[MemoryHit] = []
        for rid, score in sorted(hits.items(), key=lambda kv: -kv[1])[: limit * 3]:
            row = self._get(rid)
            if not row:
                continue
            if not include_superseded and row["superseded_by"]:
                continue
            if type and row["type"] != type:
                continue
            if project and row["project"] != project:
                continue
            if not self._readable(ctx, row):
                continue
            age_h = (now_ms() - row["updated_at"]) / 3_600_000
            recency = 1.0 / (1.0 + age_h / 24.0)
            out.append(MemoryHit(record=self._to_record(row),
                                 score=round(score + recency * row["confidence"], 4)))
        out.sort(key=lambda h: -h.score)
        return out[:limit]

    def recent(self, ctx: CallContext, limit: int = 20) -> List[MemoryHit]:
        rows = self.db.query(
            "SELECT * FROM memory_records WHERE superseded_by IS NULL ORDER BY updated_at DESC LIMIT ?",
            (limit,))
        return [MemoryHit(record=self._to_record(r), score=1.0)
                for r in rows if self._readable(ctx, r)]

    # -------------------------------------------------------------- embeddings
    def cache_embedding(self, record_id: str, vector: List[float]) -> None:
        self._vector_cache[record_id] = vector

    def cached_embedding(self, record_id: str) -> Optional[List[float]]:
        return self._vector_cache.get(record_id)

    # ---------------------------------------------------------------- helpers
    def _get(self, record_id: str):
        return self.db.query_one("SELECT * FROM memory_records WHERE record_id=?", (record_id,))

    @staticmethod
    def _to_record(row) -> MemoryRecord:
        return MemoryRecord(
            record_id=row["record_id"], type=row["type"], entity=row["entity"] or "",
            value=row["value"], confidence=row["confidence"], source=row["source"],
            person_id=row["person_id"], project=row["project"] or "",
            mission_id=row["mission_id"] or "", privacy_scope=row["privacy_scope"],
            data_class=DataClass(row["data_class"]), created_at=row["created_at"],
            updated_at=row["updated_at"], version=row["version"],
            superseded_by=row["superseded_by"], pinned=bool(row["pinned"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def _readable(self, ctx: CallContext, row) -> bool:
        scope = row["privacy_scope"] or "private:owner"
        if scope.startswith("private:"):
            return scope.split(":", 1)[1] == ctx.person_id or ctx.persona.value == "owner"
        return True

    def _fts_add(self, record_id: str, entity: str, value: str) -> None:
        try:
            self.db.execute("INSERT INTO memory_fts(record_id, entity, value) VALUES(?,?,?)",
                            (record_id, entity or "", value))
        except Exception as exc:
            log.debug("fts insert failed: %s", exc)

    def _fts_search(self, text: str, limit: int) -> List[tuple[str, float]]:
        q = " OR ".join(f'"{t}"' for t in _norm(text).split() if len(t) > 1)
        if not q:
            return []
        try:
            rows = self.db.query(
                "SELECT record_id, bm25(memory_fts) AS rank FROM memory_fts"
                " WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?", (q, limit))
            return [(r["record_id"], float(r["rank"])) for r in rows]
        except Exception as exc:
            log.debug("fts query failed (%s), falling back to LIKE", exc)
            like = f"%{text.strip()}%"
            rows = self.db.query(
                "SELECT record_id FROM memory_records WHERE value LIKE ? LIMIT ?", (like, limit))
            return [(r["record_id"], -1.0) for r in rows]

    def _audit(self, ctx: CallContext, action: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who=ctx.person_id, action=action, why="memory service",
                              result=detail, mission_id=ctx.mission_id, trace_id=ctx.trace_id)

    # ------------------------------------------------------------------ stats
    def stats(self) -> Dict[str, int]:
        total = self.db.query_one("SELECT COUNT(*) AS n FROM memory_records")["n"]
        live = self.db.query_one(
            "SELECT COUNT(*) AS n FROM memory_records WHERE superseded_by IS NULL")["n"]
        return {"total": total, "live": live, "superseded": total - live,
                "cached_vectors": len(self._vector_cache)}
