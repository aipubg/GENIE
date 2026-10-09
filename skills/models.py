"""Skill model (skills/models.py) — the canonical skill schema.

A skill is **not** a prompt and **not** a raw macro. It is a structured, versioned, verifiable
procedure:

    inputs / outputs        what it needs and produces
    preconditions           what must be true before it may run
    requirements            capabilities, plugins, devices
    steps                   capability invocations (never coordinates)
    variables               values that change between runs
    verification            how success is decided (not "all steps were issued")
    failure_paths           what to do when a step or the verification fails
    provenance              where it came from (teaching, mission, manual, import)
    statistics              success/failure/verification-failure/takeover/latency/cost
    scope                   global | user | project | application | device

Environment-specific values are **not** baked in: steps refer to `${variables}`.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.contracts import new_id


class SkillStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATING = "validating"
    ACTIVE = "active"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class SkillScope(str, Enum):
    GLOBAL = "global"
    USER = "user"
    PROJECT = "project"
    APPLICATION = "application"
    DEVICE = "device"


ALLOWED_TRANSITIONS: Dict[SkillStatus, set] = {
    SkillStatus.CANDIDATE: {SkillStatus.VALIDATING, SkillStatus.REJECTED,
                            SkillStatus.ARCHIVED, SkillStatus.ACTIVE},
    SkillStatus.VALIDATING: {SkillStatus.ACTIVE, SkillStatus.REJECTED},
    SkillStatus.ACTIVE: {SkillStatus.DEPRECATED, SkillStatus.DISABLED, SkillStatus.ARCHIVED},
    SkillStatus.DEPRECATED: {SkillStatus.ARCHIVED, SkillStatus.ACTIVE},
    SkillStatus.DISABLED: {SkillStatus.ACTIVE, SkillStatus.ARCHIVED},
    SkillStatus.REJECTED: {SkillStatus.CANDIDATE, SkillStatus.ARCHIVED},
    SkillStatus.ARCHIVED: set(),
}

_VAR_RE = re.compile(r"\$\{([a-zA-Z0-9_.]+)\}")


# --------------------------------------------------------------------------- steps
@dataclass
class SkillStep:
    id: str = field(default_factory=lambda: new_id("step"))
    capability: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    # state-based wait that must be satisfied after the step (never a fixed sleep)
    wait_for: Optional[Dict[str, Any]] = None
    optional: bool = False
    on_failure: str = "fail"          # fail | skip | retry | fallback
    max_retries: int = 1
    fallback: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "capability": self.capability, "params": self.params,
                "description": self.description, "wait_for": self.wait_for,
                "optional": self.optional, "on_failure": self.on_failure,
                "max_retries": self.max_retries, "fallback": self.fallback}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SkillStep":
        return cls(
            id=str(raw.get("id") or new_id("step")),
            capability=str(raw.get("capability", "")),
            params=raw.get("params") or {},
            description=str(raw.get("description", "")),
            wait_for=raw.get("wait_for"),
            optional=bool(raw.get("optional", False)),
            on_failure=str(raw.get("on_failure", "fail")),
            max_retries=int(raw.get("max_retries", 1)),
            fallback=raw.get("fallback"),
        )


# -------------------------------------------------------------------------- skill
@dataclass
class Skill:
    skill_id: str
    name: str
    version: int = 1
    description: str = ""
    purpose: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    required_plugins: List[str] = field(default_factory=list)
    required_devices: List[str] = field(default_factory=list)
    steps: List[SkillStep] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    verification: List[Dict[str, Any]] = field(default_factory=list)
    failure_paths: List[Dict[str, Any]] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    scope: str = SkillScope.USER.value
    scope_ref: str = ""
    tags: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_by: str = "owner"
    created_at: int = field(default_factory=lambda: int(time.time()))
    status: str = SkillStatus.CANDIDATE.value
    # statistics
    success_count: int = 0
    failure_count: int = 0
    verification_failures: int = 0
    user_takeovers: int = 0
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    last_used: int = 0
    last_verified: int = 0
    known_failures: List[Dict[str, Any]] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def key(self) -> str:
        """Stable identity across versions: `id@version`."""
        return f"{self.skill_id}@{self.version}"

    @property
    def runs(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        return round(self.success_count / self.runs, 3) if self.runs else 0.0

    @property
    def average_latency_ms(self) -> int:
        return int(self.total_latency_ms / self.runs) if self.runs else 0

    @property
    def average_cost_usd(self) -> float:
        return round(self.total_cost_usd / self.runs, 5) if self.runs else 0.0

    def can_transition(self, target: str) -> bool:
        try:
            return SkillStatus(target) in ALLOWED_TRANSITIONS[SkillStatus(self.status)]
        except (KeyError, ValueError):
            return False

    def all_variables(self, provided: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for name, spec in (self.inputs or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                merged[name] = spec["default"]
        merged.update(self.variables or {})
        merged.update(provided or {})
        return merged

    def missing_required_inputs(self, provided: Optional[Dict[str, Any]] = None) -> List[str]:
        provided = provided or {}
        missing = []
        for name, spec in (self.inputs or {}).items():
            if isinstance(spec, dict) and spec.get("required", False):
                if name not in provided and "default" not in spec:
                    missing.append(name)
        return missing

    def substitute(self, value: Any, variables: Dict[str, Any]) -> Any:
        """Replace `${var}` placeholders. Unresolved placeholders are left visible."""
        if isinstance(value, str):
            def _replace(match: "re.Match[str]") -> str:
                key = match.group(1)
                if key in variables:
                    return str(variables[key])
                return match.group(0)
            return _VAR_RE.sub(_replace, value)
        if isinstance(value, dict):
            return {k: self.substitute(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [self.substitute(v, variables) for v in value]
        return value

    def unresolved_placeholders(self, variables: Dict[str, Any]) -> List[str]:
        found: List[str] = []

        def _scan(value: Any) -> None:
            if isinstance(value, str):
                for match in _VAR_RE.finditer(value):
                    if match.group(1) not in variables:
                        found.append(match.group(1))
            elif isinstance(value, dict):
                for item in value.values():
                    _scan(item)
            elif isinstance(value, list):
                for item in value:
                    _scan(item)

        for step in self.steps:
            _scan(step.params)
            _scan(step.wait_for)
        _scan(self.verification)
        return sorted(set(found))

    # ------------------------------------------------------------------ (de)serialise
    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id, "name": self.name, "version": self.version,
            "description": self.description, "purpose": self.purpose,
            "inputs": self.inputs, "outputs": self.outputs,
            "preconditions": self.preconditions,
            "required_capabilities": self.required_capabilities,
            "required_plugins": self.required_plugins,
            "required_devices": self.required_devices,
            "steps": [s.to_dict() for s in self.steps],
            "variables": self.variables, "verification": self.verification,
            "failure_paths": self.failure_paths, "examples": self.examples,
            "scope": self.scope, "scope_ref": self.scope_ref, "tags": self.tags,
            "provenance": self.provenance, "created_by": self.created_by,
            "created_at": self.created_at, "status": self.status,
            "stats": {
                "success_count": self.success_count, "failure_count": self.failure_count,
                "verification_failures": self.verification_failures,
                "user_takeovers": self.user_takeovers, "runs": self.runs,
                "success_rate": self.success_rate,
                "average_latency_ms": self.average_latency_ms,
                "average_cost_usd": self.average_cost_usd,
                # A-053: the raw accumulators must be persisted too. Serialising only the
                # derived averages meant every save/load round trip reset total_latency_ms
                # and total_cost_usd to zero, silently destroying the statistics.
                "total_latency_ms": self.total_latency_ms,
                "total_cost_usd": self.total_cost_usd,
                "last_used": self.last_used, "last_verified": self.last_verified,
            },
            "known_failures": self.known_failures[-20:],
            "environment": self.environment,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Skill":
        stats = raw.get("stats") or {}
        return cls(
            skill_id=str(raw["skill_id"]), name=str(raw.get("name", raw["skill_id"])),
            version=int(raw.get("version", 1)),
            description=str(raw.get("description", "")),
            purpose=str(raw.get("purpose", "")),
            inputs=raw.get("inputs") or {}, outputs=raw.get("outputs") or {},
            preconditions=raw.get("preconditions") or [],
            required_capabilities=raw.get("required_capabilities") or [],
            required_plugins=raw.get("required_plugins") or [],
            required_devices=raw.get("required_devices") or [],
            steps=[SkillStep.from_dict(s) for s in (raw.get("steps") or [])],
            variables=raw.get("variables") or {},
            verification=raw.get("verification") or [],
            failure_paths=raw.get("failure_paths") or [],
            examples=raw.get("examples") or [],
            scope=str(raw.get("scope", SkillScope.USER.value)),
            scope_ref=str(raw.get("scope_ref", "")), tags=raw.get("tags") or [],
            provenance=raw.get("provenance") or {},
            created_by=str(raw.get("created_by", "owner")),
            created_at=int(raw.get("created_at", time.time())),
            status=str(raw.get("status", SkillStatus.CANDIDATE.value)),
            success_count=int(stats.get("success_count", raw.get("success_count", 0))),
            failure_count=int(stats.get("failure_count", raw.get("failure_count", 0))),
            verification_failures=int(stats.get("verification_failures", 0)),
            user_takeovers=int(stats.get("user_takeovers", 0)),
            total_latency_ms=int(stats.get("total_latency_ms", 0)),
            total_cost_usd=float(stats.get("total_cost_usd", 0.0)),
            last_used=int(stats.get("last_used", 0)),
            last_verified=int(stats.get("last_verified", 0)),
            known_failures=raw.get("known_failures") or [],
            environment=raw.get("environment") or {},
        )

    def clone_as_new_version(self, version: int) -> "Skill":
        data = self.to_dict()
        data["version"] = version
        data["status"] = SkillStatus.CANDIDATE.value
        data["created_at"] = int(time.time())
        clone = Skill.from_dict(data)
        clone.success_count = 0
        clone.failure_count = 0
        clone.verification_failures = 0
        clone.user_takeovers = 0
        clone.total_latency_ms = 0
        clone.total_cost_usd = 0.0
        clone.known_failures = []
        return clone


def validate_skill(skill: Skill) -> List[str]:
    """Structural validation. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    if not re.match(r"^[a-z][a-z0-9-]{2,63}$", skill.skill_id):
        problems.append("skill_id must be lowercase words separated by '-' (3-64 chars)")
    if not skill.name:
        problems.append("name is required")
    if not skill.steps:
        problems.append("at least one step is required")
    if not skill.verification:
        problems.append("verification is required: a skill cannot succeed without it")
    for index, step in enumerate(skill.steps):
        if not step.capability:
            problems.append(f"step {index + 1} has no capability")
        if step.on_failure not in ("fail", "skip", "retry", "fallback"):
            problems.append(f"step {index + 1} has an unknown on_failure policy")
    if skill.status not in {s.value for s in SkillStatus}:
        problems.append(f"unknown status {skill.status}")
    if skill.scope not in {s.value for s in SkillScope}:
        problems.append(f"unknown scope {skill.scope}")
    for check in skill.verification:
        if not isinstance(check, dict) or "check" not in check:
            problems.append("each verification entry needs a 'check' key")
    for check in skill.preconditions:
        if not isinstance(check, dict) or "check" not in check:
            problems.append("each precondition needs a 'check' key")
    return problems
