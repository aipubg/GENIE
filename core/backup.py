"""Backup & restore (core/backup.py) — Phase 14.1.

Hardening for daily use: the owner's memory, missions and configuration must survive a bad
update, a corrupted file or a mistake.

Two rules drive the design:

1. **Never copy a live database file.** GENIE runs SQLite in WAL mode; `shutil.copy` on an open
   WAL database can capture a torn state. Every database snapshot goes through SQLite's own
   online backup API, which produces a transactionally consistent copy.
2. **Verify before restoring.** Each backup records a sha256 per file. ``restore()`` re-checks
   them and **refuses** to apply a tampered or truncated archive — restoring corrupt data over
   good data is worse than not restoring at all.

A backup contains: the database, the user configuration, and (by default) the encrypted vault.
The vault holds secrets, so it is opt-out and clearly labelled in the manifest.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.backup")

MANIFEST_NAME = "manifest.json"
DB_NAME = "genie.db"
CONFIG_NAME = "user.json"
VAULT_NAME = "vault.enc"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_snapshot(src: Path, dest: Path) -> None:
    """Consistent copy of an open SQLite database (safe under WAL)."""
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class BackupService:
    """Create, list, verify and restore GENIE backups."""

    def __init__(self, *, db_path: Optional[Path] = None,
                 user_file: Optional[Path] = None,
                 vault_path: Optional[Path] = None,
                 version: str = "dev"):
        self.db_path = Path(db_path) if db_path else None
        self.user_file = Path(user_file) if user_file else None
        self.vault_path = Path(vault_path) if vault_path else None
        self.version = version

    # ---------------------------------------------------------------- create
    def create(self, dest_dir: Path | str, *, include_vault: bool = True,
               note: str = "") -> Dict[str, Any]:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        backup_id = f"bk-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        target = dest / backup_id
        target.mkdir(parents=True, exist_ok=True)

        files: List[Dict[str, Any]] = []

        if self.db_path and self.db_path.exists():
            out = target / DB_NAME
            _db_snapshot(self.db_path, out)
            files.append({"name": DB_NAME, "sha256": _sha256(out), "bytes": out.stat().st_size})

        if self.user_file and self.user_file.exists():
            out = target / CONFIG_NAME
            shutil.copy2(self.user_file, out)
            files.append({"name": CONFIG_NAME, "sha256": _sha256(out),
                          "bytes": out.stat().st_size})

        if include_vault and self.vault_path and self.vault_path.exists():
            out = target / VAULT_NAME
            shutil.copy2(self.vault_path, out)
            files.append({"name": VAULT_NAME, "sha256": _sha256(out),
                          "bytes": out.stat().st_size, "contains_secrets": True})

        manifest = {"backup_id": backup_id, "created_ms": int(time.time() * 1000),
                    "version": self.version, "note": note,
                    "includes_vault": include_vault, "files": files}
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["path"] = str(target)
        return manifest

    # ------------------------------------------------------------------ list
    def list(self, dest_dir: Path | str) -> List[Dict[str, Any]]:
        dest = Path(dest_dir)
        if not dest.is_dir():
            return []
        out: List[Dict[str, Any]] = []
        for entry in sorted(dest.iterdir(), reverse=True):
            manifest_path = entry / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data["path"] = str(entry)
            out.append(data)
        return out

    # ----------------------------------------------------------------- prune
    def prune(self, dest_dir: Path | str, *, keep_last: int = 5,
              dry_run: bool = False) -> Dict[str, Any]:
        """Drop old backups beyond the retention count.

        Backups otherwise accumulate forever, which quietly defeats the point of the storage
        quota. The newest ``keep_last`` are always kept, and ``dry_run`` reports first so the
        owner is never surprised by a deletion.
        """
        items = self.list(dest_dir)  # newest first (ids sort chronologically)
        if len(items) <= keep_last:
            return {"ok": True, "removed": [], "kept": len(items), "dry_run": dry_run}

        removed: List[str] = []
        for entry in items[keep_last:]:
            path = Path(entry.get("path", ""))
            if not path.is_dir():
                continue
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
            removed.append(entry.get("backup_id", path.name))
        return {"ok": True, "removed": removed, "kept": min(keep_last, len(items)),
                "dry_run": dry_run}

    # ---------------------------------------------------------------- verify
    def verify(self, backup: Path | str) -> Dict[str, Any]:
        """Recompute every hash. A tampered or truncated backup must not verify."""
        path = Path(backup)
        manifest_path = path / MANIFEST_NAME
        if not manifest_path.is_file():
            return {"ok": False, "error": f"no {MANIFEST_NAME} in {path}"}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"unreadable manifest: {exc}"}

        problems: List[str] = []
        checked = 0
        for entry in manifest.get("files", []):
            target = path / entry["name"]
            if not target.is_file():
                problems.append(f"missing file: {entry['name']}")
                continue
            if _sha256(target) != entry["sha256"]:
                problems.append(f"checksum mismatch: {entry['name']}")
                continue
            checked += 1
        if not manifest.get("files"):
            problems.append("backup contains no files")
        return {"ok": not problems, "checked": checked, "problems": problems,
                "backup_id": manifest.get("backup_id", ""), "path": str(path)}

    # --------------------------------------------------------------- restore
    def restore(self, backup: Path | str, *, verify: bool = True,
                restore_vault: bool = True) -> Dict[str, Any]:
        """Apply a backup. Verifies integrity first and refuses bad archives."""
        path = Path(backup)
        if verify:
            check = self.verify(path)
            if not check["ok"]:
                return {"ok": False, "error": "backup failed verification — refusing to restore",
                        "problems": check["problems"]}

        manifest = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
        restored: List[str] = []

        db_src = path / DB_NAME
        if db_src.is_file() and self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            _db_snapshot(db_src, self.db_path)
            restored.append(DB_NAME)

        cfg_src = path / CONFIG_NAME
        if cfg_src.is_file() and self.user_file:
            self.user_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfg_src, self.user_file)
            restored.append(CONFIG_NAME)

        vault_src = path / VAULT_NAME
        if restore_vault and vault_src.is_file() and self.vault_path:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vault_src, self.vault_path)
            restored.append(VAULT_NAME)

        return {"ok": True, "restored": restored,
                "backup_id": manifest.get("backup_id", ""),
                "note": "restart GENIE so services re-open the restored database"}
