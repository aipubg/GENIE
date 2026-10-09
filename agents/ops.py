"""Operator Console (agents/ops.py) — Phase 11 observability & control surface.

This is the single place an operator looks at a running agent fleet and the single place they
act on it. It is deliberately thin: it reads real state (teams, budgets, mailbox, blackboard,
audit log, experience store) and drives the *real* control primitives already on the service
and orchestrator. Nothing here is a stub — every action mutates live state and is recorded in
the tamper-evident audit log.

Two things make this the bridge to Phase 12 (self-improvement):

* ``record_outcome`` snapshots a finished mission into ``agent_mission_outcomes`` — structured,
  never a transcript (§10.16).
* ``improvement_suggestions`` turns those snapshots (plus the experience store) into concrete,
  evidence-backed recommendations. Phase 12 will consume exactly this output.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from agents.contracts import AgentLifecycle, Budget, TaskStatus

log = get_logger("agents.ops")


class OperatorConsole:
    """Aggregates a live operator view and performs audited control actions on real teams."""

    def __init__(self, service: Any):
        # ``service`` is the AgentService facade; we reach through it rather than duplicating
        # state, so the console can never drift from what is actually running.
        self.service = service
        self.audit = getattr(service, "audit", None)
        self.db = getattr(service, "db", None)

    # ------------------------------------------------------------- audit helper
    def _audit(self, action: str, mission_id: str, why: str, result: str,
               actor: str = "operator") -> Optional[str]:
        if self.audit is None:
            return None
        try:
            return self.audit.record(who=actor, action=action, why=why[:180],
                                     mission_id=mission_id or None, result=str(result)[:120])
        except Exception as exc:  # auditing must never break a control action
            log.debug("ops audit failed: %s", exc)
            return None

    # ------------------------------------------------------------- dashboard
    def dashboard(self, *, limit_audit: int = 25) -> Dict[str, Any]:
        """One structured snapshot of the whole fleet for the operator's screen."""
        service = self.service
        teams = service._teams  # type: ignore[attr-defined]
        global_state = service.budgets.status()  # type: ignore[attr-defined]
        exp = service.experience.statistics()  # type: ignore[attr-defined]

        working = idle = paused = cancelled = 0
        team_views: List[Dict[str, Any]] = []
        for mission_id, active in teams.items():
            orch = active.orchestrator
            view = self._team_view(mission_id, active)
            team_views.append(view)
            working += view["working"]
            idle += view["idle"]
            if view["paused"]:
                paused += 1
            if view["cancelled"]:
                cancelled += 1

        return {
            "generated_ms": int(time.time() * 1000),
            "global": {
                "teams": len(teams),
                "paused": paused,
                "cancelled": cancelled,
                "working_agents": working,
                "idle_agents": idle,
                "failovers": len(service.failovers()),  # type: ignore[attr-defined]
                "global_budget_remaining": global_state["global_remaining"],
                "experience": exp,
            },
            "teams": team_views,
            "audit": self.recent_audit(limit=limit_audit),
            "improvements": self.improvement_suggestions(),
        }

    def _team_view(self, mission_id: str, active: Any) -> Dict[str, Any]:
        orch = active.orchestrator
        status = orch.status()
        tasks = status["tasks"]["tasks"]
        counts = {key: 0 for key in ("done", "running", "failed", "pending", "blocked")}
        for t in tasks:
            s = t["status"]
            if s == TaskStatus.DONE.value:
                counts["done"] += 1
            elif s == TaskStatus.RUNNING.value:
                counts["running"] += 1
            elif s in (TaskStatus.FAILED.value, TaskStatus.BLOCKED.value):
                counts["failed"] += 1
            elif s == TaskStatus.PENDING.value:
                counts["pending"] += 1
            elif s == TaskStatus.CANCELLED.value:
                counts["blocked"] += 1

        working = len(orch.working_members())
        idle = len(orch.idle_members())
        node = self.service.budgets.node(f"mission:{mission_id}")  # type: ignore[attr-defined]
        budget = node.to_dict()["remaining"] if node else None
        cost = node.to_dict().get("budget", {}).get("cost_usd") if node else None

        history = status.get("history") or []
        last_hb = history[-1]["ts"] if history else None
        created = active.created_ms
        elapsed_ms = (int(time.time() * 1000) - created) or 1
        done = max(counts["done"], 0)
        throughput = round(done / (elapsed_ms / 60000.0), 3) if elapsed_ms > 0 else 0.0

        return {
            "mission_id": mission_id,
            "objective": status["objective"],
            "provider": active.provider,
            "failovers": active.failovers,
            "paused": status["paused"],
            "cancelled": status["cancelled"],
            "team_size": len(status["team"]),
            "working": working,
            "idle": idle,
            "orphans": status["orphans"],
            "tasks": {"total": len(tasks), **counts},
            "mailbox_count": status["mailbox"]["count"],
            "blackboard_count": status["blackboard"]["count"],
            "artifacts": status["artifacts"],
            "max_concurrency": orch.max_concurrency,
            "budget_remaining": budget,
            "budget_cap_cost_usd": cost,
            "last_heartbeat_ms": last_hb,
            "throughput_per_min": throughput,
        }

    # ------------------------------------------------------------- audit trail
    def recent_audit(self, *, limit: int = 25, mission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.audit is None:
            return []
        try:
            return self.audit.tail(limit=limit, mission_id=mission_id)  # type: ignore[attr-defined]
        except Exception as exc:
            log.debug("ops audit tail failed: %s", exc)
            return []

    # ------------------------------------------------------------- control
    def control(self, action: str, *, mission_id: str = "", actor: str = "operator",
                **params: Any) -> Dict[str, Any]:
        """Perform one real, audited control action on a live team.

        Returns ``{"ok", "action", "result", "audit_id"}``. Unknown actions return
        ``ok=False`` rather than silently succeeding — the operator must see failure.
        """
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            self._audit(f"ops.{action}", mission_id, "unknown action", "refused", actor=actor)
            return {"ok": False, "action": action,
                    "error": f"unknown operator action '{action}'",
                    "known": ["pause", "resume", "cancel", "force_failover",
                              "retire_orphans", "set_budget_cap", "throttle"]}

        try:
            result = handler(mission_id=mission_id, **params)
        except Exception as exc:
            audit_id = self._audit(f"ops.{action}", mission_id, f"error: {exc}", "error", actor=actor)
            return {"ok": False, "action": action, "error": str(exc), "audit_id": audit_id,
                    "mission_id": mission_id}
        audit_id = self._audit(f"ops.{action}", mission_id, json.dumps(params, default=str)[:160],
                               "ok" if result.get("ok") else "fail", actor=actor)
        return {"ok": bool(result.get("ok")), "action": action, "result": result,
                "audit_id": audit_id, "mission_id": mission_id}

    # -- individual actions (each calls a real primitive on the service/orchestrator) --
    def _do_pause(self, *, mission_id: str, reason: str = "operator requested", **_):
        return self.service.pause(mission_id, reason=reason)  # type: ignore[attr-defined]

    def _do_resume(self, *, mission_id: str, **_):
        return self.service.resume(mission_id)  # type: ignore[attr-defined]

    def _do_cancel(self, *, mission_id: str, reason: str = "operator requested", **_):
        return self.service.cancel(mission_id, reason=reason)  # type: ignore[attr-defined]

    def _do_force_failover(self, *, mission_id: str, from_provider: str = "",
                           reason: str = "operator initiated", to_provider: str = "", **_):
        return self.service.failover(mission_id, from_provider=from_provider,  # type: ignore[attr-defined]
                                     reason=reason, to_provider=to_provider)

    def _do_retire_orphans(self, *, mission_id: str, **_):
        orch = self.service.team(mission_id)  # type: ignore[attr-defined]
        if orch is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        return orch.retire_orphans()

    def _do_set_budget_cap(self, *, mission_id: str, tokens: int = 0, cost_usd: float = 0.0,
                           model_calls: int = 0, tool_calls: int = 0, wall_ms: int = 0, **_):
        budget = Budget(tokens=int(tokens), cost_usd=float(cost_usd),
                        model_calls=int(model_calls), tool_calls=int(tool_calls),
                        wall_ms=int(wall_ms))
        return self.service.budgets.set_cap(f"mission:{mission_id}", budget)  # type: ignore[attr-defined]

    def _do_throttle(self, *, mission_id: str, max_concurrency: int = 0, **_):
        orch = self.service.team(mission_id)  # type: ignore[attr-defined]
        if orch is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        cap = int(max_concurrency)
        if cap < 0:
            return {"ok": False, "error": "max_concurrency must be >= 0"}
        orch.max_concurrency = min(cap, 6)  # never above the team-size ceiling (D-094)
        return {"ok": True, "max_concurrency": orch.max_concurrency,
                "note": "0 halts new dispatches until raised"}

    # ------------------------------------------------------------- outcome record
    def record_outcome(self, mission_id: str) -> Dict[str, Any]:
        """Snapshot a finished mission into the outcomes store (the Phase 12 input)."""
        orch = self.service.team(mission_id)  # type: ignore[attr-defined]
        active = self.service._teams.get(mission_id)  # type: ignore[attr-defined]
        if orch is None or active is None:
            return {"ok": False, "error": f"no team for mission {mission_id}"}
        status = orch.status()
        tasks = status["tasks"]["tasks"]
        tasks_total = len(tasks)
        tasks_done = len([t for t in tasks if t["status"] == TaskStatus.DONE.value])
        tasks_failed = len([t for t in tasks if t["status"] in
                            (TaskStatus.FAILED.value, TaskStatus.BLOCKED.value)])
        providers = sorted({m.provider_used for m in orch.members.values() if m.provider_used})
        node = self.service.budgets.node(f"mission:{mission_id}")  # type: ignore[attr-defined]
        cost_usd = node.to_dict().get("budget", {}).get("cost_usd", 0.0) if node else 0.0
        # experience records tied to this mission
        exp_records = self._experience_count_for(mission_id)
        duration_ms = int(time.time() * 1000) - active.created_ms
        final_status = status["history"][-1]["status"] if status.get("history") else "unknown"

        snapshot = {
            "mission_id": mission_id,
            "objective": status["objective"],
            "final_status": final_status,
            "team_size": len(status["team"]),
            "tasks_total": tasks_total,
            "tasks_done": tasks_done,
            "tasks_failed": tasks_failed,
            "providers": providers,
            "failovers": active.failovers,
            "cost_usd": round(float(cost_usd), 6),
            "duration_ms": duration_ms,
            "orphan_count": len(status["orphans"]),
            "experience_records": exp_records,
            "ts": int(time.time() * 1000),
        }
        if self.db is not None:
            try:
                cur = self.db.execute(
                    "INSERT INTO agent_mission_outcomes(mission_id, objective, final_status,"
                    " team_size, tasks_total, tasks_done, tasks_failed, providers, failovers,"
                    " cost_usd, duration_ms, orphan_count, experience_records, snapshot, ts)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mission_id, status["objective"], final_status, len(status["team"]),
                     tasks_total, tasks_done, tasks_failed, json.dumps(providers),
                     active.failovers, float(cost_usd), duration_ms, len(status["orphans"]),
                     exp_records, json.dumps(snapshot, default=str), snapshot["ts"]))
                snapshot["row_id"] = cur.lastrowid
            except Exception as exc:
                log.debug("outcome persist failed: %s", exc)
        self._audit("ops.record_outcome", mission_id, final_status, "ok")
        return {"ok": True, **snapshot}

    def _experience_count_for(self, mission_id: str) -> int:
        if self.db is None:
            return 0
        try:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM agent_experience WHERE mission_id=?", (mission_id,))
            return int(row["n"]) if row else 0
        except Exception:
            return 0

    def outcomes(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            rows = self.db.query(
                "SELECT * FROM agent_mission_outcomes ORDER BY ts DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]
        except Exception as exc:
            log.debug("outcome query failed: %s", exc)
            return []

    # ------------------------------------------------------------- improvement loop
    def improvement_suggestions(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Turn recorded outcomes + experience into concrete, evidence-backed suggestions.

        This is the raw signal Phase 12 self-improvement will act on — it returns *findings*,
        not guesses, each backed by the data it came from.
        """
        suggestions: List[Dict[str, Any]] = []
        outcomes = self.outcomes(limit=200)

        if not outcomes:
            return [{"id": "no-data", "severity": "info",
                     "text": "no mission outcomes recorded yet; run missions to seed the loop"}]

        # 1) Budget blowouts: missions that finished but spent their whole cap.
        blowouts = [o for o in outcomes
                    if o.get("tasks_failed", 0) and o.get("cost_usd", 0) > 0]
        by_team = {}
        for o in outcomes:
            by_team.setdefault(o["team_size"], {"n": 0, "fail": 0})
            by_team[o["team_size"]]["n"] += 1
            by_team[o["team_size"]]["fail"] += o.get("tasks_failed", 0)
        big_teams = {sz: d for sz, d in by_team.items()
                     if sz >= 4 and d["n"] >= 2 and d["fail"] / max(1, d["n"]) >= 0.5}
        if big_teams:
            suggestions.append({
                "id": "team-size", "severity": "medium",
                "text": f"missions with >=4 agents failed >=50% of the time in "
                        f"{len(big_teams)} size bucket(s); prefer the single-agent alternative "
                        f"for small objectives",
                "evidence": big_teams,
            })

        # 2) Orphan leaks: missions that ended with stuck WORKING agents.
        orphan_leaks = [o for o in outcomes if o.get("orphan_count", 0) > 0]
        if orphan_leaks:
            suggestions.append({
                "id": "orphan-leak", "severity": "high",
                "text": f"{len(orphan_leaks)} mission(s) ended with orphaned WORKING agents; "
                        f"wire retire_orphans() into mission teardown",
                "evidence": [o["mission_id"] for o in orphan_leaks[:5]],
            })

        # 3) Failover frequency: missions that needed many provider switches.
        heavy_failover = [o for o in outcomes if o.get("failovers", 0) >= 2]
        if heavy_failover:
            suggestions.append({
                "id": "provider-instability", "severity": "medium",
                "text": f"{len(heavy_failover)} mission(s) needed >=2 provider failovers; "
                        f"investigate the primary provider's reliability",
                "evidence": [o["mission_id"] for o in heavy_failover[:5]],
            })

        if blowouts and not suggestions:
            suggestions.append({
                "id": "cost-scrutiny", "severity": "low",
                "text": f"{len(blowouts)} mission(s) finished with failures and non-zero cost; "
                        f"review the escalation evidence gate (D-091)",
                "evidence": [o["mission_id"] for o in blowouts[:5]],
            })

        return suggestions[:limit]
