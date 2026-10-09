"""Skill Registry (skills/registry.py) — the authoritative source of skill truth.

Supports: register · get · search · list · activate · disable · deprecate · update/version ·
delete/archive · stats · compatibility. Versioning is explicit — a working skill is **never**
silently overwritten; a new candidate becomes `id@N+1` and the previous version stays until the
new one is activated (with rollback support).

NEDLE2 may route to a skill, but it never owns skill truth: this service does.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from skills.models import (
    Skill, SkillScope, SkillStatus, SkillStep, validate_skill,
)

log = get_logger("skills.registry")

MIN_SELECTION_SCORE = 0.45          # never run a skill just because a search returned one

# Statuses that mean "this version must not be the active one" (A-052).
WITHDRAWN_STATUSES = {SkillStatus.ARCHIVED.value, SkillStatus.REJECTED.value,
                      SkillStatus.DISABLED.value, SkillStatus.DEPRECATED.value}


@dataclass
class SkillMatch:
    skill: Skill
    score: float
    reasons: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {"skill_id": self.skill.skill_id, "version": self.skill.version,
                "name": self.skill.name, "status": self.skill.status,
                "score": round(self.score, 3), "reasons": self.reasons,
                "success_rate": self.skill.success_rate, "runs": self.skill.runs}


class SkillRegistry:
    def __init__(self, db, audit=None):
        self.db = db
        self.audit = audit

    # ------------------------------------------------------------------ write
    def register(self, skill: Skill, *, activate: bool = False) -> Dict[str, Any]:
        problems = validate_skill(skill)
        if problems:
            return {"ok": False, "error": "invalid skill", "problems": problems}
        existing = self.get(skill.skill_id, skill.version)
        if existing is not None:
            return {"ok": False, "error": f"{skill.key} already exists (use update/version)"}
        if activate and skill.status != SkillStatus.ACTIVE.value:
            skill.status = SkillStatus.ACTIVE.value
            skill.last_verified = int(time.time())
        self._insert(skill)
        if activate:
            self.set_active_version(skill.skill_id, skill.version)
        self._audit(f"skill.register:{skill.key}", f"status={skill.status}")
        return {"ok": True, "key": skill.key, "status": skill.status}

    def _insert(self, skill: Skill) -> None:
        self.db.execute(
            "INSERT INTO skills(skill_id, version, name, status, scope, scope_ref, tags,"
            " document, success_count, failure_count, created_at, updated_at, active)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (skill.skill_id, skill.version, skill.name, skill.status, skill.scope,
             skill.scope_ref, json.dumps(skill.tags), json.dumps(skill.to_dict()),
             skill.success_count, skill.failure_count, skill.created_at, int(time.time())))

    def save(self, skill: Skill) -> None:
        """Persist an updated skill document (statistics, status, content)."""
        self.db.execute(
            "UPDATE skills SET name=?, status=?, scope=?, scope_ref=?, tags=?, document=?,"
            " success_count=?, failure_count=?, updated_at=? WHERE skill_id=? AND version=?",
            (skill.name, skill.status, skill.scope, skill.scope_ref, json.dumps(skill.tags),
             json.dumps(skill.to_dict()), skill.success_count, skill.failure_count,
             int(time.time()), skill.skill_id, skill.version))

    def update(self, skill_id: str, changes: Dict[str, Any], *,
               as_new_version: bool = True, activate: bool = False) -> Dict[str, Any]:
        """Update a skill. By default this creates a NEW version (never overwrite silently)."""
        current = self.get(skill_id)
        if current is None:
            return {"ok": False, "error": f"unknown skill {skill_id}"}
        if not as_new_version:
            data = current.to_dict()
            data.update(changes)
            updated = Skill.from_dict(data)
            problems = validate_skill(updated)
            if problems:
                return {"ok": False, "error": "invalid skill", "problems": problems}
            self.save(updated)
            self._audit(f"skill.update:{updated.key}", "in-place update")
            return {"ok": True, "key": updated.key, "new_version": False}

        candidate = current.clone_as_new_version(self.next_version(skill_id))
        data = candidate.to_dict()
        data.update({k: v for k, v in changes.items() if k != "version"})
        candidate = Skill.from_dict(data)
        candidate.provenance = {**candidate.provenance, "derived_from": current.key}
        problems = validate_skill(candidate)
        if problems:
            return {"ok": False, "error": "invalid skill", "problems": problems}
        self._insert(candidate)
        self._audit(f"skill.update:{candidate.key}", f"derived from {current.key}")
        return {"ok": True, "key": candidate.key, "new_version": True,
                "previous_active": self.active_version(skill_id)}

    def set_active_version(self, skill_id: str, version: int) -> Dict[str, Any]:
        skill = self.get(skill_id, version)
        if skill is None:
            return {"ok": False, "error": f"unknown {skill_id}@{version}"}
        self.db.execute("UPDATE skills SET active=0 WHERE skill_id=?", (skill_id,))
        self.db.execute("UPDATE skills SET active=1 WHERE skill_id=? AND version=?",
                        (skill_id, version))
        if skill.status not in (SkillStatus.ACTIVE.value, SkillStatus.DEPRECATED.value):
            skill.status = SkillStatus.ACTIVE.value
            self.save(skill)
        self._audit(f"skill.activate:{skill.key}", "active version set")
        return {"ok": True, "skill_id": skill_id, "version": version}

    def rollback(self, skill_id: str, to_version: Optional[int] = None) -> Dict[str, Any]:
        """Roll back to the previous (or a specified) version."""
        versions = sorted(self.versions(skill_id), key=lambda s: s.version)
        if not versions:
            return {"ok": False, "error": f"unknown skill {skill_id}"}
        current = self.active_version(skill_id)
        if to_version is None:
            candidates = [v for v in versions if v.version < (current or 10 ** 9)]
            if not candidates:
                return {"ok": False, "error": "no previous version to roll back to"}
            target = candidates[-1]
        else:
            target = next((v for v in versions if v.version == to_version), None)
            if target is None:
                return {"ok": False, "error": f"unknown version {to_version}"}
        result = self.set_active_version(skill_id, target.version)
        if result.get("ok"):
            self._audit(f"skill.rollback:{skill_id}", f"{current} -> {target.version}")
            result["rolled_back_to"] = target.version
        return result

    def set_status(self, skill_id: str, status: str, version: Optional[int] = None,
                   reason: str = "") -> Dict[str, Any]:
        skill = self.get(skill_id, version)
        if skill is None:
            return {"ok": False, "error": f"unknown skill {skill_id}"}
        if not skill.can_transition(status) and status != skill.status:
            return {"ok": False,
                    "error": f"illegal transition {skill.status} -> {status}"}
        skill.status = status
        if status == SkillStatus.DEPRECATED.value:
            skill.provenance = {**skill.provenance, "deprecated_reason": reason,
                                "deprecated_at": int(time.time())}
        self.save(skill)
        # A-052: a withdrawn version must never keep the active flag. Otherwise
        # active_version() would hand back an archived/rejected skill and rollback and
        # promotion would silently target dead versions.
        promoted = None
        if status in WITHDRAWN_STATUSES and self.active_version(skill_id) == skill.version:
            self.db.execute("UPDATE skills SET active=0 WHERE skill_id=?", (skill_id,))
            promoted = self._promote_best(skill_id)
        self._audit(f"skill.status:{skill.key}", f"{status} {reason}".strip())
        return {"ok": True, "key": skill.key, "status": status,
                "active_version": self.active_version(skill_id), "promoted": promoted}

    def _promote_best(self, skill_id: str) -> Optional[int]:
        """Promote the newest still-usable version after the active one was withdrawn.

        Only an already-ACTIVE version may be promoted — a CANDIDATE is never activated
        implicitly, because that would bypass the sandbox validator (§5.9).
        """
        row = self.db.query_one(
            "SELECT version FROM skills WHERE skill_id=? AND status=?"
            " ORDER BY version DESC LIMIT 1",
            (skill_id, SkillStatus.ACTIVE.value))
        if row is None:
            return None
        version = int(row["version"])
        self.db.execute("UPDATE skills SET active=1 WHERE skill_id=? AND version=?",
                        (skill_id, version))
        return version

    def delete(self, skill_id: str, version: Optional[int] = None,
               archive_only: bool = True) -> Dict[str, Any]:
        if archive_only:
            return self.set_status(skill_id, SkillStatus.ARCHIVED.value, version,
                                   "archived by owner")
        if version is None:
            self.db.execute("DELETE FROM skills WHERE skill_id=?", (skill_id,))
        else:
            self.db.execute("DELETE FROM skills WHERE skill_id=? AND version=?",
                            (skill_id, version))
        self._audit(f"skill.delete:{skill_id}", f"version={version or 'all'}")
        return {"ok": True, "skill_id": skill_id, "deleted": True}

    def record_run(self, skill: Skill, *, success: bool, verified: bool,
                   latency_ms: int = 0, cost_usd: float = 0.0,
                   failure: Optional[Dict[str, Any]] = None) -> Skill:
        """Statistics are updated only from a verified outcome."""
        skill.last_used = int(time.time())
        skill.total_latency_ms += max(0, latency_ms)
        skill.total_cost_usd += max(0.0, cost_usd)
        if success and verified:
            skill.success_count += 1
            skill.last_verified = int(time.time())
        else:
            skill.failure_count += 1
            if not verified:
                skill.verification_failures += 1
            if failure:
                skill.known_failures.append({**failure, "ts": int(time.time())})
                skill.known_failures = skill.known_failures[-20:]
        self.save(skill)
        return skill

    def record_takeover(self, skill: Skill, detail: str = "") -> Skill:
        skill.user_takeovers += 1
        skill.known_failures.append({"kind": "user_takeover", "detail": detail[:200],
                                     "ts": int(time.time())})
        skill.known_failures = skill.known_failures[-20:]
        self.save(skill)
        return skill

    # ------------------------------------------------------------------- read
    def get(self, skill_id: str, version: Optional[int] = None) -> Optional[Skill]:
        if version is None:
            row = self.db.query_one(
                "SELECT document FROM skills WHERE skill_id=? AND active=1", (skill_id,))
            if row is None:
                row = self.db.query_one(
                    "SELECT document FROM skills WHERE skill_id=?"
                    " ORDER BY version DESC LIMIT 1", (skill_id,))
        else:
            row = self.db.query_one(
                "SELECT document FROM skills WHERE skill_id=? AND version=?",
                (skill_id, version))
        return Skill.from_dict(json.loads(row["document"])) if row else None

    def versions(self, skill_id: str) -> List[Skill]:
        rows = self.db.query(
            "SELECT document FROM skills WHERE skill_id=? ORDER BY version", (skill_id,))
        return [Skill.from_dict(json.loads(r["document"])) for r in rows]

    def active_version(self, skill_id: str) -> Optional[int]:
        row = self.db.query_one(
            "SELECT version FROM skills WHERE skill_id=? AND active=1", (skill_id,))
        return int(row["version"]) if row else None

    def next_version(self, skill_id: str) -> int:
        row = self.db.query_one(
            "SELECT MAX(version) AS v FROM skills WHERE skill_id=?", (skill_id,))
        return int((row["v"] or 0)) + 1 if row else 1

    def list(self, *, status: Optional[str] = None, scope: Optional[str] = None,
             active_only: bool = True, limit: int = 100) -> List[Skill]:
        sql = "SELECT document FROM skills WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [Skill.from_dict(json.loads(r["document"])) for r in self.db.query(sql, params)]

    # ----------------------------------------------------------------- search
    def search(self, query: str, *, context: Optional[Dict[str, Any]] = None,
               available_capabilities: Optional[List[str]] = None,
               available_plugins: Optional[List[str]] = None,
               limit: int = 5, include_candidates: bool = False) -> List[SkillMatch]:
        """Rank skills. Never returns a low-quality skill merely because the name looks similar."""
        context = context or {}
        # A-054: capabilities/plugins may arrive either as explicit kwargs or inside the
        # context dict. Previously only the kwargs were read, so a caller that passed
        # context={"capabilities": [...]} silently disabled compatibility filtering and an
        # incompatible skill could be selected.
        capabilities = set(available_capabilities or context.get("capabilities") or [])
        plugins = set(available_plugins or context.get("plugins") or [])
        tokens = {t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2}
        intent_tokens = tokens | {t for t in (context.get("tags") or [])}

        statuses = {SkillStatus.ACTIVE.value}
        if include_candidates:
            statuses.add(SkillStatus.CANDIDATE.value)
        candidates = [s for s in self.list(active_only=True, limit=500) if s.status in statuses]

        matches: List[SkillMatch] = []
        for skill in candidates:
            reasons: Dict[str, float] = {}

            # intent / tag / name match
            haystack = " ".join([skill.skill_id, skill.name, skill.description, skill.purpose,
                                 " ".join(skill.tags)]).lower()
            overlap = len([t for t in intent_tokens if t in haystack])
            reasons["intent"] = min(1.0, overlap / max(1, len(intent_tokens))) * 0.35

            # compatibility: required capabilities and plugins must be available
            missing_caps = [c for c in skill.required_capabilities if capabilities
                            and c not in capabilities]
            missing_plugins = [p for p in skill.required_plugins if plugins and p not in plugins]
            reasons["compatibility"] = 0.30 if not (missing_caps or missing_plugins) else -0.5

            # proven quality
            if skill.runs:
                reasons["success_rate"] = skill.success_rate * 0.20
            else:
                reasons["success_rate"] = 0.05          # unproven, but not disqualified

            # recency
            age_days = (time.time() - (skill.last_used or skill.created_at)) / 86400
            reasons["recency"] = max(0.0, 1.0 - min(age_days, 30) / 30) * 0.10

            # specificity: more declared inputs/verification = more precise
            specificity = min(1.0, (len(skill.inputs) + len(skill.verification)) / 6)
            reasons["specificity"] = specificity * 0.05

            # scope affinity (project/app skills beat global ones when the context matches)
            if skill.scope == SkillScope.PROJECT.value and context.get("project"):
                reasons["scope"] = 0.10 if skill.scope_ref == context["project"] else -0.2
            elif skill.scope == SkillScope.APPLICATION.value and context.get("application"):
                reasons["scope"] = 0.10 if skill.scope_ref == context["application"] else -0.2
            else:
                reasons["scope"] = 0.0

            score = sum(reasons.values())
            if missing_caps or missing_plugins:
                score = min(score, 0.2)                 # never select an incompatible skill
            # A-051: intent is a GATE, not just a bonus. Baseline points (compatibility +
            # recency + unproven) alone must never clear the selection threshold, otherwise an
            # unrelated skill could be selected just for being recently created.
            if overlap == 0:
                score = min(score, 0.2)
            matches.append(SkillMatch(skill=skill, score=score, reasons=reasons))

        matches.sort(key=lambda m: -m.score)
        return [m for m in matches[:limit]]

    def select(self, query: str, **kwargs) -> Optional[SkillMatch]:
        """Return the best skill only if it clears the selection threshold (§5.29)."""
        threshold = float(kwargs.pop("min_score", MIN_SELECTION_SCORE))
        matches = self.search(query, limit=3, **kwargs)
        if not matches:
            return None
        best = matches[0]
        if best.score < threshold:
            log.info("skill '%s' scored %.2f (< %.2f) — planning normally instead",
                     best.skill.skill_id, best.score, threshold)
            return None
        return best

    # ------------------------------------------------------ duplicates / similarity
    def similarity(self, a: Skill, b: Skill) -> float:
        """Cheap structural similarity used for duplicate detection."""
        if a.skill_id == b.skill_id:
            return 1.0
        caps_a = [s.capability for s in a.steps]
        caps_b = [s.capability for s in b.steps]
        if not caps_a or not caps_b:
            return 0.0
        common = len(set(caps_a) & set(caps_b))
        sequence = common / max(len(set(caps_a)), len(set(caps_b)))
        words_a = set(re.split(r"[^a-z0-9]+", f"{a.skill_id} {a.name}".lower()))
        words_b = set(re.split(r"[^a-z0-9]+", f"{b.skill_id} {b.name}".lower()))
        name_overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
        verification_match = 1.0 if a.verification == b.verification else 0.0
        return round(0.6 * sequence + 0.25 * name_overlap + 0.15 * verification_match, 3)

    def find_duplicates(self, skill: Skill, threshold: float = 0.75) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for other in self.list(active_only=False, limit=500):
            if other.skill_id == skill.skill_id and other.version == skill.version:
                continue
            score = self.similarity(skill, other)
            if score >= threshold:
                out.append({"skill_id": other.skill_id, "version": other.version,
                            "status": other.status, "similarity": score})
        return sorted(out, key=lambda d: -d["similarity"])

    # ------------------------------------------------------------ compatibility
    def compatibility(self, skill: Skill, *, capabilities: List[str],
                      plugins: List[str], devices: Optional[List[str]] = None) -> Dict[str, Any]:
        devices = devices or []
        missing_caps = [c for c in skill.required_capabilities if c not in capabilities]
        missing_plugins = [p for p in skill.required_plugins if p not in plugins]
        missing_devices = [d for d in skill.required_devices if d not in devices]
        return {"compatible": not (missing_caps or missing_plugins or missing_devices),
                "missing_capabilities": missing_caps,
                "missing_plugins": missing_plugins,
                "missing_devices": missing_devices}

    # ------------------------------------------------------------------ stats
    def stats(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM skills GROUP BY status")
        by_status = {r["status"]: r["n"] for r in rows}
        totals = self.db.query_one(
            "SELECT COUNT(*) AS skills, SUM(success_count) AS ok,"
            " SUM(failure_count) AS bad FROM skills")
        return {"by_status": by_status,
                "skills": int(totals["skills"] or 0),
                "successes": int(totals["ok"] or 0),
                "failures": int(totals["bad"] or 0)}

    def _audit(self, action: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who="system", action=action, why=detail[:200], result="ok")
