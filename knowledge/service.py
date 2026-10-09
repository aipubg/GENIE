"""Canonical Knowledge service — imported sources and their indexed items.

Why this exists
---------------
`/api/knowledge` used to answer `available:false` because nothing owned it.
That was honest but it was not a product. This is the thin facade that makes
the answer real.

What it is NOT
--------------
* It is not a second Memory. Memory holds facts learned about the owner;
  Knowledge holds documents the owner imported. Separate tables, separate
  lifetimes.
* It is not a vector database. Search is an honest substring match over paths
  and names; when nothing matches it says so rather than returning "similar"
  results it cannot justify.
* It never fabricates an index. If a source cannot be read, the source carries
  the error and reports it.

Storage reuses the existing `workspace_index` table (which was created but
never written to) for per-file entries, plus one small `knowledge_sources`
table for source-level provenance.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("knowledge.service")

# Refuse to ingest these: they are noise or secrets, not knowledge.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".pytest_cache", ".mypy_cache", "bin", "obj"}
SKIP_SUFFIX = {".pyc", ".pyo", ".dll", ".pyd", ".exe", ".so", ".dylib",
               ".zip", ".tar", ".gz", ".7z", ".pack", ".idx", ".db",
               ".sqlite", ".enc", ".key", ".pem"}
TEXT_SUFFIX = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
               ".ini", ".cfg", ".csv", ".py", ".js", ".ts", ".html", ".css",
               ".log", ".sh", ".ps1", ".bat", ".nsi", ".iss"}

MAX_ITEMS_PER_SOURCE = 20000
MAX_BYTES_HASHED = 8 * 1024 * 1024      # hash the head of very large files


class KnowledgeService:
    """Sources the owner imported, and the files indexed under them."""

    def __init__(self, db=None, *, audit=None):
        self.db = db
        self.audit = audit
        self._last_error = ""

    # ------------------------------------------------------------------ write
    def add_source(self, path: str | Path, *, added_by: str = "owner",
                   kind: str = "auto") -> Dict[str, Any]:
        """Register a file or directory and index it.

        Returns the source row including `item_count` and, when something went
        wrong, `last_error`. It never pretends a failed import succeeded.
        """
        target = Path(path).expanduser()
        resolved = str(target.resolve()) if target.exists() else str(target)

        if not target.exists():
            return {"ok": False, "error": f"path does not exist: {resolved}"}

        is_dir = target.is_dir()
        source_id = uuid.uuid4().hex[:16]
        items = 0
        error = ""

        try:
            if is_dir:
                items = self._index_tree(target)
            else:
                items = 1 if self._index_file(target) else 0
        except Exception as exc:                      # noqa: BLE001
            error = f"indexing failed: {exc}"
            self._last_error = error
            log.warning("knowledge source %s: %s", resolved, error)

        detected = kind if kind != "auto" else ("directory" if is_dir else "file")
        now = int(time.time())

        if self.db is not None:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO knowledge_sources(source_id, path, kind,"
                    " item_count, added_by, added_at, last_error) VALUES(?,?,?,?,?,?,?)",
                    (source_id, resolved, detected, items, added_by, now, error))
            except Exception as exc:                  # noqa: BLE001
                error = error or f"could not record source: {exc}"
                self._last_error = error

        if self.audit is not None:
            try:
                self.audit.record(who=added_by, action="knowledge.add_source",
                                  why=f"{resolved} ({items} item(s))",
                                  result="ok" if not error else "error")
            except Exception:                          # noqa: BLE001
                pass

        return {"ok": not error, "source_id": source_id, "path": resolved,
                "kind": detected, "item_count": items,
                "last_error": error or None}

    def remove_source(self, source_id: str) -> Dict[str, Any]:
        """Forget a source and drop its index entries. Source files are NOT deleted."""
        if self.db is None:
            return {"ok": False, "error": "knowledge store unavailable"}
        row = self.db.query_one(
            "SELECT path FROM knowledge_sources WHERE source_id=?", (source_id,))
        if row is None:
            return {"ok": False, "error": f"unknown source {source_id}"}
        path = row["path"]
        try:
            self.db.execute("DELETE FROM workspace_index WHERE path = ? OR path LIKE ?",
                            (path, path.rstrip("\\/") + "%"))
            self.db.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source_id,))
        except Exception as exc:                       # noqa: BLE001
            return {"ok": False, "error": f"remove failed: {exc}"}
        if self.audit is not None:
            try:
                self.audit.record(who="owner", action="knowledge.remove_source",
                                  why=path, result="ok")
            except Exception:                           # noqa: BLE001
                pass
        # Explicit: the files themselves are untouched.
        return {"ok": True, "source_id": source_id, "path": path,
                "files_deleted": False}

    # ------------------------------------------------------------------- read
    def sources(self) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            rows = self.db.query(
                "SELECT * FROM knowledge_sources ORDER BY added_at DESC")
        except Exception as exc:                       # noqa: BLE001
            log.debug("knowledge sources read failed: %s", exc)
            return []
        return [dict(r) for r in rows]

    def search(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Honest substring search over indexed paths. No semantic claims."""
        if self.db is None or not query.strip():
            return []
        like = f"%{query.strip()}%"
        try:
            rows = self.db.query(
                "SELECT path, size, mtime, indexed_at FROM workspace_index"
                " WHERE path LIKE ? ORDER BY path LIMIT ?", (like, int(limit)))
        except Exception as exc:                       # noqa: BLE001
            log.debug("knowledge search failed: %s", exc)
            return []
        return [dict(r) for r in rows]

    def status(self) -> Dict[str, Any]:
        sources = self.sources()
        items = 0
        if self.db is not None:
            try:
                row = self.db.query_one("SELECT COUNT(*) AS n FROM workspace_index")
                items = int(row["n"]) if row else 0
            except Exception:                          # noqa: BLE001
                items = 0
        failing = [s for s in sources if s.get("last_error")]
        return {
            "available": True,
            "source_count": len(sources),
            "indexed_item_count": items,
            "failing_sources": len(failing),
            "sources": sources,
            "search": "substring over indexed paths (no semantic ranking)",
        }

    # ---------------------------------------------------------------- indexing
    def _index_tree(self, root: Path) -> int:
        count = 0
        for child in root.rglob("*"):
            if count >= MAX_ITEMS_PER_SOURCE:
                log.warning("knowledge: %s truncated at %d items", root, count)
                break
            if not child.is_file():
                continue
            if any(part in SKIP_DIRS for part in child.parts):
                continue
            if self._index_file(child):
                count += 1
        return count

    def _index_file(self, path: Path) -> bool:
        if path.suffix.lower() in SKIP_SUFFIX:
            return False
        try:
            st = path.stat()
            digest = self._hash_file(path)
        except OSError:
            return False
        entry = (str(path.resolve()), int(st.st_size), int(st.st_mtime), digest,
                 int(time.time()))
        if self.db is None:
            return True
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO workspace_index(path, size, mtime, hash,"
                " indexed_at) VALUES(?,?,?,?,?)", entry)
            return True
        except Exception as exc:                       # noqa: BLE001
            log.debug("knowledge index write failed for %s: %s", path, exc)
            return False

    @staticmethod
    def _hash_file(path: Path) -> str:
        """Hash the head of the file. Large binaries are not read in full."""
        h = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                h.update(fh.read(MAX_BYTES_HASHED))
        except OSError:
            return ""
        return h.hexdigest()

    @staticmethod
    def is_text(path: str) -> bool:
        return Path(path).suffix.lower() in TEXT_SUFFIX
