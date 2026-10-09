"""Execution loop: PLAN -> ACT -> OBSERVE -> VERIFY -> RECOVER (computer/executor).

This module is the reason a mission can never be marked COMPLETED because GENIE "generated
the right call". Every action goes through:

    PLAN     choose ordered strategies (native API -> OS -> UIA -> DOM -> vision -> raw input)
    ACT      run the strategy
    OBSERVE  take a fresh machine-state snapshot
    VERIFY   compare the resulting state with the expected effect
    RECOVER  on mismatch: record evidence, back off, try the next strategy (or fail honestly)

Plus: exclusive desktop lock, USER_TAKEOVER handling, cancellation, audit and trace.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.contracts import CallContext, EventType
from core.events import get_bus
from core.logging_setup import get_logger

from . import (
    apps, audio, files, input as input_mod, locks, planner, state, uia, verifier,
    windows_api as win, workspace,
)

log = get_logger("computer.executor")

DEFAULT_SETTLE_S = 0.35
APP_WAIT_S = 20.0


def user_search_roots() -> List[str]:
    """Where a user expects GENIE to look for their files (read-only search)."""
    locations = files.user_locations()
    return [locations[name] for name in ("downloads", "documents", "desktop")]


@dataclass
class ExecutionResult:
    ok: bool
    capability: str
    verified: bool = False
    detail: str = ""
    attempts: int = 0
    strategies_tried: List[str] = field(default_factory=list)
    strategy_used: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    verify_ms: int = 0
    takeover: bool = False
    cancelled: bool = False
    dry_run: bool = False
    error: str = ""
    # Structured failure code (e.g. TARGET_NOT_EXPOSED_BY_UIA) so callers and the
    # planner can branch on a machine-readable reason instead of parsing prose.
    error_code: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "capability": self.capability, "verified": self.verified,
            "detail": self.detail, "attempts": self.attempts,
            "strategies_tried": self.strategies_tried, "strategy_used": self.strategy_used,
            "error_code": self.error_code,
            "result": self.result, "verification": self.verification,
            "latency_ms": self.latency_ms, "verify_ms": self.verify_ms,
            "takeover": self.takeover, "cancelled": self.cancelled,
            "dry_run": self.dry_run, "error": self.error,
        }


class Executor:
    def __init__(self, db=None, audit=None, trust=None, desktop_lock: Optional[locks.DesktopLock] = None,
                 workspace_root: Optional[str] = None,
                 desktop_awareness=None):
        self.db = db
        self.audit = audit
        self.trust = trust
        self.desktop_lock = desktop_lock
        self.desktop_awareness = desktop_awareness
        self.workspace = workspace.get_workspace(workspace_root)
        self._bus = get_bus()
        self._launch_cache = apps.LaunchCache(db) if db is not None else None

    # ------------------------------------------------------------------ public
    def execute(self, ctx: CallContext, capability: str,
                params: Dict[str, Any] | None = None,
                cancel_event: Optional[threading.Event] = None) -> ExecutionResult:
        params = dict(params or {})
        if capability in ("message.prepare", "message.send", "desktop.visual_observe",
                          "desktop.visual_click", "desktop.visual_action"):
            params["_cancel_event"] = cancel_event or threading.Event()
        started = time.time()
        plan = planner.plan(capability, params, dry_run=ctx.dry_run)
        if not plan.get("verified", True):
            return ExecutionResult(False, capability, detail=plan.get("reason", "unplannable"),
                                   result={"error_code": plan.get("error_code", "unplannable"),
                                           "candidates": plan.get("candidates", [])},
                                   error=plan.get("reason", "unplannable"))

        strategies = plan.get("strategies") or []
        if not strategies:
            return ExecutionResult(False, capability, detail="no available strategy",
                                   error="no available strategy")

        # ---- desktop lock (only for capabilities that drive the machine)
        lock_holder = ctx.mission_id or ctx.trace_id
        lock_taken = False
        if plan.get("needs_desktop_lock") and self.desktop_lock is not None:
            got = self.desktop_lock.acquire(lock_holder)
            if not got.get("ok"):
                return ExecutionResult(False, capability, detail=got.get("error", "lock denied"),
                                       error=got.get("error", "lock denied"))
            lock_taken = True

        needs_full = bool(plan.get("needs_full_state"))
        try:
            before = state.snapshot(include_processes=needs_full).to_dict()
            result = ExecutionResult(False, capability)
            for strategy in strategies:
                if cancel_event is not None and cancel_event.is_set():
                    result.cancelled = True
                    result.detail = "cancelled by user"
                    break

                # USER_TAKEOVER check before touching anything
                if self.desktop_lock is not None:
                    takeover = self.desktop_lock.check_takeover()
                    if takeover:
                        result.takeover = True
                        result.detail = "user took over the desktop — automation paused"
                        self._event(EventType.USER_TAKEOVER, takeover, ctx)
                        break

                result.attempts += 1
                result.strategies_tried.append(strategy["name"])
                act_started = time.time()
                try:
                    outcome = self._act(ctx, capability, strategy["name"], params, plan)
                except Exception as exc:
                    log.error("strategy %s failed for %s: %s", strategy["name"], capability, exc)
                    outcome = {"ok": False, "error": str(exc)}
                    if getattr(exc, "action_may_have_run", False):
                        outcome.update(error_code="action_outcome_unknown", action_may_have_run=True)
                act_ms = int((time.time() - act_started) * 1000)

                if not outcome.get("ok"):
                    result.result = outcome
                    # keep the *specific* reason (refusal, missing app, timeout) — never a
                    # generic "action failed" that hides why GENIE did not act
                    reason = (outcome.get("reason") or outcome.get("error")
                              or outcome.get("detail") or "action failed")
                    result.error = str(reason)
                    result.detail = str(reason)
                    result.error_code = str(outcome.get("error_code") or "")
                    if outcome.get("action_may_have_run"):
                        break
                    continue

                # ---- dry-run: nothing was executed, so nothing can be verified
                if ctx.dry_run:
                    result.result = outcome
                    result.strategy_used = strategy["name"]
                    result.ok = True
                    result.verified = False
                    result.dry_run = True
                    result.detail = f"dry-run: {capability} planned via {strategy['name']} (not executed, not verified)"
                    result.verification = {"verified": False, "dry_run": True,
                                           "detail": "dry-run mode — no state was changed"}
                    result.latency_ms = int((time.time() - started) * 1000)
                    self._audit(ctx, capability, "dry_run", result.detail)
                    return result

                # ---- OBSERVE + VERIFY (timed as one phase: observe is part of verifying)
                observe_started = time.time()
                self._settle(capability, outcome)
                after = state.snapshot(include_processes=needs_full).to_dict()
                outcome_verify = verifier.verify(capability, params, outcome, before, after)
                result.verify_ms += int((time.time() - observe_started) * 1000)

                result.result = outcome
                result.strategy_used = strategy["name"]
                result.verification = outcome_verify.to_dict()
                result.verified = outcome_verify.verified
                result.detail = outcome_verify.detail
                result.latency_ms = int((time.time() - started) * 1000)

                self._record_verification(ctx, capability, strategy["name"], outcome_verify,
                                          result.attempts, act_ms)

                if outcome_verify.verified:
                    result.ok = True
                    result.error = ""
                    result.error_code = ""
                    self._audit(ctx, capability, "ok", outcome_verify.detail)
                    return result

                if capability == "input.type_text":
                    # Unicode input may have reached the editor despite missing
                    # readback. A second paste could duplicate the owner's text.
                    result.error_code = "action_outcome_unknown"
                    result.result["action_may_have_run"] = True
                    result.error = "Text input was issued but readback did not verify it. Reobserve before typing again."
                    result.detail = result.error
                    break

                # ---- RECOVER: log the mismatch and fall through to the next strategy
                log.warning("verification failed for %s via %s: %s", capability,
                            strategy["name"], outcome_verify.detail)
                self._audit(ctx, capability, "verify_failed",
                            f"{strategy['name']}: {outcome_verify.detail}")
                before = after          # the world changed; re-baseline
                time.sleep(min(1.5, 0.2 * result.attempts))

            if not result.detail:
                result.detail = result.error or "all strategies exhausted without verification"
            result.error = result.error or result.detail
            # ---- B06: UIA/DOM gap -> automatic bounded grounded-visual escalation
            escalated = self._maybe_escalate_to_visual(ctx, capability, params, result,
                                                       cancel_event)
            if escalated is not None:
                self._audit(ctx, capability, "escalated_visual", escalated.detail)
                return escalated
            self._audit(ctx, capability, "failed", result.detail)
            return result
        finally:
            if lock_taken and self.desktop_lock is not None:
                self.desktop_lock.release(lock_holder)

    # ------------------------------------------------------- B06 escalation
    # Interaction capabilities that depend on accessibility being exposed.
    _UIA_INTERACTION = frozenset({
        "uia.find", "uia.invoke", "uia.set_value", "uia.focus", "uia.select",
        "uia.set_toggle", "uia.click", "uia.get_value", "uia.state",
    })

    def _maybe_escalate_to_visual(self, ctx, capability, params, result, cancel_event):
        """When an accessibility target is not exposed, escalate ONCE to the
        authorized grounded visual fallback so the caller receives real visual
        targets instead of a bare failure.

        Bounded by construction: at most one escalation per execution, only for
        UIA interaction capabilities, only when Desktop Awareness + remote visual
        consent are available. It never loops and never fabricates an action.
        """
        if capability not in self._UIA_INTERACTION:
            return None
        if self.visual is None or self.desktop_awareness is None:
            return None
        if not self.desktop_awareness.settings.remote_visual_consent:
            return None
        if result.cancelled or result.takeover or (cancel_event is not None and cancel_event.is_set()):
            return None
        if result.error_code in {"access_denied", "permission_denied", "action_outcome_unknown"}:
            return None

        hwnd = params.get("window_id") or params.get("hwnd")
        if not isinstance(hwnd, int) or hwnd <= 0:
            observed = uia.describe_registered(str(params.get("element_id") or ""))
            hwnd = observed.get("window_id")
        if not hwnd:
            return None

        cancel = cancel_event or threading.Event()
        try:
            observed = self.visual.observe(ctx, {"window_id": int(hwnd)}, cancel)
        except Exception as exc:  # escalation must never mask the original failure
            log.debug("visual escalation failed: %s", exc)
            return None
        if not observed.get("ok"):
            # the visual route is unavailable too: report honestly, do not loop
            result.error_code = str(observed.get("error_code") or "TARGET_NOT_EXPOSED_BY_UIA")
            result.result = dict(result.result or {})
            result.result["escalation"] = {
                "from": capability, "to": "desktop.visual_observe",
                "attempted": True, "available": False,
                "reason": str(observed.get("error") or "visual fallback unavailable"),
            }
            result.detail = (str(result.detail) +
                             " | Accessibility did not expose the target and the visual "
                             "fallback is unavailable.")
            return result

        escalated = ExecutionResult(False, capability)
        escalated.strategy_used = result.strategy_used
        escalated.attempts = result.attempts
        escalated.strategies_tried = list(result.strategies_tried)
        escalated.error_code = "TARGET_NOT_EXPOSED_BY_UIA"
        escalated.detail = ("The accessibility tree did not expose this target. GENIE "
                            "automatically obtained a grounded visual observation instead.")
        escalated.error = escalated.detail
        escalated.result = {
            "escalation": {
                "from": capability, "to": "desktop.visual_observe",
                "attempted": True, "available": True,
                "next_tool": "desktop_visual_action",
            },
            "visual": observed,
        }
        return escalated

    # -------------------------------------------------------------------- ACT
    def _act(self, ctx: CallContext, capability: str, strategy: str,
             params: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
        if ctx.dry_run:
            return self._dry_run(capability, strategy, params, plan)
        return self._dispatch(ctx, capability, strategy, params, plan)

    def _dry_run(self, capability: str, strategy: str, params: Dict[str, Any],
                 plan: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "dry_run": True, "strategy": strategy, "capability": capability,
                "detail": f"dry-run {capability} via {strategy}",
                "expected_process": (plan.get("resolved_app") or {}).get("exe", "")}

    def _dispatch(self, ctx: CallContext, capability: str, strategy: str,
                  params: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
        w = self.workspace

        if capability == "message.prepare":
            return self.messages.prepare(ctx, params)
        if capability == "message.send":
            return self.messages.send(ctx, params, params["_cancel_event"])
        if capability == "desktop.visual_observe":
            return self.visual.observe(ctx, params, params["_cancel_event"])
        if capability == "desktop.visual_click":
            return self.visual.click(ctx, params, params["_cancel_event"])
        if capability == "desktop.visual_action":
            return self.visual.action(ctx, params, params["_cancel_event"])

        # ------------------------------------------------------------ apps
        if capability == "application.open":
            return self._open_app(ctx, strategy, params, plan)
        if capability == "application.close":
            return self._close_app(strategy, params)
        if capability == "application.resolve":
            entry = apps.resolve(str(params.get("target", "")))
            matches = [] if entry else apps.resolve_candidates(str(params.get("target", "")))
            return {"ok": entry is not None, "entry": entry.to_dict() if entry else None,
                    "candidates": [e.to_dict() for e in matches],
                    "error_code": "" if entry else "ambiguous_application" if matches else "application_not_discovered"}
        if capability == "application.list":
            entries = apps.discover(force=bool(params.get("refresh")))
            return {"ok": True, "count": len(entries),
                    "apps": [e.to_dict() for e in entries[:int(params.get("limit", 200))]]}
        if capability == "apps.discover":
            return {"ok": True, "count": len(apps.discover(force=True))}

        # ---------------------------------------------------------- windows
        if capability == "window.list":
            wins = win.list_windows()
            return {"ok": True, "count": len(wins), "windows": [x.to_dict() for x in wins]}
        if capability == "window.minimize_all":
            return win.minimize_all_application_windows()
        if capability.startswith("window."):
            return self._window_action(capability, params)

        # -------------------------------------------------------- processes
        if capability == "processes.list":
            procs = state.list_processes()
            return {"ok": True, "count": len(procs), "processes": procs[:300]}
        if capability == "process.kill":
            from .shell import run as shell_run
            pid = int(params.get("pid", 0))
            name = params.get("name", "")
            if pid:
                res = shell_run(f"taskkill /PID {pid} /F", shell="cmd", tier="elevated", timeout_s=15)
            else:
                res = shell_run(f"taskkill /IM {name} /F", shell="cmd", tier="elevated", timeout_s=15)
            return {"ok": res.ok, "detail": res.stdout or res.stderr, "pid": pid, "name": name}

        # ------------------------------------------------------------- audio
        if capability == "system.volume.set":
            out = audio.set_volume(int(params.get("level", 50)))
            out["expected"] = int(params.get("level", 50))
            return out
        if capability == "system.volume.up":
            return audio.volume_up()
        if capability == "system.volume.down":
            return audio.volume_down()
        if capability == "system.volume.mute":
            muted = params.get("muted")
            return audio.set_mute(None if muted is None else bool(muted))
        if capability == "system.audio.state":
            return {"ok": True, **audio.get_state()}
        if capability in ("system.audio.devices", "system.network.state", "system.settings.open"):
            from . import system_tools
            if capability == "system.settings.open":
                return system_tools.open_settings(params.get("page", ""))
            return system_tools.audio_devices() if capability == "system.audio.devices" else system_tools.network_state()

        # ------------------------------------------------------------- files
        if capability.startswith("files."):
            return self._file_action(capability, params, w)

        # --------------------------------------------------------- clipboard
        if capability == "clipboard.get":
            text = win.clipboard_get_text()
            return {"ok": True, "text": text, "length": len(text)}
        if capability == "clipboard.set":
            ok = win.clipboard_set_text(str(params.get("text", "")))
            return {"ok": ok, "length": len(str(params.get("text", "")))}

        # ------------------------------------------------------------- shell
        if capability in ("shell.run", "shell.powershell_json"):
            from . import shell as shell_mod
            command = str(params.get("command", ""))
            tier = str(params.get("tier", "safe"))
            sh = "cmd" if strategy == "cmd" else "powershell"
            if capability == "shell.powershell_json":
                out = shell_mod.run_powershell_json(command, timeout_s=int(params.get("timeout_s", 30)),
                                                    tier=tier)
                return {"ok": out.get("ok", False), "data": out.get("data"),
                        "exit_code": 0 if out.get("ok") else 1,
                        "error": out.get("error", "")}
            res = shell_mod.run(command, shell=sh, timeout_s=int(params.get("timeout_s", 30)),
                                tier=tier, cwd=params.get("cwd"))
            return res.to_dict()

        # --------------------------------------------------------------- uia
        if capability == "uia.windows":
            return {"ok": True, "windows": uia.windows()}
        if capability == "uia.find":
            els = uia.find_elements(name=str(params.get("name", "")),
                                    control_type=str(params.get("control_type", "")),
                                    automation_id=str(params.get("automation_id", "")),
                                    window_title=str(params.get("window", "")),
                                    window_id=int(params.get("window_id", 0)),
                                    limit=min(100, int(params.get("limit", 25))),
                                    depth=min(32, int(params.get("depth", 4))))
            error = uia.last_error()
            return {"ok": bool(els), "count": len(els), "elements": [e.to_dict() for e in els],
                    "error": "" if els else error.get("error", "no matching element"),
                    "error_code": "" if els else error.get("error_code", "element_not_found"),
                    "candidates": error.get("candidates", []),
                    "scan_warning": error if els else {}}
        if capability == "uia.invoke":
            out = uia.invoke(str(params.get("element_id", "")))
            out["element_id"] = params.get("element_id")
            return out
        if capability == "uia.click":
            out = uia.click_element(str(params.get("element_id", "")))
            out["element_id"] = params.get("element_id")
            return out
        if capability == "uia.set_value":
            out = uia.set_value(str(params.get("element_id", "")), str(params.get("value", "")))
            out["element_id"] = params.get("element_id")
            return out
        if capability == "uia.focus":
            out = uia.focus_element(str(params.get("element_id", "")))
            out["element_id"] = params.get("element_id")
            return out
        if capability == "uia.get_value":
            return uia.get_value(str(params.get("element_id", "")))
        if capability == "uia.state":
            return uia.control_state(str(params.get("element_id", "")))
        if capability == "uia.set_toggle":
            return uia.set_toggle(str(params.get("element_id", "")), params.get("enabled"))
        if capability == "uia.select":
            return uia.select_element(str(params.get("element_id", "")))
        if capability == "uia.tree":
            return uia.dump_tree(str(params.get("window", "")), depth=int(params.get("depth", 3)))

        # ------------------------------------------------------------- input
        if capability == "input.type_text":
            hwnd = int(params.get("window_id") or 0)
            expected_title = str(params.get("verify_in_window") or "")
            target = next((item for item in win.list_windows() if item.hwnd == hwnd), None) if hwnd else None
            if (target is None or not expected_title or target.title != expected_title or
                    (params.get("target_pid") and target.pid != int(params["target_pid"]))):
                return {"ok": False, "error_code": "target_not_grounded",
                        "error": "target window identity changed before typing"}
            if not win.focus_window(hwnd):
                return {"ok": False, "error_code": "target_focus_failed",
                        "error": "could not focus the observed target window"}
            foreground = win.foreground_window()
            if foreground is None or foreground.hwnd != hwnd:
                return {"ok": False, "error_code": "target_focus_failed",
                        "error": "target window is not foreground; no text was sent"}
            editor = uia.focused_editor(hwnd, target.title)
            if not editor.get("ok"):
                return editor
            target_identity = {"hwnd": hwnd, "pid": target.pid,
                               "process": target.process, "title": target.title,
                               "typed_element_id": editor["element_id"]}
            if strategy == "clipboard-paste":
                text = str(params.get("text", ""))
                if not win.clipboard_set_text(text):
                    return {"ok": False, "error": "clipboard write failed", **target_identity}
                outcome = input_mod.hotkey("ctrl", "v")
            else:
                outcome = input_mod.type_text(str(params.get("text", "")))
            if not (outcome or {}).get("ok"):
                outcome = {**(outcome or {}), "ok": False, "error_code": "action_outcome_unknown",
                           "action_may_have_run": True,
                           "error": "Text delivery was incomplete or uncertain. Inspect the editor before retrying."}
            return {**(outcome or {}), **target_identity}
        if capability == "input.key":
            if not self._focus_input_target(params):
                return {"ok": False, "error_code": "target_focus_failed", "error": "Bound input target changed; no key sent."}
            return input_mod.press_key(str(params.get("key", "enter")),
                                       int(params.get("times", 1)))
        if capability == "input.hotkey":
            if not self._focus_input_target(params):
                return {"ok": False, "error_code": "target_focus_failed", "error": "Bound input target changed; no shortcut sent."}
            return input_mod.hotkey(*str(params.get("chord", "")).split("+"))
        if capability == "input.click":
            return input_mod.click(params.get("x"), params.get("y"),
                                   str(params.get("button", "left")),
                                   bool(params.get("double", False)))
        if capability == "input.move":
            return input_mod.move(int(params.get("x", 0)), int(params.get("y", 0)))
        if capability == "input.scroll":
            if not self._focus_input_target(params):
                return {"ok": False, "error_code": "target_focus_failed", "error": "Bound scroll target changed; no wheel event sent."}
            target = next((w for w in win.list_windows() if w.hwnd == params.get("window_id")), None)
            if target is None or target.rect[2] <= target.rect[0] or target.rect[3] <= target.rect[1]:
                return {"ok": False, "error_code": "target_not_grounded", "error": "Scroll target geometry unavailable."}
            moved = input_mod.move((target.rect[0] + target.rect[2]) // 2, (target.rect[1] + target.rect[3]) // 2)
            if not moved.get("ok"):
                return moved
            return input_mod.scroll(int(params.get("delta", -120)))
        if capability == "input.state":
            return {"ok": True, **input_mod.state()}

        # ------------------------------------------------------------ screen
        if capability in ("screen.capture", "screen.capture_window"):
            out_dir = w.path("artifacts")
            name = f"capture_{int(time.time())}.bmp"
            if capability == "screen.capture_window":
                hwnd = int(params.get("hwnd") or (win.foreground_window() or win.WindowInfo(0, "", "", 0)).hwnd)
                out = win.capture_window(hwnd, out_dir / name)
            else:
                out = win.capture_screen(out_dir / name)
            return out
        if capability == "computer.state":
            return {"ok": True, **state.snapshot().to_dict()}
        if capability == "screen.state":
            fg = win.foreground_window()
            return {"ok": True, "foreground": fg.to_dict() if fg else None,
                    "monitors": [m.to_dict() for m in win.list_monitors()]}

        # --------------------------------------------------------- workspace
        if capability == "workspace.list":
            return {"ok": True, "entries": w.list(str(params.get("subdir", ""))),
                    **w.usage()}
        if capability == "workspace.usage":
            return {"ok": True, **w.usage()}
        if capability == "workspace.info":
            return {"ok": True, "root": str(w.root),
                    "isolation": workspace.detect_isolation(),
                    "resources": workspace.resource_profile()}

        # ---- GENIE Workspace / background computer (§11C) ----
        if capability == "workspace.identity":
            return {"ok": True, **w.ensure_identity()}
        if capability == "workspace.mission_claim":
            return w.claim_mission(str(params.get("mission_id", "")),
                                   agent_id=str(params.get("agent_id", "")),
                                   purpose=str(params.get("purpose", "")))
        if capability == "workspace.mission_release":
            return w.release_mission(str(params.get("mission_id", "")),
                                     purge=bool(params.get("purge", False)))
        if capability == "workspace.missions":
            return {"ok": True, "missions": w.active_missions()}
        if capability == "workspace.quota":
            return {"ok": True, **w.quota()}
        if capability == "workspace.set_quota":
            raw = params.get("cap_bytes")
            return {"ok": True, **w.set_quota(int(raw) if raw is not None else None)}
        if capability == "workspace.cleanup":
            return w.cleanup(max_age_days=float(params.get("max_age_days", 7.0)),
                             mission_id=str(params.get("mission_id", "")),
                             dry_run=bool(params.get("dry_run", True)))
        if capability == "workspace.session_dir":
            d = w.session_dir(str(params.get("session", "default")))
            return {"ok": True, "path": str(d)}
        if capability == "workspace.shell":
            return w.shell(str(params.get("command", "")), cwd=str(params.get("cwd", "")),
                           timeout=int(params.get("timeout", 60)),
                           mission_id=str(params.get("mission_id", "")))

        # ------------------------------------------------------------ locks
        if capability == "desktop.lock_state":
            return {"ok": True, **(self.desktop_lock.state() if self.desktop_lock else {})}
        if capability == "desktop.lock_resume":
            if self.desktop_lock:
                self.desktop_lock.resume()
            return {"ok": True}

        # -------------------------------------------------- desktop awareness
        if capability == "desktop.observe":
            obs = self.desktop_awareness.observe() if self.desktop_awareness else None
            return {"ok": True, **(obs.to_dict() if obs else {})}
        if capability == "desktop.observe_display":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            return self.desktop_awareness.observe_display(int(params.get("index", 0)))
        if capability == "desktop.capture_display":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            return self.desktop_awareness.capture_display(
                int(params.get("index", 0)), params.get("out_path"))
        if capability == "desktop.list_displays":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            return self.desktop_awareness.list_displays()
        if capability == "desktop.pause":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            return self.desktop_awareness.pause()
        if capability == "desktop.resume":
            # Existing desktop_lock resume + awareness resume
            if self.desktop_lock:
                self.desktop_lock.resume()
            if self.desktop_awareness:
                self.desktop_awareness.resume()
            return {"ok": True}
        if capability == "desktop.settings.get":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            return {"ok": True, **self.desktop_awareness.settings.to_dict()}
        if capability == "desktop.settings.set":
            if not self.desktop_awareness:
                return {"ok": False, "error": "desktop awareness not available"}
            changes = params.get("settings", {})
            return self.desktop_awareness.configure(changes)

        # ------------------------------------------------- app interaction context
        if capability == "app.context.status":
            registry = getattr(self, "app_contexts", None)
            return {"ok": True, **(registry.status() if registry is not None else {"contexts": 0})}
        if capability == "app.context.bind":
            registry = getattr(self, "app_contexts", None)
            if registry is None:
                return {"ok": False, "error": "app context registry unavailable"}
            target = None
            window_id = params.get("window_id")
            if isinstance(window_id, int) and window_id > 0:
                target = next((w for w in win.list_windows() if w.hwnd == window_id), None)
            if target is None:
                return {"ok": False, "error": "observed window id is missing or stale"}
            context = registry.bind_window(target, task_id=str(params.get("task_id", "")),
                                           app=str(params.get("app", "")))
            context.resolve_content_window()
            context.observe(self.desktop_awareness)
            return {"ok": True, "context": context.to_dict()}

        # ---------------------------------------------------------- browser
        if capability.startswith("browser."):
            return self._browser_action(capability, params)

        return {"ok": False, "error": f"no handler for capability {capability}"}

    def _focus_input_target(self, params):
        hwnd = params.get("window_id")
        target = next((w for w in win.list_windows() if w.hwnd == hwnd
                       and w.pid == params.get("target_pid")), None)
        if target is None or not win.focus_window(hwnd):
            return False
        foreground = win.foreground_window()
        return bool(foreground and foreground.hwnd == hwnd and foreground.pid == target.pid)

    # ------------------------------------------------------------ app helpers
    def _app_windows(self, entry, exe):
        candidates = [w for w in win.list_windows() if w.visible and w.class_name not in
                      ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")]
        if entry.method == "appsfolder":
            app_id = entry.target.removeprefix("shell:AppsFolder\\").casefold()
            identities = {w.pid: win.application_user_model_id(w.pid).casefold() for w in candidates}
            return [w for w in candidates if identities[w.pid] == app_id]
        if exe:
            return [w for w in candidates if w.process.casefold() == exe.casefold()]
        matches = [w for w in candidates if w.title.casefold() == entry.name.casefold()]
        return matches if len(matches) == 1 else []

    def _open_app(self, ctx: CallContext, strategy: str, params: Dict[str, Any],
                  plan: Dict[str, Any]) -> Dict[str, Any]:
        target = str(params.get("target", "")).strip()
        entry = None
        cached = self._launch_cache.get(target) if self._launch_cache else None
        if cached:
            entry = apps.AppEntry(name=cached["name"], target=cached["target"],
                                  method=cached["method"], source="cache")
        if entry is None:
            entry = apps.resolve(target)
        if entry is None:
            matches = apps.resolve_candidates(target)
            return {"ok": False, "error": "Multiple applications match; choose the exact name." if matches else
                    f"Application '{target}' was not found in the refreshed application index.",
                    "candidates": [e.to_dict() for e in matches],
                    "error_code": "ambiguous_application" if matches else "application_not_discovered"}

        # if it is already running with a window, focus it instead of launching again
        exe = apps.expected_process(entry)
        visible = self._app_windows(entry, exe)
        if visible:
            registry = getattr(self, "app_contexts", None)
            bound = registry.get_for_task(ctx.interaction_id) if registry else None
            matching = [w for w in visible if bound and w.hwnd == bound.hwnd and w.pid == bound.pid]
            if len(visible) > 1 and not matching:
                return {"ok": False, "error_code": "ambiguous_application",
                        "error": "Multiple application windows are open. Select and bind the intended window before editing.",
                        "candidates": [w.to_dict() for w in visible]}
            chosen = matching[0] if matching else visible[0]
            focused = win.focus_window(chosen.hwnd)
            return {"ok": bool(focused), "already_running": True, "strategy": "focus-existing",
                    "expected_process": exe, "hwnd": chosen.hwnd, "window_pid": chosen.pid,
                    "application_id": win.application_user_model_id(chosen.pid),
                    "detail": "Focused the existing window" if focused else "Existing window could not be focused"}

        launched = apps.launch(entry)
        if not launched.get("ok"):
            if self._launch_cache:
                self._launch_cache.record(target, entry.method, entry.target, ok=False)
            return {"ok": False, "error": launched.get("error", "launch failed"),
                    "expected_process": exe}

        # wait for the real effect (process + visible window) — this is what verification checks
        started = time.time()
        deadline = started + float(params.get("wait_s", APP_WAIT_S))
        while time.time() < deadline:
            wins = self._app_windows(entry, exe)
            if wins:
                if self._launch_cache:
                    self._launch_cache.record(target, entry.method, entry.target, ok=True)
                return {"ok": True, "expected_process": exe, "hwnd": wins[0].hwnd,
                        "window_pid": wins[0].pid, "application_id": win.application_user_model_id(wins[0].pid),
                        "window": wins[0].title, "strategy": strategy,
                        "launch": launched, "waited_ms": int((time.time() - started) * 1000)}
            time.sleep(0.4)
        if self._launch_cache:
            self._launch_cache.record(target, entry.method, entry.target, ok=False)
        return {"ok": False, "expected_process": exe, "error_code": "action_outcome_unknown",
                "action_may_have_run": True,
                "error": f"{exe or target} did not produce a visible window within "
                         f"{params.get('wait_s', APP_WAIT_S)}s"}

    def _close_app(self, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        target = str(params.get("target", "")).strip()
        entry = apps.resolve(target)
        exe = apps.expected_process(entry) if entry else (
            target if target.lower().endswith(".exe") else f"{target}.exe")
        windows = win.find_windows(process=exe)
        if strategy == "window-close" and windows:
            for w in windows:
                win.close_window(w.hwnd)
            deadline = time.time() + 8
            while time.time() < deadline and state.process_running(exe):
                time.sleep(0.3)
            if not state.process_running(exe):
                return {"ok": True, "expected_process": exe, "method": "WM_CLOSE"}
        from .shell import run as shell_run
        res = shell_run(f"taskkill /IM {exe} /F", shell="cmd", tier="elevated", timeout_s=15)
        return {"ok": res.ok, "expected_process": exe, "method": "taskkill",
                "detail": res.stdout or res.stderr}

    def _window_action(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        hwnd = params.get("hwnd")
        if not hwnd:
            title = str(params.get("title", ""))
            process = str(params.get("process", ""))
            matches = win.find_windows(title_contains=title, process=process)
            if not matches:
                return {"ok": False, "error": f"no window matched title={title!r} process={process!r}"}
            hwnd = matches[0].hwnd
        hwnd = int(hwnd)
        if capability == "window.close":
            ok = win.close_window(hwnd)
            return {"ok": ok, "hwnd": hwnd}
        if capability == "window.move":
            ok = win.move_window(hwnd, int(params.get("x", 0)), int(params.get("y", 0)),
                                 int(params.get("width", 800)), int(params.get("height", 600)))
            return {"ok": ok, "hwnd": hwnd}
        if capability == "window.focus":
            return {"ok": win.focus_window(hwnd), "hwnd": hwnd}
        command = capability.split(".", 1)[1]
        return {"ok": win.show_window(hwnd, command), "hwnd": hwnd, "command": command}

    def _file_action(self, capability: str, params: Dict[str, Any],
                     w: workspace.Workspace) -> Dict[str, Any]:
        def _resolve(key: str) -> str:
            raw = params.get(key)
            if raw is None:
                return ""
            p = str(raw)
            # relative paths are always workspace-relative (agent safety)
            if not (len(p) > 1 and p[1] == ":") and not p.startswith(("\\\\", "/")):
                return str(w.path(p))
            return p

        if capability == "files.read":
            return files.read_text(_resolve("path"))
        if capability == "files.locations":
            return {"ok": True, "locations": files.user_locations()}
        if capability == "files.write":
            return files.write_text(_resolve("path"), str(params.get("text", "")))
        if capability == "files.append":
            return files.append_text(_resolve("path"), str(params.get("text", "")))
        if capability == "files.delete":
            return files.delete(_resolve("path"), to_recycle_bin=bool(params.get("recycle", True)))
        if capability == "files.move":
            return files.move(_resolve("src"), _resolve("dst"),
                              overwrite=bool(params.get("overwrite")))
        if capability == "files.copy":
            return files.copy(_resolve("src"), _resolve("dst"),
                              overwrite=bool(params.get("overwrite", True)))
        if capability == "files.mkdir":
            return files.mkdir(_resolve("path"))
        if capability == "files.list":
            return files.list_dir(_resolve("path"), str(params.get("pattern", "*")),
                                  bool(params.get("recursive")))
        if capability == "files.find":
            root = _resolve("root")
            if root:
                return files.find(root, str(params.get("name", "")), params.get("extensions"),
                                  int(params.get("min_size", 0)), int(params.get("limit", 50)))
            # no root given: search the user's usual places (read-only, PTE-gated)
            limit = int(params.get("limit", 50))
            combined: List[Dict[str, Any]] = []
            searched: List[str] = []
            for candidate in user_search_roots():
                if not Path(candidate).exists():
                    continue
                searched.append(candidate)
                result = files.find(candidate, str(params.get("name", "")),
                                    params.get("extensions"), int(params.get("min_size", 0)),
                                    limit=limit)
                combined.extend(result.get("matches", []))
            combined.sort(key=lambda m: -m["mtime"])
            return {"ok": True, "root": "user-folders", "searched": searched,
                    "count": len(combined), "matches": combined[:limit]}
        if capability == "files.exists":
            p = _resolve("path")
            return {"ok": files.exists(p), "path": p, "exists": files.exists(p)}
        if capability == "files.disk_usage":
            raw_path = str(params.get("path") or "").strip()
            if raw_path.casefold() == "all":
                return files.disk_usage("all")
            if not raw_path:
                raw_path = os.environ.get("SystemDrive", "C:") + "\\"
            return files.disk_usage(_resolve("path") if params.get("path") else raw_path)
        return {"ok": False, "error": f"unknown file capability {capability}"}

    def _browser_action(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from browser.service import get_browser
            # always use the GENIE-managed profile inside the workspace, never the CWD
            browser = get_browser(workspace_root=self.workspace.root, db=self.db,
                                  locks=getattr(self, "locks", None))
            params.setdefault("_trace_id", "")
            # a session lease keeps two agents from fighting over the same browser
            holder = str(params.get("_holder") or f"browser-{id(self)}")
            lease = browser._acquire_lease("browser.session:default", holder)
            if not lease.get("ok"):
                return {"ok": False, "error": lease.get("error", "browser session busy"),
                        "error_code": "session_locked"}
            try:
                with browser._lock:
                    bound = browser.session_context().get("task_id")
                    task_id = str(params.get("task_id") or holder)
                    params.setdefault("task_id", task_id)
                    if bound and bound != task_id:
                        return {"ok": False, "error_code": "session_locked",
                                "error": "This browser is bound to another task. Finish that task before selecting a different session."}
                    if not bound and capability != "browser.session":
                        # A generic tool call must not silently select GENIE's
                        # separate Chrome profile when the owner meant Brave.
                        # Carry an explicit requested mode; otherwise retain a
                        # verified existing attachment, if any.
                        requested_mode = str(params.get("browser_mode") or "")
                        if requested_mode in ("owner_existing", "genie_owned"):
                            browser._task_session = {"task_id": task_id,
                                                     "mode": requested_mode,
                                                     "attached": bool(browser._client)}
                        elif browser._owner_browser:
                            browser._task_session = {"task_id": task_id,
                                                     "mode": "owner_existing",
                                                     "attached": True}
                    return browser.handle(capability, params)
            finally:
                browser._release_lease("browser.session:default", holder)
        except Exception as exc:
            return {"ok": False, "error": f"browser provider unavailable: {exc}"}

    # ---------------------------------------------------------------- helpers
    def _settle(self, capability: str, outcome: Dict[str, Any]) -> None:
        """Give the OS a moment to reach the new state before observing it."""
        if capability.startswith(("application.", "window.")):
            time.sleep(DEFAULT_SETTLE_S)
        elif capability.startswith(("system.volume", "clipboard", "files.")):
            time.sleep(0.12)
        elif capability.startswith("input."):
            time.sleep(0.2)

    def _record_verification(self, ctx: CallContext, capability: str, strategy: str,
                             outcome: verifier.Outcome, attempts: int, act_ms: int) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT INTO action_verifications(mission_id, capability, strategy, ok,"
                " detail, attempts, latency_ms, ts) VALUES(?,?,?,?,?,?,?,?)",
                (ctx.mission_id or "", capability, strategy, 1 if outcome.verified else 0,
                 outcome.detail[:400], attempts, act_ms, int(time.time())))
        except Exception as exc:
            log.debug("could not persist verification: %s", exc)

    def _audit(self, ctx: CallContext, capability: str, result: str, detail: str) -> None:
        if self.audit:
            self.audit.record(who=ctx.person_id, device=ctx.device_id,
                              action=f"computer.{capability}", why=ctx.mission_id or "direct",
                              mission_id=ctx.mission_id, result=f"{result}: {detail}"[:300],
                              trace_id=ctx.trace_id)

    def _event(self, event_type: str, payload: Dict[str, Any], ctx: CallContext) -> None:
        try:
            self._bus.publish(event_type, payload, trace_id=ctx.trace_id)
        except Exception:
            pass

    def capabilities(self) -> List[str]:
        return sorted(set(planner.CHAINS) | verifier.READ_ONLY)
