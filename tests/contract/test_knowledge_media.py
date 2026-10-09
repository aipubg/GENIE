"""Knowledge and Media: two authorities that used to answer "not available".

Both surfaces previously returned available=false because nothing owned them.
That was honest but it was not a product. These tests pin what is now real:

  Knowledge
    * is a SEPARATE store from Memory (imported documents vs learned facts)
    * records provenance (who added it, when, how many items)
    * keeps the error when an import fails instead of reporting zero items
    * removing a source never deletes the owner's files
    * search is an honest substring match with no semantic pretence

  Media
    * lists what was actually produced, with mission/agent/type/timestamp
    * reads durable storage, so last session's artifacts are still there
    * does not copy file contents just to display them
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

from core.ipc.server import IPCServer
from knowledge.service import KnowledgeService


class KnowledgeMediaDaemon:
    """Minimal daemon exposing the two services plus a live artifact registry."""

    def __init__(self, db, audit, artifacts):
        self.ready = True
        self.db = db
        self.services = {
            "db": db,
            "audit": audit,
            "knowledge": KnowledgeService(db, audit=audit),
            "artifacts": artifacts,
        }

    def status(self):
        return {"ready": True}


@pytest.fixture()
def km_server(app):
    from agents.artifacts import ArtifactService
    artifacts = ArtifactService(app.db, audit=app.audit, root=None)
    srv = IPCServer(KnowledgeMediaDaemon(app.db, app.audit, artifacts),
                    host="127.0.0.1", port=8807)
    srv.start(background=True)
    yield srv, artifacts
    srv.stop()


def _get(path, port=8807):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(path, payload, port=8807):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------------ knowledge
def test_knowledge_is_available_and_empty_not_fake(km_server, tmp_path):
    srv, _ = km_server
    code, body = _get("/api/knowledge")
    assert code == 200
    assert body["available"] is True
    # Empty is truthful: nothing imported yet, and the store exists to hold it.
    assert body["source_count"] == 0
    assert body["indexed_item_count"] == 0


def test_knowledge_add_source_records_provenance(km_server, tmp_path):
    srv, _ = km_server
    src = tmp_path / "docs"
    src.mkdir()
    (src / "notes.md").write_text("# hello\n", encoding="utf-8")
    (src / "spec.txt").write_text("a spec", encoding="utf-8")

    code, body = _post("/api/knowledge/add", {"path": str(src)})
    assert code == 200
    assert body["ok"] is True
    assert body["item_count"] >= 2, body
    assert body["kind"] == "directory"
    assert body["last_error"] in (None, "")

    code, status = _get("/api/knowledge")
    assert status["source_count"] == 1
    assert status["indexed_item_count"] >= 2
    # Provenance, not just a count.
    src_row = status["sources"][0]
    assert src_row["added_by"] == "owner"
    assert src_row["added_at"] > 0
    assert src_row["path"].endswith("docs")


def test_knowledge_reports_error_for_a_missing_path(km_server):
    srv, _ = km_server
    code, body = _post("/api/knowledge/add",
                       {"path": "definitely/not/a/real/path"})
    assert code == 200
    assert body["ok"] is False
    assert "does not exist" in body["error"]


def test_knowledge_search_is_substring_and_honest(km_server, tmp_path):
    srv, _ = km_server
    src = tmp_path / "corpus"
    src.mkdir()
    (src / "alpha.md").write_text("x", encoding="utf-8")
    (src / "beta.md").write_text("y", encoding="utf-8")
    _post("/api/knowledge/add", {"path": str(src)})

    code, body = _get("/api/knowledge/search?q=alpha")
    assert code == 200
    assert any("alpha.md" in h["path"] for h in body["hits"])
    assert not any("beta.md" in h["path"] for h in body["hits"])

    code, none = _get("/api/knowledge/search?q=zzzznothing")
    assert none["hits"] == []


def test_knowledge_remove_keeps_the_owners_files(km_server, tmp_path):
    srv, _ = km_server
    src = tmp_path / "keepme"
    src.mkdir()
    (src / "file.md").write_text("content", encoding="utf-8")
    added = _post("/api/knowledge/add", {"path": str(src)})[1]

    code, body = _post("/api/knowledge/remove", {"source_id": added["source_id"]})
    assert code == 200
    assert body["ok"] is True
    # The contract that matters: removing knowledge is not deleting data.
    assert body["files_deleted"] is False
    assert (src / "file.md").exists()

    _, status = _get("/api/knowledge")
    assert status["source_count"] == 0


def test_knowledge_is_a_different_store_from_memory(km_server, tmp_path):
    """Guard against Knowledge quietly becoming a second Memory."""
    srv, _ = km_server
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.md").write_text("x", encoding="utf-8")
    _post("/api/knowledge/add", {"path": str(src)})

    rows = srv.daemon.db.query("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    assert "knowledge_sources" in names
    assert "memory_records" in names          # memory still exists, separately
    # Nothing was written into memory by importing knowledge.
    mem = srv.daemon.db.query_one("SELECT COUNT(*) AS n FROM memory_records")
    assert int(mem["n"]) == 0


# ---------------------------------------------------------------------- media
def test_media_starts_empty_and_available(km_server):
    srv, _ = km_server
    code, body = _get("/api/media")
    assert code == 200
    assert body["available"] is True
    assert body["count"] == 0


def test_media_lists_produced_artifacts_with_provenance(km_server, tmp_path):
    srv, artifacts = km_server
    produced = tmp_path / "report.md"
    produced.write_text("# produced by an agent\n", encoding="utf-8")

    art = artifacts.register_file(produced, producer_agent="writer-1",
                                  mission_id="m-42", task_id="t-7",
                                  kind="document")

    code, body = _get("/api/media")
    assert code == 200
    assert body["count"] == 1
    item = body["items"][0]
    assert item["type"] == "document"
    assert item["producing_mission"] == "m-42"
    assert item["producing_agent"] == "writer-1"
    assert item["task"] == "t-7"
    assert item["created_ms"] > 0
    assert item["exists"] is True
    # A reference, never a copy of the bytes.
    assert "content" not in item
    assert item["path"].endswith("report.md")
    assert art.artifact_id == item["id"]


def test_media_filters_by_mission(km_server, tmp_path):
    srv, artifacts = km_server
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    artifacts.register_file(a, producer_agent="x", mission_id="m-one", kind="document")
    artifacts.register_file(b, producer_agent="x", mission_id="m-two", kind="document")

    _, body = _get("/api/media?mission=m-one")
    assert body["count"] == 1
    assert body["items"][0]["producing_mission"] == "m-one"


def test_media_reports_missing_files_honestly(km_server, tmp_path):
    srv, artifacts = km_server
    gone = tmp_path / "will-vanish.md"
    gone.write_text("x", encoding="utf-8")
    artifacts.register_file(gone, producer_agent="x", mission_id="m")
    gone.unlink()

    _, body = _get("/api/media")
    assert body["count"] == 1
    # The artifact is still known; the file is not. Say which.
    assert body["items"][0]["exists"] is False
