"""Plugin discovery + lifecycle (plugins/registry.py).

Lifecycle (owner-specified):

    discover -> validate -> install -> initialize -> health-check -> invoke
    -> stop -> update -> uninstall

Plugins live in `plugins/installed/<id>/` and are found by scanning for `plugin.json` — a new
plugin is added by dropping a folder in, never by editing GENIE source.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from plugins.sdk import (PluginError, PluginManifest, load_manifest, platform_supported)

log = get_logger("plugins.registry")

INSTALLED_DIRNAME = "installed"


@dataclass
class PluginRecord:
    manifest: PluginManifest
    directory: Path
    enabled: bool = True
    installed_at: int = 0
    granted_permissions: List[str] = field(default_factory=list)
    state: str = "installed"
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.manifest.id, "name": self.manifest.name,
            "version": self.manifest.version, "directory": str(self.directory),
            "enabled": self.enabled, "installed_at": self.installed_at,
            "permissions": self.manifest.permissions,
            "granted_permissions": self.granted_permissions,
            "capabilities": [c.to_dict() for c in self.manifest.capabilities],
            "observations": self.manifest.observations,
            "platforms": self.manifest.platforms, "state": self.state,
            "first_party": self.manifest.first_party, "last_error": self.last_error,
        }


class PluginRegistry:
    def __init__(self, db, root: str | Path | None = None):
        self.db = db
        self.root = Path(root) if root else Path(__file__).resolve().parents[1] / "plugins"
        self.installed_dir = self.root / INSTALLED_DIRNAME
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, PluginRecord] = {}

    # ---------------------------------------------------------------- discovery
    def discover(self) -> List[Dict[str, Any]]:
        """Scan the plugins directory. Invalid plugins are reported, never loaded."""
        found: List[Dict[str, Any]] = []
        for directory in sorted(self.installed_dir.iterdir()):
            if not directory.is_dir() or directory.name.startswith((".", "_test_")):
                continue
            if not (directory / "plugin.json").exists():
                continue
            try:
                manifest = load_manifest(directory)
            except PluginError as exc:
                found.append({"directory": str(directory), "ok": False,
                              "error": exc.to_dict()})
                log.warning("invalid plugin in %s: %s", directory, exc.message)
                continue
            found.append({"directory": str(directory), "ok": True,
                          "id": manifest.id, "version": manifest.version})
        return found

    def load_all(self) -> Dict[str, PluginRecord]:
        """Load every valid plugin and merge its persisted state from the database."""
        self._records = {}
        for directory in sorted(self.installed_dir.iterdir()):
            if directory.name.startswith((".", "_test_")):
                continue
            if not directory.is_dir() or not (directory / "plugin.json").exists():
                continue
            try:
                manifest = load_manifest(directory)
            except PluginError:
                continue
            row = self.db.query_one("SELECT * FROM plugins WHERE id=?", (manifest.id,))
            record = PluginRecord(manifest=manifest, directory=directory)
            if row:
                record.enabled = bool(row["enabled"])
                record.installed_at = int(row["installed_at"] or 0)
                record.granted_permissions = json.loads(row["granted_permissions"] or "[]")
                record.state = row["state"] or "installed"
            else:
                self._persist(record)
            if not platform_supported(manifest):
                record.state = "unsupported"
                record.last_error = f"platform {manifest.platforms} does not include this machine"
            self._records[manifest.id] = record
        log.info("plugin registry: %s plugin(s) found", len(self._records))
        return self._records

    # ------------------------------------------------------------------- state
    def all(self) -> List[PluginRecord]:
        if not self._records:
            self.load_all()
        return list(self._records.values())

    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        if not self._records:
            self.load_all()
        return self._records.get(plugin_id)

    def _persist(self, record: PluginRecord) -> None:
        self.db.execute(
            "INSERT INTO plugins(id, path, version, enabled, installed_at,"
            " granted_permissions, state) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET path=excluded.path, version=excluded.version,"
            " enabled=excluded.enabled, granted_permissions=excluded.granted_permissions,"
            " state=excluded.state",
            (record.manifest.id, str(record.directory), record.manifest.version,
             1 if record.enabled else 0, record.installed_at or int(time.time()),
             json.dumps(record.granted_permissions), record.state))

    # ----------------------------------------------------------------- lifecycle
    def install(self, source: str | Path, *, copy: bool = True,
                grant_permissions: Optional[List[str]] = None) -> Dict[str, Any]:
        """Validate and install a plugin directory.

        Installation does **not** silently grant permissions: unless the caller explicitly
        passes `grant_permissions`, the plugin is installed with none (default deny).
        """
        source_path = Path(source).resolve()
        if not (source_path / "plugin.json").exists():
            return {"ok": False, "error": f"no plugin.json in {source_path}"}
        try:
            manifest = load_manifest(source_path)
        except PluginError as exc:
            return {"ok": False, "error": exc.message, "detail": exc.to_dict()}

        target = source_path
        if copy and source_path.parent != self.installed_dir:
            target = self.installed_dir / manifest.id
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source_path, target)
            manifest = load_manifest(target)

        record = PluginRecord(manifest=manifest, directory=target,
                              installed_at=int(time.time()),
                              granted_permissions=list(grant_permissions or []))
        self._records[manifest.id] = record
        self._persist(record)
        log.info("plugin %s installed at %s (permissions granted: %s)",
                 manifest.id, target, record.granted_permissions or "none")
        return {"ok": True, "plugin": manifest.id, "directory": str(target),
                "permissions_required": manifest.permissions,
                "permissions_granted": record.granted_permissions,
                "note": "permissions are denied until granted by the owner"}

    def uninstall(self, plugin_id: str, remove_files: bool = True) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"unknown plugin {plugin_id}"}
        if remove_files:
            try:
                shutil.rmtree(record.directory)
            except Exception as exc:
                log.warning("could not remove plugin files: %s", exc)
        self.db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
        self._records.pop(plugin_id, None)
        return {"ok": True, "plugin": plugin_id}

    def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"unknown plugin {plugin_id}"}
        record.enabled = enabled
        record.state = "installed" if enabled else "disabled"
        self._persist(record)
        return {"ok": True, "plugin": plugin_id, "enabled": enabled}

    def grant(self, plugin_id: str, permissions: List[str]) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"unknown plugin {plugin_id}"}
        unknown = [p for p in permissions if p not in record.manifest.permissions]
        if unknown:
            return {"ok": False, "error": f"plugin does not declare: {', '.join(unknown)}"}
        record.granted_permissions = sorted(set(record.granted_permissions) | set(permissions))
        self._persist(record)
        return {"ok": True, "plugin": plugin_id, "granted": record.granted_permissions}

    def revoke(self, plugin_id: str, permissions: Optional[List[str]] = None) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"unknown plugin {plugin_id}"}
        if permissions is None:
            record.granted_permissions = []
        else:
            record.granted_permissions = [p for p in record.granted_permissions
                                          if p not in permissions]
        self._persist(record)
        return {"ok": True, "plugin": plugin_id, "granted": record.granted_permissions}

    def mark_state(self, plugin_id: str, state: str, error: str = "") -> None:
        record = self.get(plugin_id)
        if record is None:
            return
        record.state = state
        record.last_error = error
        self._persist(record)

    # ------------------------------------------------------------------ helpers
    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        """capability -> {plugin_id, spec} for every enabled plugin."""
        out: Dict[str, Dict[str, Any]] = {}
        for record in self.all():
            if not record.enabled:
                continue
            for spec in record.manifest.capabilities:
                capability = f"plugin.{record.manifest.id}.{spec.name}"
                out[capability] = {"plugin_id": record.manifest.id, "spec": spec,
                                   "manifest": record.manifest, "record": record}
        return out
