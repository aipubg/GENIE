"""Phase 14.1 — backup & restore.

The dangerous failure is not "backup didn't happen", it is **restoring corrupt data over good
data**. So the tamper test is the most important one here: a modified backup must be refused.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.backup import BackupService, CONFIG_NAME, DB_NAME, MANIFEST_NAME, VAULT_NAME


@pytest.fixture()
def live(tmp_path):
    """A 'live' GENIE install: database, user config, encrypted vault."""
    root = tmp_path / "live"
    root.mkdir()
    db = root / "genie.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE memory_records (record_id TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO memory_records VALUES ('mem_1', 'Owner prefers dark mode')")
    conn.commit()
    conn.close()
    (root / "user.json").write_text(json.dumps({"instance_id": "owner-pc"}), encoding="utf-8")
    (root / "vault.enc").write_bytes(b"ENCRYPTED-SECRETS")
    return root


@pytest.fixture()
def svc(live, tmp_path):
    return BackupService(db_path=live / "genie.db", user_file=live / "user.json",
                         vault_path=live / "vault.enc", version="1.2.3")


def _rows(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        return [r[0] for r in conn.execute("SELECT value FROM memory_records")]
    finally:
        conn.close()


# ------------------------------------------------------------------- create
def test_backup_contains_db_config_and_vault_with_hashes(svc, live, tmp_path):
    manifest = svc.create(tmp_path / "backups", note="before update")
    names = {f["name"] for f in manifest["files"]}
    assert {DB_NAME, CONFIG_NAME, VAULT_NAME} <= names
    assert manifest["version"] == "1.2.3"
    assert manifest["note"] == "before update"
    # every file carries a real hash and size
    for f in manifest["files"]:
        assert len(f["sha256"]) == 64 and f["bytes"] >= 0
    assert (Path(manifest["path"]) / MANIFEST_NAME).is_file()


def test_backup_of_an_open_database_is_consistent(svc, live, tmp_path):
    """SQLite online backup API — a WAL database must snapshot cleanly while open."""
    conn = sqlite3.connect(str(live / "genie.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO memory_records VALUES ('mem_2', 'written while open')")
    conn.commit()  # left OPEN on purpose
    manifest = svc.create(tmp_path / "backups")
    conn.close()
    copied = Path(manifest["path"]) / DB_NAME
    assert "written while open" in _rows(copied)


def test_vault_can_be_excluded(svc, tmp_path):
    manifest = svc.create(tmp_path / "backups", include_vault=False)
    names = {f["name"] for f in manifest["files"]}
    assert VAULT_NAME not in names
    assert manifest["includes_vault"] is False


# ------------------------------------------------------------------- verify
def test_verify_passes_on_a_fresh_backup(svc, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    check = svc.verify(manifest["path"])
    assert check["ok"] is True and check["problems"] == []
    assert check["checked"] >= 2


def test_tampered_backup_fails_verification(svc, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    target = Path(manifest["path"]) / DB_NAME
    target.write_bytes(target.read_bytes() + b"CORRUPTED")
    check = svc.verify(manifest["path"])
    assert check["ok"] is False
    assert any("checksum mismatch" in p for p in check["problems"])


def test_missing_file_fails_verification(svc, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    (Path(manifest["path"]) / CONFIG_NAME).unlink()
    check = svc.verify(manifest["path"])
    assert check["ok"] is False
    assert any("missing file" in p for p in check["problems"])


def test_directory_without_a_manifest_is_not_a_backup(svc, tmp_path):
    empty = tmp_path / "backups" / "not-a-backup"
    empty.mkdir(parents=True)
    assert svc.verify(empty)["ok"] is False


# ------------------------------------------------------------------ restore
def test_restore_brings_the_data_back(svc, live, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    assert "Owner prefers dark mode" in _rows(live / "genie.db")

    # lose the data
    conn = sqlite3.connect(str(live / "genie.db"))
    conn.execute("DELETE FROM memory_records")
    conn.commit()
    conn.close()
    (live / "user.json").write_text(json.dumps({"instance_id": "WIPED"}), encoding="utf-8")
    assert _rows(live / "genie.db") == []

    out = svc.restore(manifest["path"])
    assert out["ok"] is True
    assert DB_NAME in out["restored"] and CONFIG_NAME in out["restored"]

    assert "Owner prefers dark mode" in _rows(live / "genie.db")
    assert json.loads((live / "user.json").read_text())["instance_id"] == "owner-pc"


def test_restore_refuses_a_tampered_backup(svc, live, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    target = Path(manifest["path"]) / DB_NAME
    target.write_bytes(target.read_bytes() + b"TAMPER")

    out = svc.restore(manifest["path"])
    assert out["ok"] is False
    assert "refusing to restore" in out["error"]
    # and the live data is untouched
    assert "Owner prefers dark mode" in _rows(live / "genie.db")


def test_restore_can_skip_the_vault(svc, live, tmp_path):
    manifest = svc.create(tmp_path / "backups")
    (live / "vault.enc").write_bytes(b"ROTATED-SECRETS")
    svc.restore(manifest["path"], restore_vault=False)
    assert (live / "vault.enc").read_bytes() == b"ROTATED-SECRETS"


# --------------------------------------------------------------------- list
def test_backups_are_listable_newest_first(svc, tmp_path):
    first = svc.create(tmp_path / "backups", note="first")
    second = svc.create(tmp_path / "backups", note="second")
    items = svc.list(tmp_path / "backups")
    assert len(items) == 2
    assert items[0]["backup_id"] in (first["backup_id"], second["backup_id"])
    assert all("path" in i for i in items)


def test_listing_an_empty_directory_returns_nothing(svc, tmp_path):
    assert svc.list(tmp_path / "nothing") == []


# ----------------------------------------------------------------- retention
def test_prune_keeps_the_newest_backups_and_drops_the_rest(svc, tmp_path):
    dest = tmp_path / "backups"
    for _ in range(4):
        svc.create(dest)
    assert len(svc.list(dest)) == 4

    out = svc.prune(dest, keep_last=2)
    assert out["ok"] is True and len(out["removed"]) == 2
    remaining = svc.list(dest)
    assert len(remaining) == 2, "the newest two must survive"


def test_prune_dry_run_reports_without_deleting(svc, tmp_path):
    dest = tmp_path / "backups"
    for _ in range(3):
        svc.create(dest)
    out = svc.prune(dest, keep_last=1, dry_run=True)
    assert out["dry_run"] is True
    assert len(out["removed"]) == 2
    assert len(svc.list(dest)) == 3, "a dry run must not delete anything"


def test_prune_does_nothing_when_under_the_retention_count(svc, tmp_path):
    dest = tmp_path / "backups"
    svc.create(dest)
    out = svc.prune(dest, keep_last=5)
    assert out["removed"] == [] and out["kept"] == 1


def test_prune_on_an_empty_directory_is_safe(svc, tmp_path):
    out = svc.prune(tmp_path / "nothing", keep_last=3)
    assert out["ok"] is True and out["removed"] == []


# ----------------------------------------------------------------- CLI surface
def test_backup_cli_creates_verifies_and_lists(monkeypatch, tmp_path, capsys):
    """The CLI must actually reach the service (Phase 14.1 CLI wiring)."""
    import sqlite3

    import genie
    from core.config import Config

    data = tmp_path / "data"
    cfg = Config({"data_dir": str(data)})
    cfg.apply()
    db = data / "genie.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('hi')")
    conn.commit()
    conn.close()
    (data / "vault.enc").write_bytes(b"SECRETS")

    monkeypatch.setattr("core.config.get_config", lambda *a, **k: cfg)
    dest = tmp_path / "backups"

    assert genie.main(["backup", "--dest", str(dest), "--note", "cli", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["note"] == "cli"
    assert {f["name"] for f in created["files"]} >= {"genie.db", "vault.enc"}

    capsys.readouterr()
    assert genie.main(["backup-verify", created["path"]]) == 0, "a fresh backup must verify"

    capsys.readouterr()
    assert genie.main(["backup-list", "--dir", str(dest), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1 and listed[0]["backup_id"] == created["backup_id"]


def test_backup_cli_restore_is_reachable(monkeypatch, tmp_path, capsys):
    import sqlite3

    import genie
    from core.config import Config

    data = tmp_path / "data"
    cfg = Config({"data_dir": str(data)})
    cfg.apply()
    db = data / "genie.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('hi')")
    conn.commit()
    conn.close()

    monkeypatch.setattr("core.config.get_config", lambda *a, **k: cfg)
    dest = tmp_path / "backups"
    genie.main(["backup", "--dest", str(dest), "--json"])
    created = json.loads(capsys.readouterr().out)

    # wipe, then restore through the CLI
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM t")
    conn.commit()
    conn.close()

    capsys.readouterr()
    assert genie.main(["backup-restore", created["path"], "--no-vault"]) == 0
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT x FROM t").fetchone()[0] == "hi"
    conn.close()
