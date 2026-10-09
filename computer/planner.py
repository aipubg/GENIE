"""Execution planner (computer/planner).

Every capability has an ordered list of **strategies**, following the automation priority:

    native application/plugin API -> Windows/system API -> UI Automation/accessibility
    -> browser DOM -> vision -> raw mouse/keyboard coordinates

The planner picks the first strategy that is actually available given the observed machine
state, and hands the rest to the executor as fallbacks. This is what makes the executor able
to RECOVER instead of assuming success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

from . import apps, uia, windows_api as win, workspace

log = get_logger("computer.planner")


@dataclass
class Strategy:
    name: str
    priority: int                       # lower = preferred
    needs: List[str] = field(default_factory=list)   # availability prerequisites
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "priority": self.priority, "needs": self.needs,
                "note": self.note}


def _uia_ok() -> bool:
    return uia.available()


def _win_ok() -> bool:
    return win.available()


AVAILABILITY: Dict[str, Callable[[], bool]] = {
    "win32": _win_ok,
    "uia": _uia_ok,
    "apps": lambda: True,
    "workspace": lambda: True,
    "shell": lambda: True,
    "browser": lambda: True,
    "vision": _win_ok,  # consent and eligible provider checked per request
    "raw-input": _win_ok,
}


# Ordered strategy chains per capability (most reliable first)
CHAINS: Dict[str, List[Strategy]] = {
    "application.open": [
        Strategy("native-launch", 10, ["apps"], "resolve the installed app and launch its exe"),
        Strategy("shell-start", 30, ["shell"], "start via the shell (shortcut/association)"),
        Strategy("uia-startmenu", 60, ["uia"], "drive the Start menu through accessibility"),
        Strategy("raw-search", 90, ["raw-input"], "Win key + typed search (last resort)"),
    ],
    "application.close": [
        Strategy("window-close", 10, ["win32"], "post WM_CLOSE to the app window"),
        Strategy("taskkill", 40, ["shell"], "terminate the process"),
    ],
    "window.focus": [
        Strategy("win32-setforeground", 10, ["win32"]),
        Strategy("uia-focus", 40, ["uia"], "focus through the accessibility tree"),
    ],
    "window.minimize": [Strategy("win32-showwindow", 10, ["win32"])],
    "window.minimize_all": [Strategy("win32-enum-minimize", 10, ["win32"])],
    "window.maximize": [Strategy("win32-showwindow", 10, ["win32"])],
    "window.restore": [Strategy("win32-showwindow", 10, ["win32"])],
    "window.move": [Strategy("win32-setwindowpos", 10, ["win32"])],
    "system.volume.set": [
        Strategy("core-audio", 10, ["win32"], "IAudioEndpointVolume (verifiable)"),
        Strategy("key-scancode", 80, ["raw-input"], "media keys (approximate)"),
    ],
    "system.volume.up": [Strategy("core-audio", 10, ["win32"]),
                         Strategy("key-scancode", 80, ["raw-input"])],
    "system.volume.down": [Strategy("core-audio", 10, ["win32"]),
                           Strategy("key-scancode", 80, ["raw-input"])],
    "system.volume.mute": [Strategy("core-audio", 10, ["win32"]),
                           Strategy("key-scancode", 80, ["raw-input"])],
    "files.write": [Strategy("atomic-write", 10, ["workspace"])],
    "files.read": [Strategy("direct-read", 10, ["workspace"])],
    "files.move": [Strategy("shutil-move", 10, ["workspace"])],
    "files.copy": [Strategy("shutil-copy", 10, ["workspace"])],
    "files.delete": [Strategy("recycle-bin", 10, ["win32"], "recoverable delete"),
                     Strategy("hard-delete", 60, ["workspace"], "permanent (needs grant)")],
    "files.find": [Strategy("indexed-scan", 10, ["workspace"])],
    "files.disk_usage": [Strategy("volume-usage", 10, [])],
    "clipboard.set": [Strategy("win32-clipboard", 10, ["win32"])],
    "clipboard.get": [Strategy("win32-clipboard", 10, ["win32"])],
    "shell.run": [Strategy("powershell", 10, ["shell"]), Strategy("cmd", 40, ["shell"])],
    "uia.find": [Strategy("uia-tree", 10, ["uia"])],
    "uia.invoke": [Strategy("uia-invoke", 10, ["uia"]),
                   Strategy("rect-click", 70, ["raw-input"], "click the element rectangle")],
    "uia.set_value": [Strategy("uia-set-value", 10, ["uia"]),
                      Strategy("focus-type", 60, ["uia", "raw-input"], "focus then type")],
    "uia.set_toggle": [Strategy("uia-toggle-pattern", 10, ["uia"])],
    "uia.select": [Strategy("uia-selection-pattern", 10, ["uia"])],
    "message.prepare": [Strategy("uia-message-transaction", 10, ["uia"])],
    "message.send": [Strategy("uia-message-transaction", 10, ["uia"])],
    "input.type_text": [Strategy("sendinput-unicode", 10, ["raw-input"]),
                        Strategy("clipboard-paste", 50, ["win32"], "clipboard + Ctrl+V")],
    "input.click": [Strategy("sendinput-mouse", 10, ["raw-input"])],
    "input.hotkey": [Strategy("sendinput-chord", 10, ["raw-input"])],
    "screen.capture": [Strategy("gdi-bitblt", 10, ["win32"])],
    "screen.capture_window": [Strategy("gdi-bitblt", 10, ["win32"])],
    "browser.navigate": [Strategy("cdp", 10, ["browser"], "Chrome DevTools Protocol")],
    "browser.open_default": [Strategy("windows-url-handoff", 10, ["win32"])],
    "system.settings.open": [Strategy("windows-settings-uri", 10, ["win32"])],
    "browser.open_named": [Strategy("windows-url-handoff", 10, ["win32"])],
    "browser.installed": [Strategy("windows-registry", 10, ["win32"])],
    "browser.click": [Strategy("cdp-dom", 10, ["browser"], "DOM click (no coordinates)"),
                      Strategy("rect-click", 80, ["raw-input"])],
    "browser.type": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.extract": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.dom_query": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.tabs": [Strategy("cdp-http", 10, ["browser"])],
    "browser.screenshot": [Strategy("cdp-page", 10, ["browser"])],
    "browser.tabs_list": [Strategy("cdp-http", 10, ["browser"])],
    "browser.tab_new": [Strategy("cdp-http", 10, ["browser"])],
    "browser.tab_switch": [Strategy("cdp-http", 10, ["browser"])],
    "browser.tab_close": [Strategy("cdp-http", 10, ["browser"])],
    "browser.wait": [Strategy("cdp-condition", 10, ["browser"],
                              "condition polling, never a fixed sleep")],
    "browser.select": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.checkbox": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.scroll": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.reload": [Strategy("cdp-page", 10, ["browser"])],
    "browser.upload": [Strategy("cdp-dom-files", 10, ["browser"])],
    "browser.download": [Strategy("cdp-download", 10, ["browser"])],
    "browser.downloads": [Strategy("cdp-http", 10, ["browser"])],
    "browser.dialog": [Strategy("cdp-page", 10, ["browser"])],
    "browser.history": [Strategy("cdp-page", 10, ["browser"])],
    "browser.accessibility": [Strategy("cdp-a11y", 10, ["browser"])],
    "browser.cookies": [Strategy("cdp-network", 10, ["browser"])],
    "browser.leases": [Strategy("cdp-http", 10, ["browser"])],
    "browser.media.play": [Strategy("cdp-page", 10, ["browser"],
                                    "search -> open -> verified playback")],
    "browser.fullscreen": [Strategy("cdp-page", 10, ["browser"])],
    "browser.observe": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.detect_gate": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.act": [Strategy("cdp-dom", 10, ["browser"], "DOM click (no coordinates)")],
    "browser.fill": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.verify": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.chatgpt.image": [Strategy("cdp-dom", 10, ["browser"])],
    "browser.website_task": [Strategy("cdp-dom", 10, ["browser"],
                                      "bounded PLAN->ACT->OBSERVE->VERIFY loop")],
    # --- Desktop Awareness (Phase 1 extension) ---
    "desktop.observe": [Strategy("win32-api", 10, ["win32"])],
    "desktop.visual_observe": [Strategy("grounded-vision", 10, ["vision"])],
    "desktop.visual_click": [Strategy("grounded-vision", 10, ["vision", "raw-input"])],
    "desktop.visual_action": [Strategy("grounded-vision", 10, ["vision", "raw-input"])],
    "desktop.observe_display": [Strategy("win32-api", 10, ["win32"])],
    "desktop.capture_display": [Strategy("gdi-bitblt", 10, ["win32"])],
    "desktop.list_displays": [Strategy("win32-api", 10, ["win32"])],
    "desktop.pause": [Strategy("internal", 10, [])],
    "desktop.resume": [Strategy("internal", 10, [])],
    "desktop.settings.get": [Strategy("internal", 10, [])],
    "desktop.settings.set": [Strategy("internal", 10, [])],
    # --- generic input / window / file / system / browser session ---
    "window.list": [Strategy("win32-api", 10, ["win32"])],
    "input.scroll": [
        Strategy("win32-sendinput", 10, ["raw-input"], "wheel input via SendInput"),
    ],
    "input.move": [
        Strategy("win32-drag", 10, ["raw-input"], "press-move-release between observed targets"),
    ],
    "application.resolve": [Strategy("apps-index", 10, ["apps"])],
    "app.context.status": [Strategy("internal", 10, [])],
    "app.context.bind": [Strategy("win32-api", 10, ["win32"])],
    "files.locations": [Strategy("known-folders", 10, ["shell"])],
    "files.mkdir": [Strategy("win32-api", 10, ["win32"])],
    "files.list": [Strategy("win32-api", 10, ["win32"])],
    "system.audio.devices": [Strategy("core-audio", 10, ["win32"])],
    "system.network.state": [Strategy("win32-api", 10, ["win32"])],
    "uia.state": [Strategy("uia", 10, ["uia"])],
    "uia.get_value": [Strategy("uia", 10, ["uia"])],
    "uia.focus": [Strategy("uia", 10, ["uia"])],
    "input.state": [Strategy("win32-api", 10, ["win32"])],
    "browser.session": [
        Strategy("cdp-attach", 10, ["browser"], "attach to a verified CDP target"),
        Strategy("uia-window", 40, ["uia"], "bind to the exact browser window via accessibility"),
        Strategy("url-handoff", 80, ["shell"], "OS URL handoff (no control guarantee)"),
    ],
}

# Capabilities that need exclusive desktop input ownership
NEEDS_DESKTOP_LOCK = {
    "input.type_text", "input.click", "input.move", "input.scroll", "input.hotkey",
    "input.key", "uia.invoke", "uia.click", "uia.set_value", "uia.focus",
    "uia.set_toggle", "uia.select",
    "message.prepare",
    # send/visual_click acquire this same lease only AFTER owner confirmation.
}

# Capabilities that require a full state snapshot (heavier) for before/after comparison
NEEDS_FULL_STATE = {
    "application.open", "application.close", "process.kill", "window.focus",
    "window.minimize", "window.maximize", "window.restore", "window.close",
}


def strategies_for(capability: str) -> List[Strategy]:
    return sorted(CHAINS.get(capability, [Strategy("default", 50)]), key=lambda s: s.priority)


def available_strategies(capability: str) -> List[Strategy]:
    out = []
    for s in strategies_for(capability):
        if all(AVAILABILITY.get(n, lambda: True)() for n in s.needs):
            out.append(s)
        else:
            log.debug("strategy %s unavailable (needs %s)", s.name, s.needs)
    return out


def plan(capability: str, params: Dict[str, Any] | None = None,
         *, dry_run: bool = False) -> Dict[str, Any]:
    """Return the plan for a capability: ordered strategies + execution metadata."""
    params = params or {}
    strategies = available_strategies(capability)
    plan_dict: Dict[str, Any] = {
        "capability": capability,
        "strategies": [s.to_dict() for s in strategies],
        "needs_desktop_lock": capability in NEEDS_DESKTOP_LOCK,
        "needs_full_state": capability in NEEDS_FULL_STATE,
        "verified": True,
    }
    if capability == "application.open" and params.get("target") and not dry_run:
        entry = apps.resolve(str(params["target"]))
        plan_dict["resolved_app"] = entry.to_dict() if entry else None
        if entry is None:
            plan_dict["verified"] = False
            matches = apps.resolve_candidates(str(params["target"]))
            plan_dict["error_code"] = "ambiguous_application" if matches else "application_not_discovered"
            plan_dict["candidates"] = [e.to_dict() for e in matches]
            plan_dict["reason"] = ("Multiple applications match: " + ", ".join(e.name for e in matches)
                                   if matches else f"Application '{params['target']}' was not found in the refreshed index")
    elif capability == "application.open" and params.get("target") and dry_run:
        # A dry run validates the route without consulting or launching a real app.
        plan_dict["requested_app"] = str(params["target"])
    if not strategies:
        plan_dict["verified"] = False
        plan_dict["reason"] = "no available strategy for this capability"
    return plan_dict
