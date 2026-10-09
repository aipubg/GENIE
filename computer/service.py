"""Computer Service (computer/service) — contract C7.

Every capability call follows:  PTE check -> PLAN -> ACT -> OBSERVE -> VERIFY -> (RECOVER)
and is audited. An action that cannot be verified is reported as NOT ok, whatever the
underlying call returned.
"""
from __future__ import annotations

import os

import threading
from typing import Any, Dict, List, Optional

from core.contracts import ActionResult, CallContext, EventType
from core.events import get_bus
from core.logging_setup import get_logger

from . import desktop_awareness, locks, planner, state, verifier, windows_api as win, workspace
from .executor import Executor

log = get_logger("computer.service")

# capability -> PTE scope
SCOPE_BY_CAPABILITY: Dict[str, str] = {
    "application.open": "computer:app:open",
    "application.close": "computer:app:close",
    "application.list": "computer:app:read",
    "application.resolve": "computer:app:read",
    "app.context.status": "computer:app:read",
    "app.context.bind": "computer:app:read",
    "apps.discover": "computer:app:read",
    "window.list": "computer:window:read",
    "window.focus": "computer:window:control",
    "window.minimize": "computer:window:control",
    "window.minimize_all": "computer:window:control",
    "window.maximize": "computer:window:control",
    "window.restore": "computer:window:control",
    "window.move": "computer:window:control",
    "window.close": "computer:window:control",
    "processes.list": "computer:processes:read",
    "process.kill": "computer:processes:kill",
    "system.volume.set": "computer:system.volume:set",
    "system.volume.up": "computer:system.volume:up",
    "system.volume.down": "computer:system.volume:down",
    "system.volume.mute": "computer:system.volume:mute",
    "system.audio.state": "computer:system.volume:read",
    "system.audio.devices": "computer:system.volume:read",
    "system.network.state": "computer:state:read",
    "system.settings.open": "computer:app:open",
    "files.read": "computer:files:read",
    "files.locations": "computer:files:read",
    "files.write": "computer:files:write",
    "files.append": "computer:files:write",
    "files.delete": "computer:files:delete",
    "files.move": "computer:files:write",
    "files.copy": "computer:files:write",
    "files.mkdir": "computer:files:write",
    "files.list": "computer:files:read",
    "files.find": "computer:files:read",
    "files.exists": "computer:files:read",
    "files.disk_usage": "computer:system:read",
    "clipboard.get": "computer:clipboard:read",
    "clipboard.set": "computer:clipboard:write",
    "shell.run": "computer:shell:run",
    "shell.powershell_json": "computer:shell:run",
    "uia.windows": "computer:uia:read",
    "uia.find": "computer:uia:read",
    "uia.tree": "computer:uia:read",
    "uia.get_value": "computer:uia:read",
    "uia.invoke": "computer:uia:control",
    "uia.click": "computer:uia:control",
    "uia.set_value": "computer:uia:control",
    "uia.focus": "computer:uia:control",
    "uia.set_toggle": "computer:uia:control",
    "uia.select": "computer:uia:control",
    "uia.state": "computer:uia:read",
    "message.prepare": "computer:uia:control",
    "message.send": "computer:uia:control",
    "input.type_text": "computer:input:keyboard",
    "input.key": "computer:input:keyboard",
    "input.hotkey": "computer:input:keyboard",
    "input.click": "computer:input:mouse",
    "input.move": "computer:input:mouse",
    "input.scroll": "computer:input:mouse",
    "input.state": "computer:input:read",
    "screen.capture": "computer:screen:read",
    "screen.capture_window": "computer:screen:read",
    "screen.state": "computer:screen:read",
    "computer.state": "computer:state:read",
    "workspace.list": "computer:workspace:read",
    "workspace.usage": "computer:workspace:read",
    "workspace.info": "computer:workspace:read",
    # --- GENIE Workspace / background computer (§11C) ---
    "workspace.identity": "computer:workspace:read",
    "workspace.missions": "computer:workspace:read",
    "workspace.quota": "computer:workspace:read",
    "workspace.session_dir": "computer:workspace:read",
    "workspace.mission_claim": "computer:workspace:write",
    "workspace.mission_release": "computer:workspace:write",
    "workspace.set_quota": "computer:workspace:write",
    "workspace.cleanup": "computer:workspace:write",
    "workspace.shell": "computer:workspace:exec",
    "desktop.lock_state": "computer:state:read",
    "desktop.lock_resume": "computer:input:control",
    "browser.session": "browser:session",
    "browser.navigate": "browser:navigate",
    "browser.open_default": "browser:navigate",
    "browser.open_named": "browser:navigate",
    "browser.installed": "browser:read",
    "browser.dom_query": "browser:dom:read",
    "browser.click": "browser:dom:control",
    "browser.type": "browser:dom:control",
    "browser.extract": "browser:dom:read",
    "browser.screenshot": "browser:read",
    "browser.tabs": "browser:read",
    "browser.tabs_list": "browser:read",
    "browser.tab_new": "browser:tab:control",
    "browser.tab_switch": "browser:tab:control",
    "browser.tab_close": "browser:tab:control",
    "browser.wait": "browser:read",
    "browser.select": "browser:dom:control",
    "browser.checkbox": "browser:dom:control",
    "browser.scroll": "browser:dom:control",
    "browser.reload": "browser:navigate",
    "browser.upload": "browser:upload",
    "browser.download": "browser:download",
    "browser.downloads": "browser:read",
    "browser.dialog": "browser:dom:control",
    "browser.history": "browser:navigate",
    "browser.accessibility": "browser:read",
    "browser.cookies": "browser:read",
    "browser.leases": "browser:read",
    "browser.media.play": "browser:media:play",
    "browser.media.volume": "browser:dom:control",
    "browser.fullscreen": "browser:fullscreen",
    "browser.observe": "browser:read",
    "browser.detect_gate": "browser:read",
    "browser.verify": "browser:read",
    "browser.act": "browser:dom:control",
    "browser.fill": "browser:dom:control",
    "browser.chatgpt.image": "browser:dom:control",
    "browser.website_task": "browser:dom:control",
    "skill.search": "skill:read",
    "skill.execute": "skill:execute",
    # --- Desktop Awareness (Phase 1 extension) ---
    "desktop.observe": "computer:desktop:read",
    "desktop.visual_observe": "computer:desktop:read",
    "desktop.visual_click": "computer:input:mouse",
    "desktop.visual_action": "computer:desktop:control",
    "desktop.observe_display": "computer:desktop:read",
    "desktop.capture_display": "computer:desktop:read",
    "desktop.list_displays": "computer:desktop:read",
    "desktop.pause": "computer:desktop:control",
    "desktop.resume": "computer:desktop:control",
    "desktop.settings.get": "computer:desktop:read",
    "desktop.settings.set": "computer:desktop:write",
}


# payload keys too large to ship to the UI (names/counts are kept instead)
_EXCLUDED_PAYLOAD_KEYS = {"processes", "strategy", "capability", "ok", "detail", "dry_run"}


class ComputerService:
    def __init__(self, trust=None, audit=None, db=None, locks_service=None,
                 workspace_root: Optional[str] = None, data_dir: Optional[str] = None):
        self.trust = trust
        from .approvals import ActionApprovals
        from .owner_policy import OwnerPolicy
        from pathlib import Path
        self.owner_policy = OwnerPolicy(Path(data_dir) / "owner-automation-policy.json" if data_dir else None)
        self.approvals = ActionApprovals(self.owner_policy, audit)
        self.audit = audit
        self.db = db
        self._bus = get_bus()
        self.desktop_lock = locks.DesktopLock(locks_service)
        # Track A: one stable application identity per task, so a multi-step
        # workflow does not rediscover the app from zero on every call. Identity
        # only - execution stays in this service/executor.
        from .app_context import ApplicationContextRegistry
        self.app_contexts = ApplicationContextRegistry()
        self.desktop_awareness = desktop_awareness.DesktopAwareness(
            data_dir=data_dir or workspace_root or os.getcwd())
        self.executor = Executor(db=db, audit=audit, trust=trust,
                                 desktop_lock=self.desktop_lock,
                                 workspace_root=workspace_root,
                                 desktop_awareness=self.desktop_awareness)
        # The executor dispatches app-context capabilities; it shares the one
        # registry owned by this service (no second authority).
        self.executor.app_contexts = self.app_contexts
        from .messages import MessageTransactions
        self.executor.messages = MessageTransactions(self.approvals, self.desktop_lock)
        from .visual import VisualFallback
        self.executor.visual = VisualFallback(self.desktop_awareness, self.approvals, desktop_lock=self.desktop_lock)
        # wrong-action rate instrumentation (Phase 14 exit gate) — created lazily so that a
        # service built without a db still works.
        self._action_meter = None

    @property
    def action_meter(self):
        if self._action_meter is None:
            from core.action_metrics import ActionMeter
            self._action_meter = ActionMeter(db=self.db)
        return self._action_meter

    # ---------------------------------------------------------------- dispatch
    def execute(self, ctx: CallContext, capability: str,
                params: Dict[str, Any] | None = None,
                cancel_event: Optional[threading.Event] = None) -> ActionResult:
        params = dict(params or {})
        params["task_id"] = ctx.interaction_id

        # capability must exist before we ask for permission (unknown == programming error)
        if capability not in SCOPE_BY_CAPABILITY:
            return ActionResult(False, capability, f"unsupported capability: {capability}")

        # Raw keyboard input requires a task-bound or explicitly observed target.
        # Never send text to whichever window happens to be foreground.
        if capability in ("input.type_text", "input.key", "input.hotkey", "input.scroll"):
            task_id = ctx.interaction_id
            registry = getattr(self, "app_contexts", None)
            bound = registry.get_for_task(task_id) if registry and task_id else None
            supplied = params.get("window_id")
            if bound is not None:
                if supplied is not None and int(supplied) != bound.hwnd:
                    return ActionResult(False, capability, "target window differs from bound context",
                                        data={"error_code": "target_not_grounded"})
                current = next((w for w in win.list_windows() if w.hwnd == bound.hwnd and w.pid == bound.pid), None)
                if current is None:
                    return ActionResult(False, capability, "Bound application is no longer available",
                                        data={"error_code": "stale_application"})
                bound.window_title = current.title
                params.update(window_id=bound.hwnd, verify_in_window=current.title,
                              target_pid=bound.pid, target_process=bound.process,
                              task_id=task_id)
            else:
                title = str(params.get("verify_in_window") or params.get("window") or "")
                hwnd = int(supplied or 0)
                target = next((w for w in win.list_windows() if w.hwnd == hwnd), None) if hwnd > 0 else None
                if target is None or not title or target.title != title:
                    return ActionResult(False, capability,
                                        "keyboard and scroll input require a freshly observed exact window id and title",
                                        data={"error_code": "target_not_grounded"})
                params.update(window_id=hwnd, verify_in_window=target.title,
                              target_pid=target.pid, target_process=target.process,
                              task_id=task_id)

        # safe mode (§14.2): dangerous capabilities are switched off while GENIE stays usable.
        # Checked on capability ids, which use dots — not on PTE scopes, which use colons.
        from core.hardening import get_safe_mode
        safe = get_safe_mode()
        if not safe.is_allowed(capability):
            detail = safe.explain(capability)
            self.action_meter.record(capability=capability, ok=False, verified=False,
                                     detail=detail)
            return ActionResult(False, capability, detail)

        scope = SCOPE_BY_CAPABILITY[capability]
        if self.trust:
            decision = self.trust.check(ctx, scope, params)
            if not decision.allow:
                detail = f"denied: {decision.reason}"
                self.action_meter.record(capability=capability, ok=False, verified=False,
                                         detail=detail)
                return ActionResult(False, capability, detail,
                                    data={"error_code": "permission_denied", "scope": scope})
            approval_received = self.approvals.consume_execution(ctx, capability, params)
            if decision.needs_confirm and not approval_received:
                import json
                cancel = cancel_event or threading.Event()
                summary = "Authorize this exact action: " + capability + "\n" + json.dumps(
                    {k: v for k, v in params.items() if not k.startswith("_") and k != "confirmed"},
                    ensure_ascii=False, default=str)
                approved = self.approvals.request(ctx, summary, cancel)
                if not approved.get("ok"):
                    return ActionResult(False, capability, approved.get("error", "Owner approval required"),
                                        data={"error_code": approved.get("error_code", "owner_confirmation_required"), "scope": scope})

        params.pop("_approval_receipt", None)
        params.pop("confirmed", None)

        result = self.executor.execute(ctx, capability, params, cancel_event=cancel_event)
        if capability == "application.open" and result.ok and result.verified:
            hwnd = (result.result or {}).get("hwnd")
            pid = (result.result or {}).get("window_pid")
            target = next((w for w in win.list_windows() if w.hwnd == hwnd and w.pid == pid), None)
            if target is not None:
                self.app_contexts.bind_window(target, task_id=ctx.interaction_id,
                                             app=str(params.get("target") or target.process))
        if capability == "uia.focus" and result.ok and result.verified:
            from . import uia
            control = uia.describe_registered(str(params.get("element_id") or ""))
            target = next((w for w in win.list_windows() if w.hwnd == control.get("window_id")
                           and w.pid == control.get("window_process_id", control.get("process_id"))), None)
            if target is not None:
                self.app_contexts.bind_window(target, task_id=ctx.interaction_id, app=target.process)
        # wrong-action instrumentation: count only real executions here
        self.action_meter.record(capability=capability, ok=result.ok,
                                 verified=result.verified, detail=result.detail)

        if self.audit:
            self.audit.record(who=ctx.person_id, device=ctx.device_id,
                              action=f"computer.{capability}",
                              why=ctx.mission_id or "direct", mission_id=ctx.mission_id,
                              result=f"{'ok' if result.ok else 'failed'}: {result.detail}"[:300],
                              trace_id=ctx.trace_id)
        self._bus.publish(
            EventType.APP_OPENED if capability == "application.open" else "COMPUTER_ACTION",
            {"capability": capability, "ok": result.ok, "verified": result.verified,
             "detail": result.detail, "attempts": result.attempts,
             "strategy": result.strategy_used, "latency_ms": result.latency_ms},
            trace_id=ctx.trace_id)

        # payload: everything the action produced, minus oversized internals
        payload = {k: v for k, v in (result.result or {}).items()
                   if k not in _EXCLUDED_PAYLOAD_KEYS}
        return ActionResult(
            ok=result.ok, capability=capability, detail=result.detail,
            verified=result.verified,
            data={
                "attempts": result.attempts,
                "strategies_tried": result.strategies_tried,
                "strategy_used": result.strategy_used,
                "verification": result.verification,
                "latency_ms": result.latency_ms,
                "verify_ms": result.verify_ms,
                "takeover": result.takeover,
                "cancelled": result.cancelled,
                "dry_run": result.dry_run,
                "error_code": result.error_code,
                "error": result.error,
                **payload,
            })

    # ------------------------------------------------------------------ status
    def capabilities(self) -> List[str]:
        return sorted(SCOPE_BY_CAPABILITY)

    def plan_for(self, capability: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return planner.plan(capability, params or {})

    def state(self, include_processes: bool = True) -> Dict[str, Any]:
        return state.snapshot(include_processes=include_processes).to_dict()

    def health(self) -> Dict[str, Any]:
        from . import uia
        return {
            "win32": win.available(),
            "uia": uia.status(),
            "desktop_lock": self.desktop_lock.state(),
            "desktop_awareness": self.desktop_awareness.status,
            "workspace": self.executor.workspace.usage(),
            "isolation": workspace.detect_isolation(),
            "resources": workspace.resource_profile(),
            "verifiers": len(verifier.REGISTRY),
            "read_only_capabilities": len(verifier.READ_ONLY),
            "capabilities": len(SCOPE_BY_CAPABILITY),
            "model_tools": sorted(__import__("computer.tool_bridge", fromlist=["NAMES"]).NAMES),
            "owner_policy_enabled": self.owner_policy.status()["enabled"],
        }

    def check_takeover(self) -> Optional[Dict[str, Any]]:
        return self.desktop_lock.check_takeover()

    def resume_automation(self) -> None:
        self.desktop_lock.resume()
