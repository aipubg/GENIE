"""Read-before-write protection (core/staleguard.py) — re-audit 14.5.

Capability donor: **DeerFlow** (concurrent-edit protection).

A lock is not enough. Two agents can both hold the lock at different times and still clobber each
other:

    Agent A reads the file (version 1)
    Agent B acquires the lock, edits it (version 2)
    Agent A acquires the lock and writes its stale assumptions  <-- clobbered

The guard makes writes **conditional**: a writer must declare the version it based its edit on.
If the file moved on since, the write is **refused** and the caller must re-read and retry.

Version identity is a content hash, so this also catches the case where a file was rewritten with
identical mtime/size but different content.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional

from core.logging_setup import get_logger

log = get_logger("core.staleguard")


def version_of(path: Path | str) -> str:
    """Content-addressed version string. Empty string means 'does not exist'."""
    target = Path(path)
    if not target.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


class StaleWriteGuard:
    """Tracks what each writer last saw, and refuses writes based on stale reads."""

    def __init__(self):
        self._observed: Dict[str, str] = {}

    # ----------------------------------------------------------------- observe
    def observe(self, path: Path | str) -> str:
        version = version_of(path)
        self._observed[str(path)] = version
        return version

    def last_seen(self, path: Path | str) -> Optional[str]:
        return self._observed.get(str(path))

    def forget(self, path: Path | str) -> None:
        self._observed.pop(str(path), None)

    # ------------------------------------------------------------------- check
    def is_stale(self, path: Path | str, expected_version: str) -> bool:
        return version_of(path) != expected_version

    def check(self, path: Path | str, expected_version: str) -> Dict[str, object]:
        current = version_of(path)
        stale = current != expected_version
        return {"path": str(path), "expected_version": expected_version,
                "current_version": current, "stale": stale,
                "detail": ("file changed since it was read — re-read and retry"
                           if stale else "version matches")}

    # ------------------------------------------------------------------- write
    def write(self, path: Path | str, content: str, *,
              expected_version: str, encoding: str = "utf-8") -> Dict[str, object]:
        """Write only if the file is still at the version the edit was based on."""
        target = Path(path)
        current = version_of(target)
        if current != expected_version:
            return {"ok": False, "stale": True, "path": str(target),
                    "expected_version": expected_version, "current_version": current,
                    "detail": "stale write refused — re-read the file and retry"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        new_version = version_of(target)
        self._observed[str(target)] = new_version
        return {"ok": True, "stale": False, "path": str(target),
                "expected_version": expected_version, "current_version": new_version,
                "detail": "written"}

    # ------------------------------------------------------------------ helper
    def read_then_write(self, path: Path | str,
                        transform) -> Dict[str, object]:
        """Read -> transform -> conditional write, retrying once on a stale race."""
        target = Path(path)
        for attempt in (1, 2):
            version = self.observe(target)
            try:
                existing = target.read_text(encoding="utf-8") if target.is_file() else ""
                new_content = transform(existing)
            except Exception as exc:
                return {"ok": False, "stale": False, "path": str(target),
                        "error": f"transform failed: {exc}"}
            result = self.write(target, new_content, expected_version=version)
            if result["ok"] or attempt == 2:
                result["attempts"] = attempt
                return result
        return {"ok": False, "stale": True, "path": str(target), "attempts": 2}
