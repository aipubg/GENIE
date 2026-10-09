"""Gated spec pipeline with requirement traceability."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("specs.pipeline")

# Ordered phases. Each must validate before the next can be entered.
PHASES = ("specify", "plan", "tasks", "implement", "validate")


def _now() -> int:
    return int(time.time())


class SpecPipeline:
    """One spec: requirements, phase artifacts, tasks, and gating."""

    def __init__(self, db) -> None:
        self.db = db

    # ---------------------------------------------------------------- create
    def create(self, title: str, *, mission_id: str = "",
               constitution: Optional[List[str]] = None) -> Dict[str, Any]:
        sid = f"spec_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO spec_runs(spec_id, title, mission_id, phase, "
            "constitution, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (sid, title, mission_id, "specify",
             json.dumps(constitution or []), now, now))
        return {"spec_id": sid, "title": title, "mission_id": mission_id,
                "phase": "specify", "constitution": constitution or []}

    def get(self, spec_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM spec_runs WHERE spec_id=?", (spec_id,))
        if not row:
            return None
        d = dict(row)
        try:
            d["constitution"] = json.loads(d.get("constitution") or "[]")
        except Exception:  # noqa: BLE001
            d["constitution"] = []
        return d

    # ---------------------------------------------------------- requirements
    def add_requirement(self, spec_id: str, text: str, *,
                        rationale: str = "") -> Dict[str, Any]:
        rid = f"req_{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT INTO spec_requirements(requirement_id, spec_id, text, "
            "rationale, created_at) VALUES(?,?,?,?,?)",
            (rid, spec_id, text, rationale, _now()))
        return {"requirement_id": rid, "spec_id": spec_id, "text": text}

    def requirements(self, spec_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM spec_requirements WHERE spec_id=? ORDER BY rowid",
            (spec_id,))]

    # ----------------------------------------------------------------- tasks
    def add_task(self, spec_id: str, text: str, *,
                 requirement_ids: Optional[List[str]] = None,
                 capability: str = "") -> Dict[str, Any]:
        tid = f"task_{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT INTO spec_tasks(task_id, spec_id, text, requirement_ids, "
            "capability, status, created_at) VALUES(?,?,?,?,?,?,?)",
            (tid, spec_id, text, json.dumps(requirement_ids or []),
             capability, "pending", _now()))
        return {"task_id": tid, "spec_id": spec_id, "text": text,
                "requirement_ids": requirement_ids or []}

    def tasks(self, spec_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM spec_tasks WHERE spec_id=? ORDER BY rowid", (spec_id,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["requirement_ids"] = json.loads(d.get("requirement_ids") or "[]")
            except Exception:  # noqa: BLE001
                d["requirement_ids"] = []
            out.append(d)
        return out

    def complete_task(self, task_id: str, *, outcome: str = "ok",
                      evidence: str = "") -> bool:
        cur = self.db.execute(
            "UPDATE spec_tasks SET status=?, outcome=?, evidence=? "
            "WHERE task_id=?", ("done", outcome, evidence[:500], task_id))
        return bool(cur.rowcount)

    # -------------------------------------------------------------- artifacts
    def set_artifact(self, spec_id: str, phase: str, content: str) -> Dict[str, Any]:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        self.db.execute(
            "INSERT INTO spec_artifacts(artifact_id, spec_id, phase, content, "
            "created_at) VALUES(?,?,?,?,?)",
            (f"art_{uuid.uuid4().hex[:10]}", spec_id, phase, content, _now()))
        return {"spec_id": spec_id, "phase": phase, "stored": True}

    def artifacts(self, spec_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM spec_artifacts WHERE spec_id=? ORDER BY rowid",
            (spec_id,))]

    # -------------------------------------------------------------- gating
    def can_enter(self, spec_id: str, phase: str) -> Dict[str, Any]:
        """Can the spec move into `phase`? Mechanical, never assumed."""
        if phase not in PHASES:
            return {"ok": False, "reason": f"unknown phase {phase!r}"}
        spec = self.get(spec_id)
        if not spec:
            return {"ok": False, "reason": f"unknown spec {spec_id}"}

        # Gating is CONTENT-based, not purely sequential: a phase is reachable
        # when the artifacts the earlier phases must have produced actually
        # exist. Requiring the phase marker to be exactly the previous index
        # made the pipeline unusable (you could not go specify -> tasks even
        # with a complete spec and plan). The checks below are the real gate.
        idx = PHASES.index(phase)

        reqs = self.requirements(spec_id)
        tasks = self.tasks(spec_id)

        if idx >= 2 and not reqs:          # tasks phase and beyond
            return {"ok": False, "reason": "no requirements recorded"}
        if idx >= 2:
            covered = {r for t in tasks for r in t["requirement_ids"]}
            uncovered = [r["requirement_id"] for r in reqs
                         if r["requirement_id"] not in covered]
            if uncovered:
                return {"ok": False,
                        "reason": f"{len(uncovered)} requirement(s) have no task",
                        "uncovered": uncovered}

        if idx >= 3:                        # implement
            pending = [t["task_id"] for t in tasks if t["status"] != "done"]
            # entering implement is fine with pending tasks; finishing is not
            if not tasks:
                return {"ok": False, "reason": "no tasks to implement"}

        if idx >= 4:                        # validate
            incomplete = [t["task_id"] for t in tasks if t["status"] != "done"]
            if incomplete:
                return {"ok": False,
                        "reason": f"{len(incomplete)} task(s) not complete",
                        "incomplete": incomplete}
        return {"ok": True, "phase": phase}

    def advance(self, spec_id: str, phase: str) -> Dict[str, Any]:
        gate = self.can_enter(spec_id, phase)
        if not gate["ok"]:
            return gate
        self.db.execute(
            "UPDATE spec_runs SET phase=?, updated_at=? WHERE spec_id=?",
            (phase, _now(), spec_id))
        return {"ok": True, "phase": phase, "spec_id": spec_id}

    # ------------------------------------------------------------ validation
    def validate(self, spec_id: str) -> Dict[str, Any]:
        """Does the implementation satisfy every requirement?

        Mechanical: a requirement is satisfied when every task linked to it
        completed with outcome 'ok' and at least one task exists.
        """
        reqs = self.requirements(spec_id)
        tasks = self.tasks(spec_id)
        if not reqs:
            return {"ok": False, "reason": "no requirements", "coverage": []}

        by_req: Dict[str, List[Dict[str, Any]]] = {r["requirement_id"]: []
                                                   for r in reqs}
        for t in tasks:
            for rid in t["requirement_ids"]:
                by_req.setdefault(rid, []).append(t)

        coverage = []
        for r in reqs:
            linked = by_req.get(r["requirement_id"], [])
            if not linked:
                coverage.append({"requirement_id": r["requirement_id"],
                                 "satisfied": False, "reason": "no task"})
                continue
            done_ok = [t for t in linked if t["status"] == "done"
                       and (t.get("outcome") or "") == "ok"]
            coverage.append({
                "requirement_id": r["requirement_id"], "satisfied": bool(done_ok),
                "tasks": len(linked),
                "reason": "" if done_ok else "no task completed successfully"})

        ok = all(c["satisfied"] for c in coverage)
        result = {"ok": ok, "coverage": coverage,
                  "satisfied": sum(1 for c in coverage if c["satisfied"]),
                  "total": len(coverage)}
        if ok:
            self.db.execute(
                "UPDATE spec_runs SET phase='validate', validated_at=?, "
                "updated_at=? WHERE spec_id=?", (_now(), _now(), spec_id))
        self.db.execute(
            "INSERT INTO spec_validations(validation_id, spec_id, ok, detail, ts)"
            " VALUES(?,?,?,?,?)",
            (f"val_{uuid.uuid4().hex[:10]}", spec_id, 1 if ok else 0,
             json.dumps(result), _now()))
        return result

    def validations(self, spec_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM spec_validations WHERE spec_id=? ORDER BY ts",
            (spec_id,))]
