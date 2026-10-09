"""Artifact service (agents/artifacts.py) — master spec §10.7.

Agent-produced files go through here, never through messages. A message carries an `artifact_id`;
the bytes live in one place with a hash, a version, a producer and its dependencies.

That is what stops the two failure modes the spec names: sending file contents back and forth
through agent messages, and two agents each producing their own copy of the same file.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from agents.contracts import Artifact, ArtifactKind

log = get_logger("agents.artifacts")


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ArtifactService:
    """Registers, versions and resolves artifacts. Content is read on demand, never pushed."""

    def __init__(self, db=None, *, audit=None, root: Optional[Path] = None):
        self.db = db
        self.audit = audit
        self.root = Path(root) if root else None
        self._artifacts: Dict[str, Artifact] = {}
        self._by_path: Dict[str, str] = {}          # path -> latest artifact_id

    # ------------------------------------------------------------------ write
    def register_file(self, path: str | Path, *, producer_agent: str = "",
                      mission_id: str = "", task_id: str = "",
                      dependencies: Optional[List[str]] = None,
                      kind: str = ArtifactKind.FILE.value) -> Artifact:
        """Register a real file: hash it, size it, and remember who produced it."""
        target = Path(path)
        payload = target.read_bytes() if target.exists() else b""
        digest = _hash_bytes(payload)
        key = str(target)
        previous_id = self._by_path.get(key)
        version = (self._artifacts[previous_id].version + 1) if previous_id else 1
        artifact = Artifact(kind=kind, name=target.name, path=key, sha256=digest,
                            bytes=len(payload), version=version,
                            producer_agent=producer_agent, mission_id=mission_id,
                            task_id=task_id, dependencies=list(dependencies or []))
        self._store(artifact)
        self._by_path[key] = artifact.artifact_id
        return artifact

    def register_content(self, name: str, content: Any, *,
                         kind: str = ArtifactKind.DOCUMENT.value, producer_agent: str = "",
                         mission_id: str = "", task_id: str = "",
                         dependencies: Optional[List[str]] = None) -> Artifact:
        """Register inline content (a contract, a decision record, a test result)."""
        payload = json.dumps(content, sort_keys=True, default=str).encode("utf-8")
        artifact = Artifact(kind=kind, name=name, sha256=_hash_bytes(payload),
                            bytes=len(payload), producer_agent=producer_agent,
                            mission_id=mission_id, task_id=task_id, content=content,
                            dependencies=list(dependencies or []))
        self._store(artifact)
        return artifact

    def _store(self, artifact: Artifact) -> None:
        self._artifacts[artifact.artifact_id] = artifact
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO agent_artifacts(artifact_id, kind, name, path, sha256,"
                " bytes, version, producer_agent, mission_id, task_id, dependencies, content,"
                " created_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact.artifact_id, artifact.kind, artifact.name, artifact.path,
                 artifact.sha256, artifact.bytes, artifact.version, artifact.producer_agent,
                 artifact.mission_id, artifact.task_id, json.dumps(artifact.dependencies),
                 json.dumps(artifact.content, default=str) if artifact.content is not None
                 else None, artifact.created_ms))
        except Exception as exc:
            log.debug("artifact persist failed: %s", exc)
        if self.audit:
            try:
                self.audit.record(who=artifact.producer_agent or "system",
                                  action="artifact.register",
                                  why=f"{artifact.name} v{artifact.version}",
                                  mission_id=artifact.mission_id or None, result="ok")
            except Exception as exc:
                log.debug("artifact audit failed: %s", exc)

    # ------------------------------------------------------------------- read
    def get(self, artifact_id: str) -> Optional[Artifact]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is not None:
            return artifact
        if self.db is None:
            return None
        row = self.db.query_one("SELECT * FROM agent_artifacts WHERE artifact_id=?",
                                (artifact_id,))
        if row is None:
            return None
        artifact = Artifact(
            artifact_id=row["artifact_id"], kind=row["kind"], name=row["name"],
            path=row["path"] or "", sha256=row["sha256"] or "", bytes=int(row["bytes"] or 0),
            version=int(row["version"] or 1), producer_agent=row["producer_agent"] or "",
            mission_id=row["mission_id"] or "", task_id=row["task_id"] or "",
            dependencies=json.loads(row["dependencies"] or "[]"),
            content=json.loads(row["content"]) if row["content"] else None,
            created_ms=int(row["created_ms"] or 0))
        self._artifacts[artifact_id] = artifact
        return artifact

    def read_text(self, artifact_id: str, *, max_bytes: int = 200_000) -> str:
        """Read an artifact's text. This is the *only* way contents move between agents."""
        artifact = self.get(artifact_id)
        if artifact is None:
            return ""
        if artifact.content is not None:
            return json.dumps(artifact.content, indent=2, default=str)[:max_bytes]
        if artifact.path:
            try:
                return Path(artifact.path).read_text(encoding="utf-8", errors="replace")[:max_bytes]
            except OSError as exc:
                log.warning("cannot read artifact %s: %s", artifact_id, exc)
        return ""

    def verify(self, artifact_id: str) -> Dict[str, Any]:
        """Confirm the bytes on disk still match the hash we recorded."""
        artifact = self.get(artifact_id)
        if artifact is None:
            return {"ok": False, "error": f"unknown artifact {artifact_id}"}
        if not artifact.path:
            return {"ok": True, "detail": "inline content (no file to verify)"}
        target = Path(artifact.path)
        if not target.exists():
            return {"ok": False, "detail": f"{artifact.path} no longer exists"}
        digest = _hash_bytes(target.read_bytes())
        return {"ok": digest == artifact.sha256, "detail": f"sha256 {'matches' if digest == artifact.sha256 else 'DIFFERS'}",
                "sha256": digest}

    def for_mission(self, mission_id: str) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._artifacts.values() if a.mission_id == mission_id]

    def media(self, *, mission_id: str = "", kind: str = "",
               limit: int = 100) -> Dict[str, Any]:
        """What GENIE has produced — the Media surface.

        Reads the durable table, not only the in-memory map: an artifact made
        in a previous session is still media. Only the reference is returned;
        the bytes are never copied or embedded just to be listed.
        """
        items: List[Dict[str, Any]] = []
        if self.db is not None:
            try:
                sql = ("SELECT artifact_id, kind, name, path, sha256, bytes, version,"
                       " producer_agent, mission_id, task_id, created_ms"
                       " FROM agent_artifacts")
                where, args = [], []
                if mission_id:
                    where.append("mission_id = ?")
                    args.append(mission_id)
                if kind:
                    where.append("kind = ?")
                    args.append(kind)
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY created_ms DESC LIMIT ?"
                args.append(int(limit))
                for row in self.db.query(sql, tuple(args)):
                    items.append(self._media_item(dict(row)))
            except Exception as exc:                   # noqa: BLE001
                log.debug("media query failed: %s", exc)

        if not items:
            # Fall back to what is in memory (no db, or nothing persisted yet).
            for a in self._artifacts.values():
                if mission_id and a.mission_id != mission_id:
                    continue
                if kind and a.kind != kind:
                    continue
                items.append(self._media_item(a.to_dict()))
            items.sort(key=lambda x: x.get("created_ms", 0), reverse=True)
            items = items[:int(limit)]

        kinds: Dict[str, int] = {}
        for it in items:
            kinds[it["type"]] = kinds.get(it["type"], 0) + 1
        return {"available": True, "count": len(items), "items": items,
                "by_kind": kinds,
                "note": "references only - files are not copied into Media"}

    @staticmethod
    def _media_item(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise one artifact for display, including honest file state."""
        path = row.get("path") or ""
        exists = bool(path) and Path(path).exists()
        return {
            "id": row.get("artifact_id", ""),
            "type": row.get("kind", "other"),
            "name": row.get("name", ""),
            "path": path,
            "exists": exists,
            "bytes": int(row.get("bytes") or 0),
            "sha256": (row.get("sha256") or "")[:16],
            "version": int(row.get("version") or 1),
            "producing_mission": row.get("mission_id", ""),
            "producing_agent": row.get("producer_agent", ""),
            "task": row.get("task_id", ""),
            "created_ms": int(row.get("created_ms") or 0),
        }

    def latest_for_path(self, path: str) -> Optional[Artifact]:
        artifact_id = self._by_path.get(str(path))
        return self.get(artifact_id) if artifact_id else None

    def status(self) -> Dict[str, Any]:
        return {"count": len(self._artifacts),
                "files": len([a for a in self._artifacts.values() if a.path]),
                "bytes": sum(a.bytes for a in self._artifacts.values())}
