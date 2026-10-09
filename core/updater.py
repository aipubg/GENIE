"""Updater & rollback (core/updater.py) — Phase 14.3.

A release is a *directory*, never an in-place overwrite. That single decision is what makes
rollback possible: the old version is still on disk, untouched, so reverting is just pointing
"active" back at it.

    releases/
      1.2.3/          staged files + manifest.json (sha256 per file)
      1.3.0/
    active.json       {"active_version": "1.3.0", "previous_version": "1.2.3"}

Rules:
* **stage** copies and hashes — an update is never trusted without a manifest.
* **activate** records the previous version *before* switching, so rollback always has a target.
* **rollback** activates the previous version and refuses when there is nothing to go back to.
* **verify** re-checks hashes; a corrupted release is reported, never silently activated.

This deliberately does not download anything: fetching is a separate concern. It stages, activates,
verifies and rolls back — the parts that must not be able to brick an install.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.updater")

MANIFEST_NAME = "manifest.json"
ACTIVE_NAME = "active.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Updater:
    """Stage, verify, activate and roll back GENIE releases."""

    def __init__(self, releases_dir: Path | str):
        self.releases_dir = Path(releases_dir)
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.active_file = self.releases_dir / ACTIVE_NAME

    # ----------------------------------------------------------------- state
    def _state(self) -> Dict[str, Any]:
        if not self.active_file.is_file():
            return {"active_version": None, "previous_version": None, "updated_ms": 0}
        try:
            return json.loads(self.active_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"active_version": None, "previous_version": None, "updated_ms": 0}

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.active_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def active(self) -> Optional[str]:
        return self._state().get("active_version")

    def previous(self) -> Optional[str]:
        return self._state().get("previous_version")

    # ----------------------------------------------------------------- stage
    def stage(self, version: str, source_dir: Path | str, *,
              note: str = "") -> Dict[str, Any]:
        """Copy a build into releases/<version>/ and record its hashes."""
        source = Path(source_dir)
        if not source.is_dir():
            return {"ok": False, "error": f"source directory not found: {source}"}
        target = self.releases_dir / version
        # A staged version must stay INSIDE the releases directory. Without this
        # check a version string such as "../../.." would resolve outside it and
        # stage() would rmtree an arbitrary directory - including an install root.
        try:
            resolved_root = self.releases_dir.resolve()
            resolved_target = target.resolve()
        except OSError:
            resolved_root, resolved_target = self.releases_dir, target
        if not (resolved_target == resolved_root
                or resolved_root in resolved_target.parents):
            return {"ok": False,
                    "error": f"refusing to stage outside the releases directory: {version}"}
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)

        files: List[Dict[str, Any]] = []
        for src in sorted(source.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(source)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files.append({"name": str(rel).replace("\\", "/"),
                          "sha256": _sha256(dst), "bytes": dst.stat().st_size})

        manifest = {"version": version, "created_ms": int(time.time() * 1000),
                    "note": note, "files": files}
        (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["ok"] = True
        manifest["path"] = str(target)
        return manifest

    # ---------------------------------------------------------------- verify
    def verify(self, version: str) -> Dict[str, Any]:
        target = self.releases_dir / version
        manifest_path = target / MANIFEST_NAME
        if not manifest_path.is_file():
            return {"ok": False, "error": f"no manifest for version {version}"}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"unreadable manifest: {exc}"}

        problems: List[str] = []
        for entry in manifest.get("files", []):
            path = target / entry["name"]
            if not path.is_file():
                problems.append(f"missing file: {entry['name']}")
            elif _sha256(path) != entry["sha256"]:
                problems.append(f"checksum mismatch: {entry['name']}")
        return {"ok": not problems, "problems": problems, "version": version,
                "files": len(manifest.get("files", []))}

    # --------------------------------------------------------------- activate
    def activate(self, version: str, *, verify: bool = True) -> Dict[str, Any]:
        """Switch to a release, remembering the one we came from."""
        if not (self.releases_dir / version).is_dir():
            return {"ok": False, "error": f"unknown version {version}"}
        if verify:
            check = self.verify(version)
            if not check["ok"]:
                return {"ok": False, "error": "release failed verification — refusing to "
                                              "activate", "problems": check["problems"]}
        state = self._state()
        previous = state.get("active_version")
        state["previous_version"] = previous
        state["active_version"] = version
        state["updated_ms"] = int(time.time() * 1000)
        self._write_state(state)
        return {"ok": True, "active_version": version, "previous_version": previous}

    # --------------------------------------------------------------- rollback
    def rollback(self) -> Dict[str, Any]:
        """Go back to the last known-good version."""
        state = self._state()
        target = state.get("previous_version")
        if not target:
            return {"ok": False, "error": "nothing to roll back to",
                    "active_version": state.get("active_version")}
        if not (self.releases_dir / target).is_dir():
            return {"ok": False, "error": f"previous version {target} is missing from disk"}
        check = self.verify(target)
        if not check["ok"]:
            return {"ok": False, "error": f"previous version {target} is corrupt — cannot roll "
                                          "back safely", "problems": check["problems"]}
        rolled_from = state.get("active_version")
        state["previous_version"] = rolled_from
        state["active_version"] = target
        state["updated_ms"] = int(time.time() * 1000)
        self._write_state(state)
        return {"ok": True, "active_version": target, "rolled_back_from": rolled_from}

    # ------------------------------------------------------------------ query
    def releases(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for entry in sorted(self.releases_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / MANIFEST_NAME
            info: Dict[str, Any] = {"version": entry.name, "path": str(entry)}
            if manifest_path.is_file():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    info["files"] = len(data.get("files", []))
                    info["created_ms"] = data.get("created_ms")
                    info["note"] = data.get("note", "")
                except (OSError, ValueError):
                    info["files"] = 0
            out.append(info)
        return out

    def status(self) -> Dict[str, Any]:
        state = self._state()
        return {"active_version": state.get("active_version"),
                "previous_version": state.get("previous_version"),
                "updated_ms": state.get("updated_ms", 0),
                "releases": [r["version"] for r in self.releases()]}
