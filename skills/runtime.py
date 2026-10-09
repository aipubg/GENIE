"""Skill Runtime (skills/runtime.py).

Executes a skill as **orchestration, never as a security bypass**:

    step -> capability invocation -> PTE -> execute -> verify -> next step

Every step goes through the same capability worker that the rest of GENIE uses, so PTE,
mission tracking, computer/browser verification, plugin permissions, the injection guard and
the audit chain all apply unchanged. A saved skill does **not** inherit permissions: each run
is checked against the current policy.

Also handles: preconditions (adapt / declare incompatible instead of blind replay),
state-based waits (never fixed sleeps), cancellation, USER_TAKEOVER pausing, and statistics
that are only updated from a *verified* outcome.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.contracts import CallContext, EventType, TaskType
from core.events import get_bus
from core.logging_setup import get_logger
from skills.models import Skill, SkillStatus, SkillStep

log = get_logger("skills.runtime")

CAPABILITY_TASK_TYPE = {
    "browser.": TaskType.BROWSER_ACTION.value,
    "files.": TaskType.FILE_ACTION.value,
    "media.": TaskType.DEVICE_ACTION.value,
    "device.": TaskType.DEVICE_ACTION.value,
}


def task_type_for(capability: str) -> str:
    for prefix, task_type in CAPABILITY_TASK_TYPE.items():
        if capability.startswith(prefix):
            return task_type
    if capability.startswith("plugin."):
        return TaskType.COMPUTER_ACTION.value      # the worker routes plugins first anyway
    return TaskType.COMPUTER_ACTION.value


@dataclass
class StepResult:
    step_id: str
    capability: str
    ok: bool
    verified: bool = False
    detail: str = ""
    latency_ms: int = 0
    skipped: bool = False
    error_code: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"step_id": self.step_id, "capability": self.capability, "ok": self.ok,
                "verified": self.verified, "detail": self.detail,
                "latency_ms": self.latency_ms, "skipped": self.skipped,
                "error_code": self.error_code}


@dataclass
class SkillRunResult:
    skill_id: str
    version: int
    ok: bool = False
    verified: bool = False
    # A-055: this default used to be "failed". run() treats `status == "failed"` as "a step
    # failed" and then short-circuits verification — so a fresh result was already terminal
    # and EVERY skill run reported failure even when all steps succeeded. The initial value
    # must be a neutral in-progress state; run() sets the final verdict.
    status: str = "running"           # running | succeeded | failed | rejected | paused_by_takeover | cancelled
    detail: str = ""
    steps: List[StepResult] = field(default_factory=list)
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    verification: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    inputs: Dict[str, Any] = field(default_factory=dict)
    corrections: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"skill_id": self.skill_id, "version": self.version, "ok": self.ok,
                "verified": self.verified, "status": self.status, "detail": self.detail,
                "steps": [s.to_dict() for s in self.steps],
                "preconditions": self.preconditions, "verification": self.verification,
                "latency_ms": self.latency_ms, "inputs": self.inputs,
                "corrections": self.corrections}


class SkillRuntime:
    def __init__(self, registry, worker: Callable[[CallContext, Dict[str, Any]], Dict[str, Any]],
                 *, computer=None, audit=None, guard=None, locks=None,
                 capability_provider: Optional[Callable[[], List[str]]] = None,
                 plugin_provider: Optional[Callable[[], List[str]]] = None):
        self.registry = registry
        self.worker = worker
        self.computer = computer
        self.audit = audit
        self.guard = guard
        self.locks = locks
        self.capability_provider = capability_provider or (lambda: [])
        self.plugin_provider = plugin_provider or (lambda: [])
        self._bus = get_bus()

    # ------------------------------------------------------------ preconditions
    def check_preconditions(self, skill: Skill, ctx: CallContext,
                            inputs: Dict[str, Any]) -> Dict[str, Any]:
        variables = skill.all_variables(inputs)
        checks: List[Dict[str, Any]] = []
        adaptations: List[Dict[str, Any]] = []
        ok = True

        for check in skill.preconditions:
            kind = str(check.get("check", ""))
            expected = skill.substitute(check.get("value", check.get("path", "")), variables)
            spec = {k: skill.substitute(v, variables) for k, v in check.items()}
            result = self._run_check(kind, expected, spec, variables, ctx)
            checks.append({"check": kind, "target": expected, "ok": result["ok"],
                           "detail": result["detail"]})
            if not result["ok"]:
                if check.get("optional"):
                    continue
                if check.get("adapt") and result.get("adaptation"):
                    adaptations.append(result["adaptation"])
                    continue
                ok = False
                if not check.get("continue_on_failure"):
                    break

        return {"ok": ok, "checks": checks, "adaptations": adaptations,
                "incompatible": [c for c in checks if not c["ok"]]}

    def _run_check(self, kind: str, target: Any, spec: Dict[str, Any],
                   variables: Dict[str, Any], ctx: CallContext) -> Dict[str, Any]:
        try:
            if kind == "app_installed":
                from computer import apps
                entry = apps.resolve(str(target))
                return {"ok": entry is not None,
                        "detail": f"{target} installed" if entry else f"{target} not installed"}
            if kind == "file_exists":
                from computer import files
                exists = files.exists(str(target))
                return {"ok": exists, "detail": f"{target} {'exists' if exists else 'missing'}"}
            if kind == "dir_exists":
                from pathlib import Path
                is_dir = Path(str(target)).is_dir()
                return {"ok": is_dir, "detail": f"{target} {'is a directory' if is_dir else 'missing'}"}
            if kind == "dir_writable":
                from pathlib import Path
                path = Path(str(target))
                if not path.exists():
                    return {"ok": False, "detail": f"{target} does not exist",
                            "adaptation": {"action": "create_dir", "path": str(path)}}
                probe = path / ".genie-write-probe"
                try:
                    probe.write_text("x", encoding="utf-8")
                    probe.unlink()
                    return {"ok": True, "detail": f"{target} is writable"}
                except OSError as exc:
                    return {"ok": False, "detail": f"{target} is not writable: {exc}"}
            if kind == "capability_available":
                available = self.capability_provider()
                return {"ok": (not available) or str(target) in available,
                        "detail": f"capability {target}"}
            if kind == "plugin_available":
                available = self.plugin_provider()
                return {"ok": str(target) in available,
                        "detail": f"plugin {target} "
                                  f"{'available' if str(target) in available else 'missing'}"}
            if kind == "process_running":
                from computer import state
                running = state.process_running(str(target))
                return {"ok": running, "detail": f"process {target} running={running}"}
            if kind == "window_present":
                from computer import windows_api as win
                matches = win.find_windows(title_contains=str(target))
                return {"ok": bool(matches), "detail": f"{len(matches)} window(s) match {target}"}
            if kind == "uia_element_present":
                from computer import uia
                if not uia.available():
                    return {"ok": False, "detail": "UI Automation unavailable"}
                elements = uia.find_elements(name=str(spec.get("name", target)),
                                             control_type=str(spec.get("control_type", "")),
                                             window_title=str(spec.get("window", "")))
                return {"ok": bool(elements), "detail": f"{len(elements)} element(s)"}
            if kind == "device_connected":
                return {"ok": False, "detail": "device mesh is not available yet (Phase 6)",
                        "adaptation": None}
            return {"ok": False, "detail": f"unknown precondition check {kind!r}"}
        except Exception as exc:
            return {"ok": False, "detail": f"precondition check failed: {exc}"}

    # ------------------------------------------------------------------- verify
    def verify(self, skill: Skill, ctx: CallContext, inputs: Dict[str, Any],
               step_results: List[StepResult]) -> Dict[str, Any]:
        variables = skill.all_variables(inputs)
        checks: List[Dict[str, Any]] = []
        verified = True
        for check in skill.verification:
            kind = str(check.get("check", ""))
            target = skill.substitute(check.get("value", check.get("path", "")), variables)
            # A-062: substitute the WHOLE check, not just the target. `file_contains` compares
            # against `spec["text"]`, so leaving it raw made the verifier look for the literal
            # string "${text}" and every content check failed.
            spec = {k: skill.substitute(v, variables) for k, v in check.items()}
            result = self._verify_check(kind, target, spec, step_results)
            checks.append({"check": kind, "target": target, "ok": result["ok"],
                           "detail": result["detail"]})
            if not result["ok"]:
                verified = False
                if not check.get("optional"):
                    break
        return {"verified": verified, "checks": checks}

    def _verify_check(self, kind: str, target: Any, spec: Dict[str, Any],
                      step_results: List[StepResult]) -> Dict[str, Any]:
        try:
            if kind == "all_steps_verified":
                failed = [s for s in step_results if not s.verified and not s.skipped]
                return {"ok": not failed,
                        "detail": "all steps verified" if not failed
                                  else f"{len(failed)} step(s) unverified"}
            if kind == "file_exists":
                from computer import files
                exists = files.exists(str(target))
                return {"ok": exists, "detail": f"{target} {'exists' if exists else 'missing'}"}
            if kind == "file_size_min":
                from pathlib import Path
                minimum = int(spec.get("min_bytes", 1))
                path = Path(str(target))
                size = path.stat().st_size if path.exists() else -1
                return {"ok": size >= minimum,
                        "detail": f"{target} is {size} bytes (min {minimum})"}
            if kind == "file_contains":
                from pathlib import Path
                needle = str(spec.get("text", ""))
                path = Path(str(target))
                if not path.exists():
                    return {"ok": False, "detail": f"{target} missing"}
                content = path.read_text(encoding="utf-8", errors="ignore")
                return {"ok": needle in content,
                        "detail": f"{target} contains {needle!r}={needle in content}"}
            if kind == "dir_exists":
                from pathlib import Path
                is_dir = Path(str(target)).is_dir()
                return {"ok": is_dir, "detail": f"{target} is a directory={is_dir}"}
            if kind == "window_present":
                from computer import windows_api as win
                matches = win.find_windows(title_contains=str(target))
                return {"ok": bool(matches), "detail": f"{len(matches)} window(s) match"}
            if kind == "uia_value_contains":
                from computer import uia
                elements = uia.find_elements(name=str(spec.get("name", "")),
                                             window_title=str(spec.get("window", "")))
                for element in elements:
                    value = uia.get_value(element.element_id).get("value") or ""
                    if str(spec.get("text", "")) in value:
                        return {"ok": True, "detail": "UI Automation value matched"}
                return {"ok": False, "detail": "value not found through UI Automation"}
            if kind == "url_contains":
                from browser.service import get_browser
                current = str(get_browser()._current_url())
                return {"ok": str(target) in current, "detail": f"url={current}"}
            if kind == "text_contains":
                from browser.service import get_browser
                page = get_browser().handle("browser.extract", {"fields": ["text"]})
                text = str(((page.get("data") or {}).get("text") or ""))
                return {"ok": str(target).lower() in text.lower(),
                        "detail": f"{len(text)} chars on page"}
            if kind == "step_verified":
                step_id = str(spec.get("step_id", ""))
                match = next((s for s in step_results if s.step_id == step_id), None)
                return {"ok": bool(match and match.verified),
                        "detail": f"step {step_id} verified={bool(match and match.verified)}"}
            return {"ok": False, "detail": f"unknown verification check {kind!r}"}
        except Exception as exc:
            return {"ok": False, "detail": f"verification failed: {exc}"}

    # ---------------------------------------------------------------- run
    def run(self, skill: Skill, ctx: CallContext, inputs: Optional[Dict[str, Any]] = None,
            *, cancel_event=None, dry_run: bool = False,
            on_step: Optional[Callable[[StepResult], None]] = None) -> SkillRunResult:
        inputs = dict(inputs or {})
        started = time.time()
        result = SkillRunResult(skill_id=skill.skill_id, version=skill.version, inputs=inputs)
        ctx = ctx.with_(mission_id=ctx.mission_id or f"skill-{skill.skill_id}")

        missing = skill.missing_required_inputs(inputs)
        if missing:
            result.status = "rejected"
            result.detail = f"missing required inputs: {', '.join(missing)}"
            return result

        variables = skill.all_variables(inputs)
        unresolved = skill.unresolved_placeholders(variables)
        if unresolved:
            result.status = "rejected"
            result.detail = f"unresolved variables: {', '.join(unresolved)}"
            return result

        # 1. preconditions (adapt or declare incompatible — never blind replay)
        pre = self.check_preconditions(skill, ctx, inputs)
        result.preconditions = pre["checks"]
        if not pre["ok"]:
            result.status = "rejected"
            result.detail = ("preconditions not met: " +
                             ", ".join(f"{c['check']}({c['target']})" for c in pre["incompatible"]))
            self._audit(ctx, skill, "precondition_failed", result.detail)
            return result
        for adaptation in pre.get("adaptations", []):
            self._apply_adaptation(adaptation, ctx)

        # 2. steps
        for step in skill.steps:
            if cancel_event is not None and cancel_event.is_set():
                result.status = "cancelled"
                result.detail = "cancelled by user"
                break
            takeover = self._check_takeover(ctx, skill)
            if takeover:
                result.status = "paused_by_takeover"
                result.detail = "user took over — skill paused and correction evidence recorded"
                result.corrections.append(takeover)
                break

            step_result = self._run_step(skill, step, ctx, variables, dry_run=dry_run)
            result.steps.append(step_result)
            if on_step:
                on_step(step_result)
            if not step_result.ok and not step_result.skipped:
                result.status = "failed"
                result.detail = f"step {step.capability} failed: {step_result.detail}"
                break

        # 3. verification decides success
        if result.status == "failed" or result.status == "rejected":
            verification = {"verified": False, "checks": []}
        elif result.status in ("cancelled", "paused_by_takeover"):
            verification = {"verified": False, "checks": []}
        else:
            verification = self.verify(skill, ctx, inputs, result.steps)
        result.verification = verification["checks"]
        result.verified = bool(verification["verified"])
        result.ok = result.verified and result.status not in ("failed", "rejected",
                                                              "cancelled", "paused_by_takeover")
        if result.ok:
            result.status = "succeeded"
            result.detail = result.detail or "all steps verified"
        elif result.status not in ("cancelled", "paused_by_takeover", "rejected"):
            result.status = "failed"
            failed_checks = [c for c in verification["checks"] if not c["ok"]]
            result.detail = result.detail or (
                "verification failed: " + "; ".join(f"{c['check']}:{c['detail']}"
                                                    for c in failed_checks) or "unverified")

        result.latency_ms = int((time.time() - started) * 1000)
        self._record(skill, result)
        self._audit(ctx, skill, result.status, result.detail)
        self._bus.publish("SKILL_RUN", {"skill_id": skill.skill_id, "version": skill.version,
                                        "status": result.status, "verified": result.verified,
                                        "latency_ms": result.latency_ms})
        return result

    def _run_step(self, skill: Skill, step: SkillStep, ctx: CallContext,
                  variables: Dict[str, Any], *, dry_run: bool) -> StepResult:
        params = skill.substitute(step.params, variables)
        started = time.time()
        attempts = max(1, step.max_retries)
        last: Dict[str, Any] = {}
        for attempt in range(attempts):
            task = {"type": task_type_for(step.capability), "capability": step.capability,
                    "params": params, "target": params.get("target", "")}
            try:
                last = self.worker(ctx, task) or {}
            except Exception as exc:
                last = {"ok": False, "error": str(exc)}
            latency = int((time.time() - started) * 1000)
            if last.get("ok"):
                wait_ok, wait_detail = self._satisfy_wait(skill, step, variables)
                return StepResult(step_id=step.id, capability=step.capability, ok=True,
                                  verified=bool(last.get("verified", True)) and wait_ok,
                                  detail=(last.get("detail") or "") +
                                         ("" if wait_ok else f" | wait: {wait_detail}"),
                                  latency_ms=latency,
                                  error_code=str(last.get("error_code", "")), raw=last)
            if attempt + 1 < attempts:
                time.sleep(min(1.0, 0.2 * (attempt + 1)))
        if step.optional or step.on_failure == "skip":
            return StepResult(step_id=step.id, capability=step.capability, ok=False,
                              skipped=True, detail=str(last.get("detail") or last.get("error", "")),
                              latency_ms=int((time.time() - started) * 1000),
                              error_code=str(last.get("error_code", "")), raw=last)
        return StepResult(step_id=step.id, capability=step.capability, ok=False,
                          detail=str(last.get("detail") or last.get("error") or "step failed"),
                          latency_ms=int((time.time() - started) * 1000),
                          error_code=str(last.get("error_code", "")), raw=last)

    # ------------------------------------------------------------- state waits
    def _satisfy_wait(self, skill: Skill, step: SkillStep,
                      variables: Dict[str, Any]) -> tuple[bool, str]:
        """State-based wait after a step. Never a fixed sleep (§5.16)."""
        wait = step.wait_for
        if not wait:
            return True, ""
        condition = str(wait.get("condition", ""))
        timeout_s = float(wait.get("timeout_s", 10))
        target = skill.substitute(wait.get("value", wait.get("selector", "")), variables)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if condition == "file_exists":
                    from computer import files
                    if files.exists(str(target)):
                        return True, ""
                elif condition == "window_present":
                    from computer import windows_api as win
                    if win.find_windows(title_contains=str(target)):
                        return True, ""
                elif condition in ("element", "element_visible"):
                    from browser.service import get_browser
                    result = get_browser().handle("browser.wait", {
                        "condition": "element_visible", "selector": str(target),
                        "timeout_s": 1})
                    if result.get("ok"):
                        return True, ""
                elif condition == "url_contains":
                    from browser.service import get_browser
                    if str(target) in str(get_browser()._current_url()):
                        return True, ""
                elif condition == "process_running":
                    from computer import state
                    if state.process_running(str(target)):
                        return True, ""
                else:
                    return False, f"unknown wait condition {condition!r}"
            except Exception as exc:
                log.debug("wait check failed: %s", exc)
            time.sleep(0.2)
        return False, f"{condition} not satisfied within {timeout_s}s"

    # ------------------------------------------------------------------ helpers
    def _check_takeover(self, ctx: CallContext, skill: Skill) -> Optional[Dict[str, Any]]:
        if self.computer is None:
            return None
        try:
            takeover = self.computer.check_takeover()
        except Exception:
            return None
        if not takeover:
            return None
        self.registry.record_takeover(skill, detail=f"during skill {skill.key}")
        return {"kind": "user_takeover", "detail": takeover.get("reason", ""),
                "at": takeover.get("ts")}

    def _apply_adaptation(self, adaptation: Dict[str, Any], ctx: CallContext) -> None:
        action = adaptation.get("action")
        if action == "create_dir" and self.computer is not None:
            try:
                self.computer.execute(ctx, "files.mkdir", {"path": adaptation["path"]})
            except Exception as exc:
                log.debug("adaptation failed: %s", exc)

    def _record(self, skill: Skill, result: SkillRunResult) -> None:
        failure = None
        if result.status == "failed":
            failed_step = next((s for s in result.steps if not s.ok and not s.skipped), None)
            failure = {"kind": "step_failed",
                       "step": failed_step.capability if failed_step else "",
                       "detail": result.detail[:200],
                       "error_code": failed_step.error_code if failed_step else ""}
        elif result.status == "rejected":
            failure = {"kind": "precondition", "detail": result.detail[:200]}
        self.registry.record_run(skill, success=result.ok, verified=result.verified,
                                 latency_ms=result.latency_ms, failure=failure)

    def _audit(self, ctx: CallContext, skill: Skill, outcome: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who=ctx.person_id, device=ctx.device_id,
                              action=f"skill.run:{skill.key}", why=detail[:200],
                              mission_id=ctx.mission_id, result=outcome, trace_id=ctx.trace_id)
