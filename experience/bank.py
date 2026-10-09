"""Trajectory recording + semantic group advantage + textual experience bank.

The pipeline mirrors Youtu-Agent's Training-Free GRPO, minus the model:

    rollouts for one task_type
        -> split into successes and failures
        -> compare tool usage, strategy and failure modes
        -> emit SEMANTIC advantages as short natural-language lessons
        -> store, rank by support, retrieve for future runs

Everything is derived from recorded outcomes. With too few rollouts the bank
says so rather than inventing a lesson.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("experience.bank")

# Minimum rollouts before any comparative claim is made. Below this the bank
# reports insufficient evidence — a lesson derived from one run is a guess.
MIN_GROUP = 3
MIN_PER_SIDE = 1


def _now() -> int:
    return int(time.time() * 1000)


class ExperienceBank:
    """Records trajectories and derives semantic advantages from them."""

    def __init__(self, db) -> None:
        self.db = db

    # ------------------------------------------------------------ recording
    def record(self, *, task_type: str, steps: List[Dict[str, Any]],
               success: bool, failures: Optional[List[str]] = None,
               tools: Optional[List[str]] = None, strategy: str = "",
               provider: str = "", role: str = "", mission_id: str = "",
               latency_ms: int = 0, cost_usd: float = 0.0) -> Dict[str, Any]:
        tid = f"trj_{uuid.uuid4().hex[:12]}"
        summary = self.summarise(steps, success=success,
                                 failures=failures or [])
        now = _now()
        self.db.execute(
            "INSERT INTO agent_trajectories(trajectory_id, task_type, role, "
            "strategy, provider, tools, steps, step_count, success, failures, "
            "summary, mission_id, latency_ms, cost_usd, ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, task_type, role, strategy, provider,
             json.dumps(sorted(set(tools or []))), json.dumps(steps or []),
             len(steps or []), 1 if success else 0,
             json.dumps((failures or [])[:8]), summary, mission_id,
             int(latency_ms), float(cost_usd), now))
        return {"trajectory_id": tid, "task_type": task_type, "success": bool(success),
                "step_count": len(steps or []), "summary": summary}

    # --------------------------------------------------------- summarisation
    @staticmethod
    def summarise(steps: List[Dict[str, Any]], *, success: bool,
                  failures: Optional[List[str]] = None) -> str:
        """A short factual digest of one trajectory — no interpretation."""
        if not steps:
            return "no steps recorded"
        caps = []
        for s in steps:
            cap = s.get("capability") or s.get("tool") or s.get("action")
            if cap:
                caps.append(str(cap))
        ok = sum(1 for s in steps if s.get("ok") is True)
        failed = sum(1 for s in steps if s.get("ok") is False)
        parts = [f"{len(steps)} step(s)", f"{ok} ok", f"{failed} failed"]
        if caps:
            parts.append("path: " + " -> ".join(caps[:8]))
        if failures:
            parts.append("errors: " + ", ".join(str(f)[:40] for f in failures[:3]))
        parts.append("outcome: " + ("success" if success else "failure"))
        return "; ".join(parts)

    # ------------------------------------------------------------ retrieval
    def trajectories(self, task_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM agent_trajectories WHERE task_type=? "
            "ORDER BY ts DESC LIMIT ?", (task_type, int(limit)))
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> Dict[str, Any]:
        def load(v, default):
            try:
                return json.loads(v) if v else default
            except Exception:  # noqa: BLE001
                return default
        return {"trajectory_id": r["trajectory_id"], "task_type": r["task_type"],
                "role": r["role"], "strategy": r["strategy"], "provider": r["provider"],
                "tools": load(r["tools"], []), "steps": load(r["steps"], []),
                "step_count": r["step_count"], "success": bool(r["success"]),
                "failures": load(r["failures"], []), "summary": r["summary"],
                "mission_id": r["mission_id"], "ts": r["ts"]}

    # -------------------------------------------------------- group advantage
    def group_advantage(self, task_type: str) -> Dict[str, Any]:
        """Compare successes vs failures for a task and derive SEMANTIC lessons.

        This is the Training-Free GRPO step: the 'gradient' is a set of
        natural-language differences, not numbers. Requires at least MIN_GROUP
        rollouts and at least one on each side.
        """
        trajs = self.trajectories(task_type)
        if len(trajs) < MIN_GROUP:
            return {"ok": False, "task_type": task_type, "lessons": [],
                    "reason": f"insufficient evidence: {len(trajs)} rollout(s), "
                              f"need {MIN_GROUP}"}

        wins = [t for t in trajs if t["success"]]
        losses = [t for t in trajs if not t["success"]]
        if len(wins) < MIN_PER_SIDE or len(losses) < MIN_PER_SIDE:
            return {"ok": False, "task_type": task_type, "lessons": [],
                    "reason": f"need at least {MIN_PER_SIDE} success(es) and "
                              f"{MIN_PER_SIDE} failure(s); have "
                              f"{len(wins)}/{len(losses)}"}

        lessons: List[Dict[str, Any]] = []

        # --- tools present in wins but absent from every loss -------------
        win_tools: Dict[str, int] = {}
        for t in wins:
            for tool in t["tools"]:
                win_tools[tool] = win_tools.get(tool, 0) + 1
        loss_tools: Dict[str, int] = {}
        for t in losses:
            for tool in t["tools"]:
                loss_tools[tool] = loss_tools.get(tool, 0) + 1

        for tool, n in sorted(win_tools.items(), key=lambda kv: -kv[1]):
            if loss_tools.get(tool, 0) == 0:
                lessons.append({
                    "kind": "tool_advantage", "text":
                    f"Using '{tool}' appeared in {n}/{len(wins)} successful run(s) "
                    f"and no failing run — prefer it for {task_type}.",
                    "support": n, "confidence": round(n / max(1, len(wins)), 3)})
        for tool, n in sorted(loss_tools.items(), key=lambda kv: -kv[1]):
            if win_tools.get(tool, 0) == 0:
                lessons.append({
                    "kind": "tool_risk", "text":
                    f"Using '{tool}' appeared in {n}/{len(losses)} failing run(s) "
                    f"and no successful run — avoid it for {task_type}.",
                    "support": n, "confidence": round(n / max(1, len(losses)), 3)})

        # --- recurring failure modes --------------------------------------
        modes: Dict[str, int] = {}
        for t in losses:
            for f in t["failures"]:
                key = str(f)[:80]
                modes[key] = modes.get(key, 0) + 1
        for mode, n in sorted(modes.items(), key=lambda kv: -kv[1])[:3]:
            if n >= 2:
                lessons.append({
                    "kind": "failure_mode", "text":
                    f"Avoid the recurring failure '{mode}' ({n} occurrence(s)); "
                    f"plan around it for {task_type}.",
                    "support": n, "confidence": round(n / max(1, len(losses)), 3)})

        # --- strategy comparison ------------------------------------------
        strat: Dict[str, List[int]] = {}
        for t in trajs:
            if not t["strategy"]:
                continue
            s = strat.setdefault(t["strategy"], [0, 0])
            s[0 if t["success"] else 1] += 1
        ranked = sorted(strat.items(), key=lambda kv: (kv[1][0] - kv[1][1]), reverse=True)
        if ranked and ranked[0][1][0] > 0:
            best, (w, l) = ranked[0]
            lessons.append({
                "kind": "strategy_advantage", "text":
                f"Strategy '{best}' has the best record for {task_type}: "
                f"{w} success(es), {l} failure(s).",
                "support": w, "confidence": round(w / max(1, w + l), 3)})

        lessons.sort(key=lambda x: -x["support"])
        return {"ok": True, "task_type": task_type, "lessons": lessons,
                "successes": len(wins), "failures": len(losses)}

    # ------------------------------------------------------ experience bank
    def refresh(self, task_type: str) -> List[Dict[str, Any]]:
        """Recompute and persist the lessons for one task type."""
        adv = self.group_advantage(task_type)
        self.db.execute("DELETE FROM agent_experience_bank WHERE task_type=?",
                        (task_type,))
        if not adv.get("ok"):
            return []
        now = _now()
        for les in adv["lessons"]:
            self.db.execute(
                "INSERT INTO agent_experience_bank(lesson_id, task_type, kind, "
                "text, support, confidence, updated_at) VALUES(?,?,?,?,?,?,?)",
                (f"les_{uuid.uuid4().hex[:12]}", task_type, les["kind"],
                 les["text"], int(les["support"]), float(les["confidence"]), now))
        return adv["lessons"]

    def retrieve(self, task_type: str, *, limit: int = 5,
                 min_confidence: float = 0.0) -> List[Dict[str, Any]]:
        """Lessons to inject into a future run's context, best-supported first."""
        rows = self.db.query(
            "SELECT * FROM agent_experience_bank WHERE task_type=? AND "
            "confidence>=? ORDER BY support DESC, confidence DESC LIMIT ?",
            (task_type, float(min_confidence), int(limit)))
        return [dict(r) for r in rows]

    def all_lessons(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM agent_experience_bank ORDER BY support DESC LIMIT ?",
            (int(limit),))]

    # ------------------------------------------------------------- querying
    def query(self, *, mission_id: str = "", capability: str = "",
              agent: str = "", outcome: str = "", provider: str = "",
              limit: int = 100) -> List[Dict[str, Any]]:
        """Recorded experience, filtered by what actually exists in the schema.

        Filters map to real columns only - mission_id, task_type (capability),
        role (agent profile), success (outcome), provider. Nothing is inferred
        and no filter is silently ignored: unsupported values simply match
        nothing rather than broadening the query.
        """
        # record() writes trajectories; agent_experience is a different,
        # narrower table. Querying the wrong one would silently return nothing
        # and look like "GENIE has learned nothing".
        sql = ("SELECT task_type, role, strategy, provider, tools, success,"
               " failures, latency_ms, cost_usd, mission_id, ts, summary"
               " FROM agent_trajectories")
        where, args = [], []
        if mission_id:
            where.append("mission_id = ?")
            args.append(mission_id)
        if capability:
            where.append("task_type = ?")
            args.append(capability)
        if agent:
            where.append("role = ?")
            args.append(agent)
        if provider:
            where.append("provider = ?")
            args.append(provider)
        if outcome == "success":
            where.append("success = 1")
        elif outcome == "failure":
            where.append("success = 0")
        elif outcome:
            # An unknown outcome matches nothing, rather than silently
            # widening to "everything" and pretending the filter worked.
            return []
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))

        out = []
        for r in self.db.query(sql, tuple(args)):
            row = dict(r)
            row["success"] = bool(row.get("success"))
            for field in ("tools", "failures"):
                try:
                    row[field] = json.loads(row.get(field) or "[]")
                except Exception:  # noqa: BLE001
                    row[field] = []
            out.append(row)
        return out

    def digest(self, *, mission_id: str = "", capability: str = "",
               agent: str = "", provider: str = "") -> Dict[str, Any]:
        """Turn raw trajectories into durable learning a person can act on.

        Six things, all derived from recorded rows:
          successful_strategies, failed_approaches, verifier lessons, what each
          agent profile learned, reusable provider configuration, and an
          outcome summary. Counters are real; nothing is invented to fill a gap.
        """
        rows = self.query(mission_id=mission_id, capability=capability,
                          agent=agent, provider=provider, limit=500)
        successes = [r for r in rows if r["success"]]
        failures = [r for r in rows if not r["success"]]

        def tally(items, key):
            counts: Dict[str, int] = {}
            for it in items:
                k = str(it.get(key) or "")
                if k:
                    counts[k] = counts.get(k, 0) + 1
            return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

        by_capability: Dict[str, Dict[str, int]] = {}
        for r in rows:
            slot = by_capability.setdefault(str(r.get("task_type") or ""),
                                            {"total": 0, "success": 0, "failure": 0})
            slot["total"] += 1
            slot["success" if r["success"] else "failure"] += 1

        latencies = [int(r.get("latency_ms") or 0) for r in rows
                     if int(r.get("latency_ms") or 0) > 0]
        return {
            "count": len(rows),
            "successful_strategies": tally(successes, "strategy"),
            "failed_approaches": tally(failures, "strategy"),
            # Verifier lessons: the recorded failure reasons themselves.
            "verifier_lessons": [{"strategy": r.get("strategy") or "",
                                  "failures": r.get("failures") or []}
                                 for r in failures if r.get("failures")][:20],
            "agent_profile_learning": tally(rows, "role"),
            "reusable_configuration": tally(rows, "provider"),
            "by_capability": by_capability,
            "outcome_summary": {
                "success": len(successes), "failure": len(failures),
                "success_rate": (round(len(successes) / len(rows), 3)
                                 if rows else None),
                "median_latency_ms": (sorted(latencies)[len(latencies) // 2]
                                      if latencies else None),
            },
        }
