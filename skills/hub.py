"""Skill registry / hub (`skills/hub.py`).

The skill-install *scanner* (`security/skill_scanner.py`) decides whether a skill is safe. This module
is the *registry*: it records scanned skills, keeps their safety verdict, and answers enable/disable
and capability-lookup queries. It is the "Skill Hub" half of the deferred "MCP/Skill Hub" item
(the MCP half — supervision + runtime — is done in `integrations/`).

Storage is pluggable: by default an in-memory store; an app passes a persistent `SkillStorage`
(``save(records)`` / ``load() -> list[dict]``) to survive restarts. The registry never re-runs a
dangerous skill's code — only the scanner's static result is stored.

Policy:
  * a **BLOCK** verdict registers the skill *disabled* (cannot be enabled without an explicit force)
  * a **CONFIRM** verdict registers enabled but flagged `confirm_required`
  * an **OK** verdict registers enabled
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from security.skill_scanner import scan_skill
except Exception:  # pragma: no cover - only when run outside the app
    scan_skill = None  # type: ignore

try:
    from core.logging_setup import get_logger
    log = get_logger("skills.hub")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("skills.hub")

VERDICT_OK = "OK"
VERDICT_CONFIRM = "CONFIRM"
VERDICT_BLOCK = "BLOCK"
VERDICT_UNKNOWN = "UNKNOWN"


@dataclass
class SkillRecord:
    skill_id: str
    name: str
    path: str
    verdict: str = VERDICT_UNKNOWN
    enabled: bool = False
    confirm_required: bool = False
    forced: bool = False
    donor: str = ""
    capabilities: List[str] = field(default_factory=list)
    findings_count: int = 0
    scanner_summary: str = ""
    source: str = "local"
    registered_at: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id, "name": self.name, "path": self.path,
            "verdict": self.verdict, "enabled": self.enabled,
            "confirm_required": self.confirm_required, "forced": self.forced,
            "donor": self.donor, "capabilities": list(self.capabilities),
            "findings_count": self.findings_count, "scanner_summary": self.scanner_summary,
            "source": self.source, "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SkillRecord":
        return cls(
            skill_id=d["skill_id"], name=d["name"], path=d["path"],
            verdict=d.get("verdict", VERDICT_UNKNOWN),
            enabled=bool(d.get("enabled", False)),
            confirm_required=bool(d.get("confirm_required", False)),
            forced=bool(d.get("forced", False)), donor=d.get("donor", ""),
            capabilities=list(d.get("capabilities", [])),
            findings_count=int(d.get("findings_count", 0)),
            scanner_summary=d.get("scanner_summary", ""),
            source=d.get("source", "local"),
            registered_at=float(d.get("registered_at", time.time())),
        )


class SkillStorage:
    """Default in-memory store. Swap for a DB-backed implementation to persist."""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def save(self, records: List[Dict[str, Any]]) -> None:
        self._records = [dict(r) for r in records]

    def load(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._records]


class SkillRegistry:
    def __init__(self, storage: Optional[SkillStorage] = None, scanner=None):
        self._storage = storage or SkillStorage()
        self._scanner = scanner if scanner is not None else scan_skill
        self._records: Dict[str, SkillRecord] = {}
        for raw in self._storage.load():
            rec = SkillRecord.from_dict(raw)
            self._records[rec.skill_id] = rec

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _id_for(path: str, name: str) -> str:
        basis = f"{name}:{path}".encode("utf-8")
        return "skill-" + hashlib.sha1(basis).hexdigest()[:12]

    def _persist(self) -> None:
        self._storage.save([r.to_dict() for r in self._records.values()])

    # ----------------------------------------------------------- register
    def register(self, path: str, *, name: Optional[str] = None,
                 donor: str = "", capabilities: Optional[List[str]] = None,
                 scan: bool = True, force: bool = False) -> SkillRecord:
        import os
        abspath = os.path.abspath(path)
        skill_name = name or os.path.basename(abspath.rstrip("/\\")) or "unnamed"
        skill_id = self._id_for(abspath, skill_name)

        if not os.path.isdir(abspath):
            rec = SkillRecord(skill_id=skill_id, name=skill_name, path=abspath,
                              verdict=VERDICT_UNKNOWN, enabled=False,
                              scanner_summary="path does not exist")
            self._records[skill_id] = rec
            self._persist()
            return rec

        verdict = VERDICT_UNKNOWN
        findings_count = 0
        summary = ""
        if scan and self._scanner is not None:
            try:
                result = self._scanner(abspath)
                verdict = result.verdict()
                findings_count = len(result.findings)
                if result.findings:
                    summary = "; ".join(
                        f"{f.rule_id}({f.severity})" for f in result.findings[:5])
                else:
                    summary = "no dangerous patterns matched"
            except Exception as exc:  # scanner error -> unknown, never a fabricated clean bill
                log.warning("skill scan failed for %s: %s", abspath, exc)
                verdict = VERDICT_UNKNOWN
                summary = f"scan error: {exc}"

        enabled = (verdict != VERDICT_BLOCK) or force
        rec = SkillRecord(
            skill_id=skill_id, name=skill_name, path=abspath, verdict=verdict,
            enabled=enabled, confirm_required=(verdict == VERDICT_CONFIRM),
            forced=bool(force and verdict == VERDICT_BLOCK), donor=donor,
            capabilities=list(capabilities or []), findings_count=findings_count,
            scanner_summary=summary, source="local")
        self._records[skill_id] = rec
        self._persist()
        return rec

    def deregister(self, skill_id: str) -> bool:
        if skill_id in self._records:
            del self._records[skill_id]
            self._persist()
            return True
        return False

    # ----------------------------------------------------------- read
    def get(self, skill_id: str) -> Optional[SkillRecord]:
        return self._records.get(skill_id)

    def list_skills(self, *, enabled_only: bool = False) -> List[SkillRecord]:
        out = list(self._records.values())
        if enabled_only:
            out = [r for r in out if r.enabled]
        return out

    def blocked_skills(self) -> List[SkillRecord]:
        return [r for r in self._records.values() if r.verdict == VERDICT_BLOCK]

    def confirm_skills(self) -> List[SkillRecord]:
        return [r for r in self._records.values() if r.verdict == VERDICT_CONFIRM]

    def find_by_capability(self, capability: str) -> List[SkillRecord]:
        return [r for r in self._records.values()
                if capability in r.capabilities and r.enabled]

    # ----------------------------------------------------------- mutate
    def set_enabled(self, skill_id: str, enabled: bool, force: bool = False) -> bool:
        """Enable/disable a skill. Refuses to *enable* a BLOCK verdict unless forced."""
        rec = self._records.get(skill_id)
        if rec is None:
            return False
        if enabled and rec.verdict == VERDICT_BLOCK and not force:
            return False
        rec.enabled = enabled
        rec.forced = bool(enabled and rec.verdict == VERDICT_BLOCK and force)
        self._persist()
        return True
