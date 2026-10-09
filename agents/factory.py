"""Agent factory (agents/factory.py) — master spec §10.1, §10.2, §10.16, §10.18, §10.19, §10.24.

Creates task-specific agents, and — more importantly — refuses to create them when it should not.

Rules encoded here:

* **Search before creating.** An equivalent healthy agent is reused; a second agent solving the
  same thing is waste and a source of divergence.
* **Capability, not vendor.** An `AgentDefinition` asks for `coding/strong`, never "provider X
  forever" (D-090).
* **Cost-aware composition.** Before a team is built, the factory estimates what it will cost and
  states the single-agent alternative. If one strong agent is cheaper and equally effective, that
  is what it recommends (§10.19).
* **Generated agents do not accumulate.** Mission-scoped agents are disposable and retired at the
  end; only an evaluated candidate may become persistent (§10.2, §10.24).
* **The factory cannot grant capabilities.** It proposes tools and scopes; the PTE decides
  (§10.23).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import AgentDefinition
from core.logging_setup import get_logger
from agents.budget import ESTIMATED_COST_PER_1K, tier_cost_estimate
from agents.contracts import AgentKind, AgentLifecycle, TaskNode, TeamPlan

log = get_logger("agents.factory")

#: Role templates. Deliberately a *starting point*, not a fixed roster: the plan adapts them.
ROLE_TEMPLATES = {
    "lead": {"purpose": "own the mission, decompose it and resolve blockers",
             "model_capability": "reasoning", "quality": "strong"},
    "architect": {"purpose": "decide the structure and the interfaces",
                  "model_capability": "reasoning", "quality": "strong"},
    "backend": {"purpose": "implement the backend and its data flow",
                "model_capability": "coding", "quality": "strong"},
    "frontend": {"purpose": "implement the user-facing surface",
                 "model_capability": "coding", "quality": "standard"},
    "tester": {"purpose": "write and run tests; verify the result independently",
               "model_capability": "coding", "quality": "standard"},
    "reviewer": {"purpose": "review a produced artifact and accept or reject it",
                 "model_capability": "reasoning", "quality": "strong"},
    "researcher": {"purpose": "gather and summarise external information",
                   "model_capability": "research", "quality": "standard"},
    "worker": {"purpose": "carry out one scoped task",
               "model_capability": "reasoning", "quality": "standard"},
}

#: How many agents a mission of a given complexity needs. The point is the *minimum*.
COMPLEXITY_TEAM_SIZE = ((0.35, 1), (0.65, 2), (0.85, 3), (1.01, 4))

#: Per-agent cost estimates (USD) used for the pre-flight estimate.
AGENT_COST_ESTIMATE = {"cheap-fast": 0.002, "standard": 0.01, "strong": 0.05, "specialist": 0.15}


class ExperienceStore:
    """Structured task experience. Never a raw conversation (§10.16)."""

    def __init__(self, db=None):
        self.db = db
        self._records: List[Dict[str, Any]] = []
        if db is not None:
            for row in db.query("SELECT * FROM agent_experience ORDER BY ts, rowid"):
                entry = dict(row)
                entry["tools"] = json.loads(entry["tools"])
                entry["failures"] = json.loads(entry["failures"])
                entry["success"] = bool(entry["success"])
                self._records.append(entry)

    def record(self, *, task_type: str, role: str, strategy: str, provider: str,
               tools: List[str], success: bool, failures: List[str], latency_ms: int,
               cost_usd: float, mission_id: str = "") -> Dict[str, Any]:
        entry = {"task_type": task_type, "role": role, "strategy": strategy,
                 "provider": provider, "tools": list(tools), "success": bool(success),
                 "failures": list(failures)[:5], "latency_ms": int(latency_ms),
                 "cost_usd": round(float(cost_usd), 6), "mission_id": mission_id,
                 "ts": int(time.time() * 1000)}
        self._records.append(entry)
        if self.db is not None:
            try:
                self.db.execute(
                    "INSERT INTO agent_experience(task_type, role, strategy, provider, tools,"
                    " success, failures, latency_ms, cost_usd, mission_id, ts)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (task_type, role, strategy, provider, json.dumps(list(tools)),
                     1 if success else 0, json.dumps(list(failures)[:5]), int(latency_ms),
                     float(cost_usd), mission_id, entry["ts"]))
            except Exception as exc:
                log.debug("experience persist failed: %s", exc)
        return entry

    def best_configuration(self, task_type: str) -> Optional[Dict[str, Any]]:
        """Which role/provider combination has historically worked for this task type?"""
        relevant = [r for r in self._records if r["task_type"] == task_type]
        if not relevant:
            return None
        by_role: Dict[str, Dict[str, Any]] = {}
        for record in relevant:
            bucket = by_role.setdefault(record["role"], {"attempts": 0, "successes": 0,
                                                         "cost": 0.0, "latency": 0})
            bucket["attempts"] += 1
            bucket["successes"] += 1 if record["success"] else 0
            bucket["cost"] += record["cost_usd"]
            bucket["latency"] += record["latency_ms"]
        ranked = sorted(by_role.items(),
                        key=lambda kv: (-(kv[1]["successes"] / kv[1]["attempts"]),
                                        kv[1]["cost"]))
        role, stats = ranked[0]
        return {"task_type": task_type, "role": role,
                "success_rate": round(stats["successes"] / stats["attempts"], 3),
                "attempts": stats["attempts"],
                "average_cost_usd": round(stats["cost"] / stats["attempts"], 6),
                "average_latency_ms": int(stats["latency"] / stats["attempts"])}

    # ------------------------------------------------- distillation (Phase 12)
    def distill(self, *, min_attempts: int = 3) -> List[Dict[str, Any]]:
        """Turn recorded attempts into reusable guidance — training-free group advantage.

        Adapted from Youtu-Agent's training-free GRPO loop, using GENIE's *structured* records
        instead of raw trajectories (§10.16: experience is never a transcript).

        For each task type we compare every configuration (role + provider + strategy) against
        the task type's baseline success rate. A configuration that beats the baseline by a
        meaningful margin, on enough attempts, becomes guidance the factory can act on — with the
        evidence attached, so a suggestion is never a guess.
        """
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for record in self._records:
            by_type.setdefault(record["task_type"], []).append(record)

        guidance: List[Dict[str, Any]] = []
        for task_type, records in by_type.items():
            if len(records) < max(min_attempts, 2):
                continue
            baseline = len([r for r in records if r["success"]]) / len(records)

            buckets: Dict[tuple, List[Dict[str, Any]]] = {}
            for r in records:
                key = (r["role"], r["provider"], r["strategy"])
                buckets.setdefault(key, []).append(r)

            for (role, provider, strategy), group in buckets.items():
                if len(group) < min_attempts:
                    continue
                wins = len([g for g in group if g["success"]])
                rate = wins / len(group)
                advantage = rate - baseline
                if abs(advantage) < 0.05:  # noise, not signal
                    continue
                guidance.append({
                    "task_type": task_type, "role": role, "provider": provider,
                    "strategy": strategy,
                    "verdict": "prefer" if advantage > 0 else "avoid",
                    "advantage": round(advantage, 3),
                    "success_rate": round(rate, 3), "baseline": round(baseline, 3),
                    "attempts": len(group),
                    "avg_cost_usd": round(sum(g["cost_usd"] for g in group) / len(group), 6),
                    "evidence": f"{wins}/{len(group)} succeeded vs baseline "
                                f"{round(baseline * 100)}% over {len(records)} attempt(s)",
                })
        # strongest, best-evidenced guidance first
        guidance.sort(key=lambda g: (-abs(g["advantage"]), -g["attempts"]))
        return guidance

    def guidance_for(self, task_type: str, *, min_attempts: int = 3) -> List[Dict[str, Any]]:
        return [g for g in self.distill(min_attempts=min_attempts)
                if g["task_type"] == task_type]

    def statistics(self) -> Dict[str, Any]:
        successes = len([r for r in self._records if r["success"]])
        return {"records": len(self._records), "successes": successes,
                "success_rate": round(successes / len(self._records), 3)
                if self._records else 0.0,
                "task_types": sorted({r["task_type"] for r in self._records})}


@dataclass
class FactoryResult:
    ok: bool = False
    reused: bool = False
    definition: Optional[AgentDefinition] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reused": self.reused, "reason": self.reason,
                "definition": self.definition.to_dict() if self.definition else None}


class AgentFactory:
    def __init__(self, *, db=None, experience: Optional[ExperienceStore] = None,
                 audit=None):
        self.db = db
        self.experience = experience or ExperienceStore(db)
        self.audit = audit
        self._registry: Dict[str, AgentDefinition] = {}      # live, healthy definitions
        self._candidates: Dict[str, AgentDefinition] = {}    # awaiting evaluation
        self._versions: Dict[str, List[int]] = {}            # profile_key -> versions
        if db is not None:
            for row in db.query("SELECT * FROM agent_definitions ORDER BY created_ms, rowid"):
                self._versions.setdefault(row["profile_key"], []).append(row["version"])
                # Restart must not resurrect finished mission workers or unevaluated candidates.
                if row["active"] and row["kind"] in (AgentKind.PERSISTENT.value,
                                                       AgentKind.BUILTIN.value):
                    definition = AgentDefinition(**json.loads(row["document"]))
                    self._registry[definition.agent_id] = definition

    # ------------------------------------------------------------------ registry
    def register(self, definition: AgentDefinition, *,
                 kind: str = AgentKind.PERSISTENT.value) -> Dict[str, Any]:
        definition.kind = kind
        definition.profile_key = definition.profile_key or definition.role
        definition.version = len(self._versions.get(definition.profile_key, [])) + 1
        self._versions.setdefault(definition.profile_key, []).append(definition.version)
        self._registry[definition.agent_id] = definition
        if self.db is not None:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO agent_definitions(agent_id, profile_key, role, kind,"
                    " version, document, active, created_ms) VALUES(?,?,?,?,?,?,1,?)",
                    (definition.agent_id, definition.profile_key, definition.role,
                     definition.kind, definition.version,
                     json.dumps(definition.to_dict()), int(time.time() * 1000)))
            except Exception as exc:
                log.debug("definition persist failed: %s", exc)
        return {"ok": True, "agent_id": definition.agent_id, "version": definition.version}

    def healthy_agents(self) -> List[AgentDefinition]:
        return [d for d in self._registry.values() if d.kind != AgentKind.CANDIDATE.value]

    def find_suitable(self, *, role: str, capability: str = "") -> Optional[AgentDefinition]:
        """An equivalent healthy agent, if one exists (§10.1)."""
        for definition in self._registry.values():
            if definition.role != role or definition.kind == AgentKind.CANDIDATE.value:
                continue
            if capability and definition.model_capability != capability:
                continue
            return definition
        return None

    # -------------------------------------------------------------------- create
    def create(self, *, role: str, purpose: str = "", capability: str = "",
               quality: str = "", tools: Optional[List[str]] = None,
               skills: Optional[List[str]] = None, scopes: Optional[List[str]] = None,
               memory_scope: Optional[Dict[str, Any]] = None,
               budget: Optional[Dict[str, Any]] = None,
               kind: str = AgentKind.MISSION.value,
               allow_reuse: bool = True) -> FactoryResult:
        template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES["worker"])
        capability = capability or template["model_capability"]
        quality = quality or template.get("quality", "standard")

        if allow_reuse:
            existing = self.find_suitable(role=role, capability=capability)
            if existing is not None:
                return FactoryResult(ok=True, reused=True, definition=existing,
                                     reason=f"reusing healthy {role} agent "
                                            f"{existing.agent_id} (v{existing.version})")

        definition = AgentDefinition(
            name=role,
            role=role,
            purpose=purpose or template["purpose"],
            tools=list(tools or []),
            skills=list(skills or []),
            scopes=list(scopes or []),
            model_capability=capability,
            kind=kind,
            # capability + quality, never a vendor (D-090)
            model_requirement={"capability": capability, "quality": quality},
            memory_scope=dict(memory_scope or {"scope": "mission"}),
            budget=dict(budget or {"tokens": 60_000, "cost_usd": 0.25, "model_calls": 12}),
            ephemeral=kind in (AgentKind.MISSION.value, AgentKind.CANDIDATE.value))
        if kind in (AgentKind.PERSISTENT.value, AgentKind.BUILTIN.value):
            self.register(definition, kind=kind)
        else:
            self._registry[definition.agent_id] = definition
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.create",
                                  why=f"{role} ({capability}/{quality})", result="ok")
            except Exception as exc:
                log.debug("create audit failed: %s", exc)
        return FactoryResult(ok=True, reused=False, definition=definition,
                             reason=f"created {role} ({capability}/{quality})")

    # ------------------------------------------------------------------- planning
    def estimate_team_size(self, complexity: float) -> int:
        """The minimum useful team for a given complexity — never "spawn many"."""
        complexity = max(0.0, min(1.0, float(complexity)))
        for threshold, size in COMPLEXITY_TEAM_SIZE:
            if complexity < threshold:
                return size
        return COMPLEXITY_TEAM_SIZE[-1][1]

    def plan_team(self, *, objective: str, complexity: float = 0.5,
                  needs: Optional[List[str]] = None, mission_id: str = "",
                  budget_usd: float = 0.0) -> TeamPlan:
        """Compose a team and *show the cheaper alternative* (§10.19)."""
        needs = [n for n in (needs or []) if n]
        size = self.estimate_team_size(complexity)
        roles: List[str] = []
        for need in needs[:size]:
            roles.append(need if need in ROLE_TEMPLATES else "worker")
        while len(roles) < size:
            roles.append("worker")
        # a reviewer only above the risk threshold (§10.15)
        if complexity >= 0.6 and "reviewer" not in roles:
            roles.append("reviewer")

        team: List[Dict[str, Any]] = []
        estimated_cost = 0.0
        for role in roles:
            template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES["worker"])
            quality = template.get("quality", "standard")
            per_agent = AGENT_COST_ESTIMATE.get(quality, AGENT_COST_ESTIMATE["standard"])
            estimated_cost += per_agent
            team.append({"role": role, "quality": quality,
                         "capability": template["model_capability"],
                         "purpose": template["purpose"],
                         "estimated_cost_usd": round(per_agent, 6)})

        single_cost = AGENT_COST_ESTIMATE["strong"]
        rationale = (f"complexity {complexity:.2f} → {len(roles)} agent(s): "
                     f"{', '.join(roles)}")
        alternative = (f"a single strong agent would cost about ${single_cost:.3f} and "
                       f"is the right choice when the work has no separable parts")
        if len(roles) <= 1 or estimated_cost <= single_cost:
            rationale += " — a single agent is sufficient here"
        if budget_usd and estimated_cost > budget_usd:
            rationale += (f" — WARNING: estimated ${estimated_cost:.3f} exceeds the "
                          f"${budget_usd:.3f} budget")
        plan = TeamPlan(mission_id=mission_id, objective=objective, team=team,
                        estimated_cost_usd=estimated_cost,
                        estimated_model_calls=len(roles) * 4,
                        rationale=rationale, single_agent_alternative=alternative)
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.plan_team",
                                  why=rationale[:180], mission_id=mission_id or None,
                                  result=f"{len(roles)} agents")
            except Exception as exc:
                log.debug("plan audit failed: %s", exc)
        return plan

    # ------------------------------------------------------------------- versions
    def revise(self, profile_key: str, changes: Dict[str, Any], *,
               evaluate: bool = True) -> Dict[str, Any]:
        """A revised profile becomes a *candidate*; it never silently replaces the permanent one."""
        current = next((d for d in self._registry.values()
                        if d.profile_key == profile_key), None)
        if current is None:
            return {"ok": False, "error": f"unknown profile {profile_key}"}
        candidate = AgentDefinition(**{**current.__dict__})
        candidate.agent_id = f"{current.agent_id}_c{len(self._versions.get(profile_key, []))}"
        candidate.version = len(self._versions.get(profile_key, [])) + 1
        candidate.kind = AgentKind.CANDIDATE.value
        candidate.ephemeral = True
        for key, value in changes.items():
            if hasattr(candidate, key):
                setattr(candidate, key, value)
        self._candidates[candidate.agent_id] = candidate
        if self.db is not None:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO agent_definitions(agent_id, profile_key, role, kind,"
                    " version, document, active, created_ms) VALUES(?,?,?,?,?,?,0,?)",
                    (candidate.agent_id, profile_key, candidate.role, candidate.kind,
                     candidate.version, json.dumps(candidate.to_dict()),
                     int(time.time() * 1000)))
            except Exception as exc:
                log.debug("candidate persist failed: %s", exc)
        return {"ok": True, "candidate_id": candidate.agent_id,
                "version": candidate.version, "active": current.agent_id,
                "note": "the candidate must be evaluated before it can replace the active profile"}

    def promote(self, candidate_id: str) -> Dict[str, Any]:
        """Promote an evaluated candidate to the active profile.

        The previous active profile is retired rather than mutated — a permanent agent is never
        silently rewritten (§10.24).
        """
        candidate = self._candidates.pop(candidate_id, None)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate {candidate_id}"}
        superseded: List[str] = []
        for existing in list(self._registry.values()):
            if existing.profile_key == candidate.profile_key \
                    and existing.kind != AgentKind.CANDIDATE.value:
                self._registry.pop(existing.agent_id, None)
                superseded.append(existing.agent_id)
        candidate.kind = AgentKind.PERSISTENT.value
        candidate.ephemeral = False
        self.register(candidate, kind=AgentKind.PERSISTENT.value)
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.promote_candidate",
                                  why=f"{candidate.profile_key} v{candidate.version}",
                                  result=f"superseded {len(superseded)}")
            except Exception as exc:
                log.debug("promote audit failed: %s", exc)
        return {"ok": True, "promoted": candidate.agent_id,
                "profile_key": candidate.profile_key, "version": candidate.version,
                "superseded": superseded}

    def reject(self, candidate_id: str, *, reason: str = "") -> Dict[str, Any]:
        candidate = self._candidates.pop(candidate_id, None)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate {candidate_id}"}
        if self.audit:
            try:
                self.audit.record(who="system", action="agent.reject_candidate",
                                  why=reason[:160] or candidate_id, result="rejected")
            except Exception as exc:
                log.debug("reject audit failed: %s", exc)
        return {"ok": True, "rejected": candidate_id, "reason": reason}

    def retire(self, agent_id: str) -> Dict[str, Any]:
        definition = self._registry.pop(agent_id, None)
        if definition is None:
            return {"ok": False, "error": f"unknown agent {agent_id}"}
        if self.db is not None:
            self.db.execute("UPDATE agent_definitions SET active=0 WHERE agent_id=?", (agent_id,))
        return {"ok": True, "retired": agent_id}

    # --------------------------------------------------------- personas (§11B)
    def create_from_persona(self, need: str, *, purpose: str = "", division: str = "",
                            budget: Optional[Dict[str, Any]] = None,
                            kind: str = AgentKind.MISSION.value,
                            allow_reuse: bool = False) -> FactoryResult:
        """Role templates → Agent Factory → task-specific agent.

        Backed by the vendored **agency-agents** corpus (``data/personas/``). The corpus is
        *data*, not 264 permanent agents: we resolve the best-matching persona for this need and
        instantiate exactly one agent for it. If the corpus is absent we degrade to the built-in
        worker template instead of failing.
        """
        from agents.personas import get_personas

        lib = get_personas()
        persona = None
        if division:
            pool = lib.by_division(division)
            persona = pool[0] if pool else None
        if persona is None:
            persona = lib.best(need)
        if persona is None:
            return self.create(role="worker", purpose=purpose or need, budget=budget,
                               kind=kind, allow_reuse=allow_reuse)

        spec = persona.to_agent_spec(purpose or need)
        result = self.create(role=spec["role"], purpose=spec["purpose"],
                             capability=spec["capability"], quality=spec["quality"],
                             budget=budget, kind=kind, allow_reuse=allow_reuse)
        if result.definition is not None:
            try:
                result.definition.persona = persona.to_dict()
            except Exception:  # AgentDefinition is a contract; never fail creation on metadata
                log.debug("could not attach persona metadata")
        return result

    def persona_statistics(self) -> Dict[str, Any]:
        from agents.personas import get_personas
        return get_personas().statistics()

    # ------------------------------------------------------ self-improvement (§12)
    def recommend_configuration(self, task_type: str) -> Optional[Dict[str, Any]]:
        """Which configuration has historically worked for this task type?

        This closes the loop: past attempts are distilled into guidance, and the next agent for
        the same kind of work is built with the configuration that actually won. Returns ``None``
        when there is not yet enough evidence — the factory then falls back to its defaults
        instead of acting on a guess.
        """
        if self.experience is None:
            return None
        try:
            options = self.experience.guidance_for(task_type)
        except Exception:  # never let the improvement loop break agent creation
            return None
        preferred = [g for g in options if g["verdict"] == "prefer"]
        if not preferred:
            return None
        best = max(preferred, key=lambda g: (g["advantage"], g["attempts"]))
        return {"task_type": task_type, "role": best["role"], "provider": best["provider"],
                "strategy": best["strategy"], "advantage": best["advantage"],
                "attempts": best["attempts"], "evidence": best["evidence"]}

    def improvement_report(self, *, min_attempts: int = 3) -> Dict[str, Any]:
        """What has the fleet learned so far? (evidence-backed, never a guess)"""
        guidance = self.experience.distill(min_attempts=min_attempts) \
            if self.experience is not None else []
        return {"guidance": guidance, "count": len(guidance),
                "experience": self.experience.statistics()
                if self.experience is not None else {}}

    def status(self) -> Dict[str, Any]:
        return {"agents": len(self._registry), "candidates": len(self._candidates),
                "profiles": {k: v for k, v in self._versions.items()},
                "experience": self.experience.statistics()}
