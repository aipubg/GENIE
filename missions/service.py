"""Mission Service — truth owner for mission state (contract C4, master spec §2.4/§12).

Only this service may write mission state. NEDLE2 and agents propose; the service decides.
Missions are resumable: every state change is persisted, and a snapshot can rebuild
the whole execution context after a crash or provider failover.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional

from core.contracts import (
    ALLOWED_TRANSITIONS, CallContext, EventType, Mission, MissionState, MissionStep,
    TaskType, now_ms,
)
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("missions.service")


class MissionError(Exception):
    pass


class MissionService:
    def __init__(self, db, audit=None):
        self.db = db
        self.audit = audit
        self._bus = get_bus()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ create
    def create(self, ctx: CallContext, goal: str, *,
               targets: List[str] | None = None,
               criteria: List[str] | None = None,
               steps: List[MissionStep] | None = None,
               schedule: str = "", continuous: bool = False) -> Mission:
        m = Mission(goal=goal, owner=ctx.person_id,
                    targets=targets or ["pc_main"],
                    criteria=criteria or [],
                    steps=steps or [], trace_id=ctx.trace_id,
                    schedule=(schedule or "")[:64], continuous=bool(continuous))
        self.db.execute(
            "INSERT INTO missions(mission_id,goal,owner,state,targets,criteria,trace_id,"
            "created_at,updated_at,cost_usd,errors,schedule,continuous,next_run_ms,"
            "last_run_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m.mission_id, m.goal, m.owner, m.state.value, json.dumps(m.targets),
             json.dumps(m.criteria), m.trace_id, m.created_at, m.updated_at, 0.0, "[]",
             m.schedule, 1 if m.continuous else 0, 0, 0))
        for i, s in enumerate(m.steps):
            self._persist_step(m.mission_id, s, i)
        self._audit(ctx, m.mission_id, "mission.create", goal)
        self._bus.publish(EventType.MISSION_CREATED,
                          {"mission_id": m.mission_id, "goal": goal,
                           "targets": m.targets}, trace_id=ctx.trace_id)
        return m

    def plan(self, ctx: CallContext, mission_id: str, steps: List[MissionStep]) -> Mission:
        m = self.get(mission_id)
        if not m:
            raise KeyError(mission_id)
        for i, s in enumerate(steps):
            self._persist_step(mission_id, s, i)
        m.steps = steps
        if m.state is MissionState.CREATED:
            self.transition(ctx, mission_id, MissionState.PLANNED)
        return self.get(mission_id) or m

    # ------------------------------------------------------------------- state
    def transition(self, ctx: CallContext, mission_id: str, new_state: MissionState,
                   reason: str = "") -> Mission:
        with self._lock:
            m = self.get(mission_id)
            if not m:
                raise KeyError(mission_id)
            if new_state not in ALLOWED_TRANSITIONS[m.state] and new_state != m.state:
                raise MissionError(
                    f"illegal transition {m.state.value} -> {new_state.value}")
            m.state = new_state
            m.updated_at = now_ms()
            self.db.execute("UPDATE missions SET state=?, updated_at=? WHERE mission_id=?",
                            (new_state.value, m.updated_at, mission_id))
            self._audit(ctx, mission_id, f"mission.state:{new_state.value}", reason)
            if new_state is MissionState.COMPLETED:
                self._bus.publish(EventType.MISSION_COMPLETED,
                                  {"mission_id": mission_id, "goal": m.goal}, trace_id=ctx.trace_id)
            if new_state is MissionState.FAILED:
                self._bus.publish(EventType.MISSION_FAILED,
                                  {"mission_id": mission_id, "reason": reason}, trace_id=ctx.trace_id)
            return m

    def cancel(self, ctx: CallContext, mission_id: str, reason: str = "user requested") -> Mission:
        m = self.get(mission_id)
        if not m:
            raise KeyError(mission_id)
        if m.state in (MissionState.COMPLETED, MissionState.CANCELLED):
            return m
        # release locks held by the mission (C: cancellation is first-class)
        self.db.execute("DELETE FROM locks WHERE holder=?", (mission_id,))
        return self.transition(ctx, mission_id, MissionState.CANCELLED, reason)

    def record_error(self, mission_id: str, error: str) -> None:
        row = self.db.query_one("SELECT errors FROM missions WHERE mission_id=?", (mission_id,))
        errors = json.loads(row["errors"] or "[]") if row else []
        errors.append({"ts": now_ms(), "error": error})
        self.db.execute("UPDATE missions SET errors=? WHERE mission_id=?",
                        (json.dumps(errors[-50:]), mission_id))

    def delete(self, ctx: CallContext, mission_id: str) -> bool:
        """Delete a settled mission; never remove an executing step underneath a worker."""
        with self._lock:
            def remove(conn):
                row = conn.execute("SELECT state FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
                if row is None:
                    return False
                if row["state"] not in ("COMPLETED", "CANCELLED", "FAILED"):
                    raise MissionError("Cancel this mission before deleting it.")
                active = conn.execute("SELECT 1 FROM mission_steps WHERE mission_id=? AND status='running'",
                                      (mission_id,)).fetchone()
                if active:
                    raise MissionError("A step is still stopping. Try deleting again after it finishes.")
                conn.execute("DELETE FROM mission_schedule WHERE mission_id=?", (mission_id,))
                conn.execute("DELETE FROM mission_steps WHERE mission_id=?", (mission_id,))
                conn.execute("DELETE FROM locks WHERE holder=?", (mission_id,))
                conn.execute("DELETE FROM missions WHERE mission_id=?", (mission_id,))
                return True
            deleted = self.db.in_transaction(remove)
            if deleted:
                self._audit(ctx, mission_id, "mission.delete", "owner requested")
            return deleted

    def add_cost(self, mission_id: str, usd: float, provider: str = "") -> None:
        self.db.execute("UPDATE missions SET cost_usd = cost_usd + ? WHERE mission_id=?",
                        (usd, mission_id))
        self.db.execute(
            "INSERT INTO provider_state(provider_id, model_id, total_calls, total_cost)"
            " VALUES(?,?,0,?) ON CONFLICT(provider_id, model_id) DO UPDATE SET"
            " total_cost = total_cost + excluded.total_cost",
            (provider or "unknown", "_mission", usd))

    # -------------------------------------------------------------------- read
    def get(self, mission_id: str) -> Optional[Mission]:
        row = self.db.query_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        return self._row_to_mission(row) if row else None

    def list(self, limit: int = 25, state: str | None = None) -> List[Mission]:
        if state:
            rows = self.db.query("SELECT * FROM missions WHERE state=? ORDER BY created_at DESC LIMIT ?",
                                 (state, limit))
        else:
            rows = self.db.query("SELECT * FROM missions ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_mission(r) for r in rows]

    def update_step(self, mission_id: str, step_id: str, status: str,
                    result: Dict[str, Any] | None = None) -> None:
        self.db.execute("UPDATE mission_steps SET status=?, result=? WHERE step_id=? AND mission_id=?",
                        (status, json.dumps(result or {}), step_id, mission_id))

    def steps(self, mission_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM mission_steps WHERE mission_id=? ORDER BY seq", (mission_id,))]

    # ------------------------------------------------- durable execution (Pass 3)
    def replace_steps(self, mission_id: str, steps: List[MissionStep]) -> None:
        """Persist a whole plan, replacing any previous steps for this mission."""
        self.db.execute("DELETE FROM mission_steps WHERE mission_id=?", (mission_id,))
        for i, s in enumerate(steps):
            self._persist_step(mission_id, s, i)

    def add_step(self, mission_id: str, step: MissionStep) -> MissionStep:
        seq = len(self.steps(mission_id))
        self._persist_step(mission_id, step, seq)
        return step

    def set_step_status(self, mission_id: str, step_id: str, status: str, *,
                        result: Dict[str, Any] | None = None,
                        evidence: Dict[str, Any] | None = None,
                        provider: str = "", attempt: int | None = None) -> None:
        """Persist a step transition. This IS the checkpoint: after this call the
        step's state survives exit/relaunch."""
        sets = ["status=?"]
        params: List[Any] = [status]
        if result is not None:
            sets.append("result=?")
            params.append(json.dumps(result))
        if evidence is not None:
            sets.append("evidence=?")
            params.append(json.dumps(evidence))
        if provider:
            sets.append("provider_used=?")
            params.append(provider)
        if attempt is not None:
            sets.append("attempt=?")
            params.append(int(attempt))
        if status == "running":
            sets.append("started_ms=?")
            params.append(now_ms())
        if status in ("completed", "failed", "cancelled", "needs_revision"):
            sets.append("finished_ms=?")
            params.append(now_ms())
        params.extend([step_id, mission_id])
        self.db.execute(
            f"UPDATE mission_steps SET {', '.join(sets)} WHERE step_id=? AND mission_id=?",
            tuple(params))

    def ready_steps(self, mission_id: str) -> List[Dict[str, Any]]:
        """Steps whose dependencies are all completed and that have not started."""
        rows = self.steps(mission_id)
        done = {r["step_id"] for r in rows if r["status"] == "completed"}
        out = []
        for r in rows:
            if r["status"] not in ("pending", "ready"):
                continue
            deps = json.loads(r.get("depends_on") or "[]")
            if all(d in done for d in deps):
                out.append(r)
        return out

    def progress(self, mission_id: str) -> Dict[str, Any]:
        rows = self.steps(mission_id)
        done = [r for r in rows if r["status"] == "completed"]
        current = next((r for r in rows if r["status"] in ("running", "ready")), None)
        return {"done": len(done), "total": len(rows),
                "current": (current or {}).get("objective", "") if current else "",
                "current_step_id": (current or {}).get("step_id", "") if current else ""}

    def set_fields(self, mission_id: str, **fields: Any) -> None:
        """Update whitelisted mission columns (used for schedule/wake/continuous)."""
        allowed = {"state", "continuous", "next_run_ms", "last_run_ms", "schedule",
                   "updated_at", "criteria"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            params.append(1 if (k == "continuous" and isinstance(v, bool) and v) else
                          0 if k == "continuous" else v)
        if not sets:
            return
        if "updated_at" not in fields:
            sets.append("updated_at=?")
            params.append(now_ms())
        params.append(mission_id)
        self.db.execute(f"UPDATE missions SET {', '.join(sets)} WHERE mission_id=?",
                        tuple(params))

    # --------------------------------------------------------- scheduling
    def set_schedule(self, mission_id: str, spec: Dict[str, Any], next_run_ms: int,
                     catch_up: str = "once") -> None:
        self.db.execute(
            "INSERT INTO mission_schedule(mission_id, spec, next_run_ms, last_run_ms,"
            " enabled, catch_up, missed_count, created_at, updated_at)"
            " VALUES(?,?,?,?,1,?,0,?,?) ON CONFLICT(mission_id) DO UPDATE SET"
            " spec=excluded.spec, next_run_ms=excluded.next_run_ms, enabled=1,"
            " catch_up=excluded.catch_up, updated_at=excluded.updated_at",
            (mission_id, json.dumps(spec), int(next_run_ms), 0, catch_up,
             now_ms(), now_ms()))

    def schedule_of(self, mission_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM mission_schedule WHERE mission_id=?",
                                (mission_id,))
        if not row:
            return None
        result = dict(row)
        try:
            result["spec"] = json.loads(result.get("spec") or "{}")
        except (TypeError, ValueError):
            result["spec"] = {}
        return result

    def clear_schedule(self, mission_id: str) -> None:
        self.db.execute("DELETE FROM mission_schedule WHERE mission_id=?", (mission_id,))

    def due_missions(self, now: int | None = None) -> List[Dict[str, Any]]:
        now = now if now is not None else now_ms()
        rows = self.db.query(
            "SELECT s.*, m.state, m.goal, m.continuous FROM mission_schedule s"
            " JOIN missions m ON m.mission_id = s.mission_id"
            " WHERE s.enabled=1 AND s.next_run_ms>0 AND s.next_run_ms<=?"
            " AND m.state NOT IN ('CANCELLED','COMPLETED','FAILED')", (now,))
        return [dict(r) for r in rows]

    def mark_run(self, mission_id: str, next_run_ms: int, missed: int = 0) -> None:
        self.db.execute(
            "UPDATE mission_schedule SET last_run_ms=?, next_run_ms=?,"
            " missed_count=missed_count+?, updated_at=? WHERE mission_id=?",
            (now_ms(), int(next_run_ms), int(missed), now_ms(), mission_id))
        self.set_fields(mission_id, last_run_ms=now_ms(), next_run_ms=int(next_run_ms))

    # ------------------------------------------------------------- lookup
    def find_by_text(self, query: str) -> Optional[Mission]:
        """Resolve a mission referenced by the owner's words (chat/voice control).

        Matches on goal text, preferring non-terminal missions. Returns None when
        nothing matches — the caller then asks which mission is meant rather than
        guessing.
        """
        q = (query or "").strip().lower()
        if not q:
            return None
        words = [w for w in re.split(r"\W+", q) if len(w) > 2]
        best: Optional[Mission] = None
        best_score = 0
        for m in self.list(200):
            goal = (m.goal or "").lower()
            score = sum(1 for w in words if w in goal)
            if score > best_score:
                best_score, best = score, m
        return best if best_score > 0 else None

    # --------------------------------------------------------------- resumable
    def snapshot(self, mission_id: str) -> Dict[str, Any]:
        """Provider-agnostic snapshot (used for failover and crash recovery)."""
        m = self.get(mission_id)
        if not m:
            raise KeyError(mission_id)
        return {
            "mission_id": m.mission_id,
            "goal": m.goal,
            "state": m.state.value,
            "steps": [s.__dict__ | {"type": s.type.value} for s in m.steps],
            "persisted_steps": self.steps(mission_id),
            "criteria": m.criteria,
            "targets": m.targets,
            "cost_usd": m.cost_usd,
            "errors": m.errors,
            "trace_id": m.trace_id,
            "created_at": m.created_at,
        }

    def resume(self, ctx: CallContext, mission_id: str) -> Mission:
        m = self.get(mission_id)
        if not m:
            raise KeyError(mission_id)
        if m.state in (MissionState.CREATED, MissionState.PLANNED):
            return self.transition(ctx, mission_id, MissionState.RUNNING, "resume")
        if m.state in (MissionState.WAITING, MissionState.BLOCKED, MissionState.PAUSED):
            return self.transition(ctx, mission_id, MissionState.RUNNING, "resume")
        return m

    def interrupted(self) -> List[Mission]:
        """Missions left RUNNING/VERIFYING by a crash — offered to the user on boot."""
        rows = self.db.query(
            "SELECT * FROM missions WHERE state IN ('RUNNING','VERIFYING','WAITING','BLOCKED')")
        return [self._row_to_mission(r) for r in rows]

    # ---------------------------------------------------------------- internals
    def _persist_step(self, mission_id: str, step: MissionStep, seq: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO mission_steps(step_id, mission_id, type, capability,"
            " params, status, result, seq, depends_on, objective, attempt, max_attempts,"
            " required_role, completion_criteria, started_ms, finished_ms, provider_used,"
            " evidence, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (step.step_id, mission_id, step.type.value, step.capability,
             json.dumps(step.params), step.status, json.dumps(step.result), seq,
             json.dumps(step.depends_on), step.objective, int(step.attempt),
             int(step.max_attempts), step.required_role, step.completion_criteria,
             int(step.started_ms), int(step.finished_ms), step.provider_used,
             json.dumps(step.evidence), step.idempotency_key))

    def _row_to_mission(self, row) -> Mission:
        keys = row.keys()
        m = Mission(
            mission_id=row["mission_id"], goal=row["goal"], owner=row["owner"],
            state=MissionState(row["state"]),
            targets=json.loads(row["targets"] or "[]"),
            criteria=json.loads(row["criteria"] or "[]"),
            trace_id=row["trace_id"], created_at=row["created_at"],
            updated_at=row["updated_at"], cost_usd=row["cost_usd"] or 0.0,
            errors=json.loads(row["errors"] or "[]"),
            schedule=(row["schedule"] if "schedule" in keys else "") or "",
            # Pass 3 — continuous + wake state must round-trip, otherwise a
            # continuous mission is mistaken for a one-off and "completes".
            continuous=bool(row["continuous"]) if "continuous" in keys else False,
            next_run_ms=int(row["next_run_ms"] or 0) if "next_run_ms" in keys else 0,
            last_run_ms=int(row["last_run_ms"] or 0) if "last_run_ms" in keys else 0,
        )
        keys = {r["name"] for r in self.db.query("PRAGMA table_info(mission_steps)")}

        def _g(row, name, default=None):
            return row[name] if name in keys else default

        for s in self.db.query("SELECT * FROM mission_steps WHERE mission_id=? ORDER BY seq",
                               (m.mission_id,)):
            try:
                stype = TaskType(s["type"])
            except ValueError:
                stype = TaskType.UNKNOWN
            m.steps.append(MissionStep(
                step_id=s["step_id"], type=stype, capability=s["capability"],
                params=json.loads(s["params"] or "{}"), status=s["status"],
                result=json.loads(s["result"] or "{}"),
                depends_on=json.loads(_g(s, "depends_on") or "[]"),
                objective=_g(s, "objective", "") or "",
                required_role=_g(s, "required_role", "worker") or "worker",
                completion_criteria=_g(s, "completion_criteria", "") or "",
                attempt=int(_g(s, "attempt", 0) or 0),
                max_attempts=int(_g(s, "max_attempts", 2) or 2),
                started_ms=int(_g(s, "started_ms", 0) or 0),
                finished_ms=int(_g(s, "finished_ms", 0) or 0),
                provider_used=_g(s, "provider_used", "") or "",
                evidence=json.loads(_g(s, "evidence") or "{}"),
                idempotency_key=_g(s, "idempotency_key", "") or ""))
        return m

    def _audit(self, ctx: CallContext, mission_id: str, action: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who=ctx.person_id, action=action, why=detail,
                              mission_id=mission_id, result="", trace_id=ctx.trace_id)


# --------------------------------------------------------------------------- locks
class LockService:
    """Lease-based locks (master spec §2.9). Leases expire so crashes can't deadlock GENIE."""

    def __init__(self, db, default_ttl_ms: int = 30_000):
        self.db = db
        self.default_ttl = default_ttl_ms

    def acquire(self, resource: str, holder: str, ttl_ms: int | None = None) -> bool:
        now = now_ms()
        ttl = ttl_ms or self.default_ttl
        row = self.db.query_one("SELECT * FROM locks WHERE resource=?", (resource,))
        if row and row["lease_until"] > now and row["holder"] != holder:
            return False
        self.db.execute(
            "INSERT INTO locks(resource, holder, lease_until, acquired_at) VALUES(?,?,?,?)"
            " ON CONFLICT(resource) DO UPDATE SET holder=excluded.holder,"
            " lease_until=excluded.lease_until, acquired_at=excluded.acquired_at",
            (resource, holder, now + ttl, now))
        return True

    def release(self, resource: str, holder: str) -> bool:
        cur = self.db.execute("DELETE FROM locks WHERE resource=? AND holder=?", (resource, holder))
        return cur.rowcount > 0

    def release_all(self, holder: str) -> int:
        return self.db.execute("DELETE FROM locks WHERE holder=?", (holder,)).rowcount

    def purge_stale(self) -> int:
        return self.db.execute("DELETE FROM locks WHERE lease_until < ?", (now_ms(),)).rowcount

    def holders(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM locks")]
