"""Skill learning (skills/learning.py).

Two ways a skill can be born:

    successful mission  -> candidate detector -> generalizer -> sandbox validation -> active
    user demonstration  -> teaching session    -> generalizer -> sandbox validation -> active

Neither path activates anything without evidence: a candidate must pass validation before it
can run in normal operation (§5.9, §5.8).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import CallContext, DataClass, ModelRequirement
from core.logging_setup import get_logger
from skills.models import Skill, SkillScope, SkillStatus, SkillStep, validate_skill

log = get_logger("skills.learning")

MIN_STEPS_FOR_CANDIDATE = 3
# Parameters that are almost always task-specific rather than configuration
VARIABLE_HINTS = ("path", "file", "folder", "dir", "name", "title", "heading", "text",
                  "url", "query", "level", "amount", "count", "destination", "target",
                  "content", "message", "value")
CONFIG_HINTS = ("format", "mode", "quality", "enabled", "apply_transform", "encoding",
                "profile", "preset", "theme")


@dataclass
class CandidateDecision:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reasons": self.reasons, "blockers": self.blockers,
                "score": round(self.score, 3)}


class CandidateDetector:
    """Decides whether a mission trace deserves to become a skill candidate."""

    def should_create(self, trace: Dict[str, Any]) -> CandidateDecision:
        steps = trace.get("steps") or []
        reasons: List[str] = []
        blockers: List[str] = []
        score = 0.0

        if len(steps) < MIN_STEPS_FOR_CANDIDATE and not trace.get("user_requested"):
            blockers.append(f"only {len(steps)} step(s): too trivial to be a skill")
        else:
            score += 0.25
            reasons.append(f"{len(steps)} steps")

        if not trace.get("succeeded"):
            blockers.append("the mission did not succeed")
        else:
            score += 0.25
            reasons.append("mission succeeded")

        if not trace.get("verified", True):
            blockers.append("the mission result was not verified")
        else:
            score += 0.15
            reasons.append("result verified")

        if trace.get("user_requested"):
            score += 0.25
            reasons.append("user explicitly asked to remember this")

        if trace.get("recurring_hint"):
            score += 0.1
            reasons.append("looks recurring")

        generalizable = trace.get("generalizable")
        if generalizable is None:
            generalizable = self._looks_generalizable(steps)
        if not generalizable:
            blockers.append("steps are not generalizable (one-off values only)")
        else:
            score += 0.15
            reasons.append("steps are generalizable")

        # a candidate made only of read-only steps saves little
        if steps and all(str(s.get("capability", "")).endswith((".list", ".read", ".get"))
                         for s in steps):
            blockers.append("read-only workflow: little value in saving it")
            score -= 0.2

        ok = not blockers and score >= 0.5
        return CandidateDecision(ok=ok, reasons=reasons, blockers=blockers, score=score)

    @staticmethod
    def _looks_generalizable(steps: List[Dict[str, Any]]) -> bool:
        for step in steps:
            params = step.get("params") or {}
            for key, value in params.items():
                if key in CONFIG_HINTS:
                    continue
                if isinstance(value, str) and (len(value) > 3 or "\\" in value or "/" in value):
                    return True
                if isinstance(value, (int, float)) and key in VARIABLE_HINTS:
                    return True
        return False


# Keys whose value names *what* a step acts on. For these capabilities the target is part of
# the skill's identity ("write a note in Notepad"), not a per-run variable — parameterising it
# produced skills that demanded a `target` input and emitted `app_installed ${target}`
# preconditions that could never be evaluated (A-058).
LITERAL_TARGET_CAPABILITIES = ("application.", "window.", "processes.", "apps.")
LITERAL_TARGET_KEYS = ("target", "title")

_VAR_SCAN_RE = re.compile(r"\$\{([a-zA-Z0-9_.]+)\}")


# Capabilities that only establish a state. Retrying them is safe because running them twice
# has the same effect as running them once — unlike, say, `input.type_text`, which would type
# the text twice. Window focus in particular races with window animation after a move.
TRANSIENT_CAPABILITIES = {
    "window.focus": 5, "window.restore": 3, "window.maximize": 2, "window.minimize": 2,
    "window.move": 2, "clipboard.set": 2,
}


class Generalizer:
    """Turns a trace or demonstration into a reusable, parameterised skill.

    A large remote model is used when one is configured (workflow abstraction is exactly what
    NEDLE2 must not be asked to do). The deterministic path below always produces a valid
    skill, so learning never depends on a provider being available.
    """

    def __init__(self, gateway=None, director=None):
        self.gateway = gateway
        self.director = director

    # --------------------------------------------------------------- public API
    def from_trace(self, trace: Dict[str, Any], *, skill_id: str = "",
                   name: str = "") -> Skill:
        steps_raw = trace.get("steps") or []
        skill = Skill(
            skill_id=skill_id or self._slug(trace.get("goal") or name or "learned-skill"),
            name=name or (trace.get("goal") or "Learned skill")[:80],
            description=trace.get("goal", "")[:400],
            purpose=trace.get("goal", "")[:200],
            scope=str(trace.get("scope", SkillScope.USER.value)),
            scope_ref=str(trace.get("scope_ref", "")),
            tags=self._tags(trace),
            provenance={"source": trace.get("source", "mission"),
                        "mission_id": trace.get("mission_id", ""),
                        "generalizer": "deterministic"},
        )
        skill.steps = self._steps_from_trace(steps_raw)
        skill.inputs = self._inputs_from_steps(skill.steps, steps_raw)
        skill.required_capabilities = sorted({s.capability for s in skill.steps if s.capability})
        skill.required_plugins = sorted({c.split(".")[1] for c in skill.required_capabilities
                                         if c.startswith("plugin.") and len(c.split(".")) > 1})
        skill.preconditions = self._preconditions_from_steps(skill.steps)
        skill.verification = self._verification_from_steps(skill.steps)
        skill.examples = [{"goal": trace.get("goal", ""), "inputs": trace.get("inputs", {})}]
        skill.environment = trace.get("environment", {})
        skill.status = SkillStatus.CANDIDATE.value
        self._apply_retry_policy(skill)
        self._reconcile_inputs(skill)
        return skill

    def from_demonstration(self, session: Dict[str, Any], *, skill_id: str = "",
                           name: str = "") -> Skill:
        """A teaching demonstration is already semantic; this shapes it into a skill."""
        events = session.get("events") or []
        skill = Skill(
            skill_id=skill_id or self._slug(session.get("goal") or name or "taught-skill"),
            name=name or (session.get("goal") or "Taught skill")[:80],
            description=session.get("goal", "")[:400],
            purpose=session.get("goal", "")[:200],
            scope=str(session.get("scope", SkillScope.USER.value)),
            tags=self._tags(session),
            provenance={"source": "teaching", "session_id": session.get("session_id", ""),
                        "application": session.get("application", ""),
                        "generalizer": "deterministic"},
        )
        skill.steps = self._steps_from_events(events)
        skill.inputs = self._inputs_from_events(events)
        skill.required_capabilities = sorted({s.capability for s in skill.steps if s.capability})
        skill.required_plugins = sorted({c.split(".")[1] for c in skill.required_capabilities
                                         if c.startswith("plugin.") and len(c.split(".")) > 1})
        skill.preconditions = self._preconditions_from_events(session)
        skill.verification = self._verification_from_events(session, skill.steps)
        skill.examples = [{"demonstrated_by": session.get("demonstrated_by", "owner")}]
        skill.status = SkillStatus.CANDIDATE.value
        self._apply_retry_policy(skill)
        self._reconcile_inputs(skill)
        return skill

    def refine_with_model(self, skill: Skill, context: str = "") -> Dict[str, Any]:
        """Optional: ask a large model to improve abstraction. Never required."""
        if self.gateway is None:
            return {"ok": False, "error": "no model gateway available"}
        prompt = (
            "You are improving a GENIE automation skill definition. Reply with STRICT JSON only.\n"
            "Keep it environment independent: no coordinates, no absolute paths, no fixed waits.\n"
            f"Current skill JSON:\n{json.dumps(skill.to_dict(), ensure_ascii=False)[:6000]}\n"
            f"Context: {context[:500]}\n"
            "Return {\"skill\": {...same schema...}, \"changes\": [\"...\"]}"
        )
        try:
            completion = self.gateway.complete(
                CallContext(person_id="system"),
                ModelRequirement(capability="reasoning", data_class=DataClass.INTERNAL,
                                 max_input_tokens=8000),
                [{"role": "user", "content": prompt}], max_tokens=1500)
            text = completion.text
            start, end = text.find("{"), text.rfind("}")
            payload = json.loads(text[start:end + 1]) if start >= 0 else {}
            refined = payload.get("skill")
            if not refined:
                return {"ok": False, "error": "model did not return a skill"}
            candidate = Skill.from_dict(refined)
            problems = validate_skill(candidate)
            if problems:
                return {"ok": False, "error": "model output invalid", "problems": problems}
            candidate.provenance = {**skill.provenance, "refined_by": completion.provider_id,
                                    "refined_model": completion.model}
            return {"ok": True, "skill": candidate, "changes": payload.get("changes", []),
                    "provider": completion.provider_id}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------- trace -> steps
    def _apply_retry_policy(self, skill: Skill) -> None:
        """Give idempotent state-establishing steps a small retry budget."""
        for step in skill.steps:
            retries = TRANSIENT_CAPABILITIES.get(step.capability)
            if retries and step.max_retries < retries:
                step.max_retries = retries

    def _reconcile_inputs(self, skill: Skill) -> None:
        """Guarantee the invariant: every `${var}` used in a step is a declared input.

        Without this a generalised skill could reference `${content}` while declaring
        `content` as optional, so it would be rejected at run time with "unresolved
        variables" and could never execute (A-059).
        """
        used: set[str] = set()
        for step in skill.steps:
            for placeholder in _VAR_SCAN_RE.findall(json.dumps(step.params, default=str)):
                used.add(placeholder)
        for name in sorted(used):
            spec = skill.inputs.get(name)
            if spec is None:
                skill.inputs[name] = {"type": "string", "required": True,
                                      "description": f"{name} provided by the caller"}
            elif isinstance(spec, dict) and not spec.get("required") \
                    and spec.get("default") is None:
                # declared but unsatisfiable: either give it a default or require it
                spec["required"] = True

    def _steps_from_trace(self, steps_raw: List[Dict[str, Any]]) -> List[SkillStep]:
        steps: List[SkillStep] = []
        for raw in steps_raw:
            capability = str(raw.get("capability", ""))
            if not capability:
                continue
            params = dict(raw.get("params") or {})
            params.pop("target", None) if raw.get("target") in (None, "") else None
            if raw.get("target"):
                params.setdefault("target", raw["target"])
            params = {k: self._parameterise(k, v, capability) for k, v in params.items()}
            steps.append(SkillStep(capability=capability, params=params,
                                   description=str(raw.get("description", capability)),
                                   wait_for=raw.get("wait_for")))
        return steps

    def _parameterise(self, key: str, value: Any, capability: str = "") -> Any:
        """Replace task-specific values with variables; keep configuration literal."""
        if key in CONFIG_HINTS:
            return value
        if key in LITERAL_TARGET_KEYS and capability.startswith(LITERAL_TARGET_CAPABILITIES):
            return value
        if key in VARIABLE_HINTS:
            if isinstance(value, str):
                return f"${{{key}}}"
            if isinstance(value, (int, float)) and key in ("level", "amount", "count"):
                return f"${{{key}}}"
        if isinstance(value, str) and ("\\" in value or "/" in value) and len(value) > 6:
            return f"${{{key}}}"
        return value

    def _inputs_from_steps(self, steps: List[SkillStep],
                           raw_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Declare an input for every value that was actually turned into a variable.

        Deriving inputs from the parameter *names* was wrong: `target` is a variable hint, so
        an `application.open` step whose target stayed literal still demanded a `target` input,
        and every replay failed with "missing required inputs" (A-060). The authoritative
        source is the parameterised step itself; the raw trace only supplies the example.
        """
        examples: Dict[str, str] = {}
        for raw in raw_steps:
            for key, value in (raw.get("params") or {}).items():
                if isinstance(value, str) and value:
                    examples.setdefault(key, value[:80])
        inputs: Dict[str, Any] = {}
        for step in steps:
            for key, value in step.params.items():
                for name in _VAR_SCAN_RE.findall(str(value)):
                    if name in inputs:
                        continue
                    inputs[name] = {"type": "string", "required": True,
                                    "description": f"{name} provided by the caller",
                                    "example": examples.get(key, "")}
        return inputs

    def _preconditions_from_steps(self, steps: List[SkillStep]) -> List[Dict[str, Any]]:
        pre: List[Dict[str, Any]] = []
        for step in steps:
            if step.capability == "application.open" and step.params.get("target"):
                target = str(step.params["target"])
                # a precondition must be evaluable up front: `${target}` never is
                if "${" not in target:
                    pre.append({"check": "app_installed", "value": target})
                break
        for step in steps:
            if step.capability.startswith("plugin."):
                parts = step.capability.split(".")
                if len(parts) > 1:
                    pre.append({"check": "plugin_available", "value": parts[1]})
                    break
        return pre

    def _verification_from_steps(self, steps: List[SkillStep]) -> List[Dict[str, Any]]:
        """Infer success criteria from the observable effect of the workflow.

        A `file_exists` check alone would accept a zero-byte file, so when the workflow wrote
        known content we also assert that the content is really there. Verification must
        decide real success, not "the step was issued".
        """
        checks: List[Dict[str, Any]] = []
        for step in reversed(steps):
            capability = step.capability
            if capability in ("files.write", "files.append", "files.copy", "files.move"):
                target = step.params.get("dst") or step.params.get("path")
                if target:
                    checks.append({"check": "file_exists", "path": target})
                    content = step.params.get("text", step.params.get("content"))
                    if isinstance(content, str) and content:
                        checks.append({"check": "file_contains", "path": target,
                                       "text": content})
                    break
            if capability.startswith("browser.navigate"):
                url = step.params.get("url")
                if url:
                    checks.append({"check": "url_contains", "value": str(url)[:40]})
                    break
            if capability.startswith("plugin."):
                checks.append({"check": "all_steps_verified"})
                break
        if not checks:
            checks.append({"check": "all_steps_verified"})
        return checks

    # ------------------------------------------------------ demonstration -> steps
    def _steps_from_events(self, events: List[Dict[str, Any]]) -> List[SkillStep]:
        """Semantic events become capability steps — never raw coordinates."""
        steps: List[SkillStep] = []
        typed_text = ""
        for event in events:
            kind = str(event.get("kind", ""))
            if kind == "app_launch":
                steps.append(SkillStep(
                    capability="application.open",
                    params={"target": event.get("target", "")},
                    description=f"open {event.get('target', '')}",
                    wait_for={"condition": "window_present",
                              "value": event.get("window_hint", event.get("target", "")),
                              "timeout_s": 20}))
            elif kind == "uia_invoke":
                steps.append(SkillStep(
                    capability="uia.invoke",
                    params={"window": event.get("window", ""),
                            "name": event.get("element_name", ""),
                            "control_type": event.get("control_type", "")},
                    description=f"invoke {event.get('element_name', '')}",
                    wait_for=event.get("wait_for")))
            elif kind == "typing":
                typed_text = str(event.get("text", ""))
                steps.append(SkillStep(
                    capability="input.type_text",
                    params={"text": self._parameterise("text", typed_text)},
                    description="type the provided text"))
            elif kind == "file_save":
                content = str(event.get("content", ""))
                # What was typed and what was saved are usually the same value; reuse the
                # single `${text}` variable instead of inventing a duplicate `${content}`
                # input the caller would have to supply twice (A-061).
                content_param = "${text}" if (content and content == typed_text) \
                    else self._parameterise("content", content)
                steps.append(SkillStep(
                    capability="files.write",
                    params={"path": self._parameterise("path", event.get("path", "")),
                            "text": content_param},
                    description="save the file",
                    wait_for={"condition": "file_exists", "value": event.get("path", ""),
                              "timeout_s": 10}))
            elif kind == "browser_action":
                steps.append(SkillStep(
                    capability=event.get("capability", "browser.click"),
                    params=event.get("params", {}),
                    description=event.get("description", "browser action")))
            elif kind == "plugin_action":
                steps.append(SkillStep(capability=event.get("capability", ""),
                                       params=event.get("params", {}),
                                       description=event.get("description", "plugin action")))
            elif kind == "keystroke" and event.get("semantic"):
                steps.append(SkillStep(capability="input.hotkey",
                                       params={"chord": event.get("chord", "")},
                                       description=event.get("description", "shortcut")))
        return [s for s in steps if s.capability]

    def _inputs_from_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        inputs: Dict[str, Any] = {}
        for event in events:
            if event.get("kind") == "typing" and event.get("text"):
                inputs.setdefault("text", {"type": "string", "required": True,
                                           "description": "text to type and save",
                                           "example": event["text"][:60]})
            if event.get("kind") == "file_save":
                inputs.setdefault("path", {"type": "string", "required": True,
                                           "description": "destination file path"})
        return inputs

    def _preconditions_from_events(self, session: Dict[str, Any]) -> List[Dict[str, Any]]:
        pre: List[Dict[str, Any]] = []
        application = str(session.get("application", ""))
        if application and "${" not in application:
            pre.append({"check": "app_installed", "value": application})
        return pre

    def _verification_from_events(self, session: Dict[str, Any],
                                  steps: List[SkillStep]) -> List[Dict[str, Any]]:
        checks = self._verification_from_steps(steps)
        after = session.get("after_state") or {}
        if after.get("file_path"):
            checks = [{"check": "file_exists", "path": "${path}"}]
            if after.get("content_hint"):
                checks.append({"check": "file_contains", "path": "${path}",
                               "text": after["content_hint"]})
        return checks or [{"check": "all_steps_verified"}]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _slug(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (text or "learned-skill").lower()).strip("-")
        return (slug or "learned-skill")[:64]

    @staticmethod
    def _tags(source: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        application = source.get("application") or ""
        if application:
            tags.append(application.lower())
        for word in re.split(r"[^a-z0-9]+", (source.get("goal") or "").lower()):
            if len(word) > 3 and word not in tags:
                tags.append(word)
        return tags[:10]


class SandboxValidator:
    """Validates a candidate before it may become active (§5.9)."""

    def __init__(self, runtime, registry):
        self.runtime = runtime
        self.registry = registry

    def validate(self, skill: Skill, ctx: CallContext, inputs: Dict[str, Any],
                 *, execute: bool = True) -> Dict[str, Any]:
        problems = validate_skill(skill)
        if problems:
            return {"passed": False, "stage": "structure", "problems": problems}

        # stage 1: dry run — proves the plan is structurally executable without side effects
        dry = self.runtime.run(skill, ctx.with_(dry_run=True), inputs)
        dry_ok = not [s for s in dry.steps if not s.ok and not s.skipped]
        if not dry_ok:
            return {"passed": False, "stage": "dry_run",
                    "detail": dry.detail, "steps": [s.to_dict() for s in dry.steps]}

        # stage 2: real run — the only thing that can prove a skill works
        if not execute:
            return {"passed": True, "stage": "dry_run",
                    "detail": "structurally valid; execution not requested"}
        real = self.runtime.run(skill, ctx, inputs)
        return {"passed": bool(real.verified), "stage": "execution",
                "detail": real.detail, "verified": real.verified,
                "steps": [s.to_dict() for s in real.steps],
                "verification": real.verification, "latency_ms": real.latency_ms}
