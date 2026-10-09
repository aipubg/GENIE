"""Action verification (computer/verifier).

**Nothing may be reported COMPLETED without passing through here.**

A capability result is only "verified" when the *resulting machine state* matches the
expected effect — not when the call returned without raising. Every verifier returns
`(verified, detail, evidence)` so the audit trail can show exactly what was checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.logging_setup import get_logger

from . import audio, files, state, uia, windows_api as win

log = get_logger("computer.verifier")


@dataclass
class VerifyContext:
    capability: str
    params: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    verified: bool
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"verified": self.verified, "detail": self.detail, "evidence": self.evidence}


def _windows_of(after: Dict[str, Any]) -> list:
    return after.get("windows") or []


def _window_matches(after: Dict[str, Any], title: str = "", process: str = "") -> list:
    out = []
    for w in _windows_of(after):
        if title and title.lower() not in (w.get("title") or "").lower():
            continue
        if process and process.lower() != (w.get("process") or "").lower():
            continue
        out.append(w)
    return out


# ------------------------------------------------------------------ verifiers
def _verify_app_open(c: VerifyContext) -> Outcome:
    target = c.params.get("target", "")
    exe = c.result.get("expected_process") or ""
    if c.result.get("hwnd") and c.result.get("window_pid"):
        match = next((w for w in _windows_of(c.after) if w.get("hwnd") == c.result["hwnd"]
                      and w.get("pid") == c.result["window_pid"] and w.get("visible")), None)
        if not match:
            return Outcome(False, "The resolved application window disappeared or changed process.")
        app_id = c.result.get("application_id")
        if app_id and win.application_user_model_id(match["pid"]) != app_id:
            return Outcome(False, "Application package identity changed after launch.")
        return Outcome(True, "Resolved application window is present and visible",
                       {"hwnd": match["hwnd"], "pid": match["pid"], "application_id": app_id})
    procs = c.after.get("processes") or []
    proc_ok = any(p["name"].lower() == exe.lower() for p in procs) if exe else False
    wins = _window_matches(c.after, process=exe) if exe else []
    if not wins and target:
        wins = _window_matches(c.after, title=target)
    visible = [w for w in wins if w.get("visible") and w.get("class_name") not in
               ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")]
    if proc_ok and visible:
        return Outcome(True, f"process {exe} running and window visible",
                       {"process": exe, "window": visible[0].get("title"),
                        "hwnd": visible[0].get("hwnd")})
    if proc_ok and not visible:
        return Outcome(False, f"process {exe} is running but no visible window yet",
                       {"process": exe, "windows": len(wins)})
    if visible and not exe:
        return Outcome(True, f"window matched by title: {visible[0].get('title')}",
                       {"window": visible[0].get("title")})
    return Outcome(False, f"no running process/window found for {exe or target}",
                   {"expected_process": exe, "process_count": len(procs)})


def _verify_app_close(c: VerifyContext) -> Outcome:
    exe = c.result.get("expected_process") or ""
    if not exe:
        return Outcome(False, "no expected process name to verify against")
    procs = [p for p in (c.after.get("processes") or []) if p["name"].lower() == exe.lower()]
    wins = _window_matches(c.after, process=exe)
    if not procs and not wins:
        return Outcome(True, f"{exe} is no longer running", {"processes": 0, "windows": 0})
    return Outcome(False, f"{exe} still present ({len(procs)} process, {len(wins)} window)",
                   {"processes": len(procs), "windows": len(wins)})


def _verify_window_moved(c: VerifyContext) -> Outcome:
    hwnd = int(c.result.get("hwnd") or c.params.get("hwnd") or 0)
    match = next((w for w in _windows_of(c.after) if w.get("hwnd") == hwnd), None)
    if not match:
        return Outcome(False, f"window {hwnd} not found after move")
    want_x, want_y = c.params.get("x"), c.params.get("y")
    rect = match.get("rect") or [0, 0, 0, 0]
    if want_x is None or want_y is None:
        return Outcome(True, "window moved", {"rect": rect})
    # allow a small tolerance for window-manager adjustment / DPI rounding
    ok = abs(rect[0] - int(want_x)) <= 16 and abs(rect[1] - int(want_y)) <= 16
    return Outcome(ok, f"window at {rect[0]},{rect[1]} (requested {want_x},{want_y})",
                   {"rect": rect, "requested": [want_x, want_y]})


def _verify_window_state(c: VerifyContext, expect: str) -> Outcome:
    hwnd = int(c.result.get("hwnd") or c.params.get("hwnd") or 0)
    match = next((w for w in _windows_of(c.after) if w.get("hwnd") == hwnd), None)
    if not match:
        return Outcome(False, f"window {hwnd} not found after action")
    if expect == "minimize" and match.get("minimized"):
        return Outcome(True, "window is minimized", {"hwnd": hwnd})
    if expect == "maximize" and match.get("maximized"):
        return Outcome(True, "window is maximized", {"hwnd": hwnd})
    if expect == "restore" and not match.get("minimized"):
        return Outcome(True, "window is restored", {"hwnd": hwnd})
    if expect == "focus" and (c.after.get("foreground") or {}).get("hwnd") == hwnd:
        return Outcome(True, "window is foreground", {"hwnd": hwnd})
    return Outcome(False, f"window state does not match {expect}",
                   {"hwnd": hwnd, "minimized": match.get("minimized"),
                    "maximized": match.get("maximized"),
                    "foreground": (c.after.get("foreground") or {}).get("hwnd")})


def _verify_minimize_all(c: VerifyContext) -> Outcome:
    result = c.result or {}
    if not result.get("ok"):
        return Outcome(False, str(result.get("error") or "not all windows were minimized"),
                       {"failed_hwnds": result.get("failed_hwnds", [])})
    targets = [int(hwnd) for hwnd in result.get("target_hwnds", [])]
    after_by_hwnd = {int(w.get("hwnd") or 0): w for w in _windows_of(c.after)}
    missing = [hwnd for hwnd in targets if hwnd not in after_by_hwnd]
    not_minimized = [hwnd for hwnd in targets
                     if hwnd in after_by_hwnd and not after_by_hwnd[hwnd].get("minimized")]
    if missing or not_minimized:
        return Outcome(False, "fresh observation did not confirm every target window minimized",
                       {"missing_hwnds": missing, "not_minimized_hwnds": not_minimized,
                        "target_count": len(targets)})
    return Outcome(True, f"fresh observation confirmed {len(targets)} application window(s) minimized",
                   {"minimized_hwnds": targets,
                    "skipped_shell_hwnds": result.get("skipped_shell_hwnds", [])})


def _verify_window_close(c: VerifyContext) -> Outcome:
    """Confirm the target window was actually CLOSED, never merely hidden.

    A closed window is destroyed: its HWND is gone and it is absent from the
    post-action snapshot. Minimize and hide are explicitly NOT close - the window
    and its HWND still exist - so they are reported as failures with the real
    reason instead of being counted as a successful close.

    HWNDs are recycled by Windows, so a still-live handle is only treated as "our"
    window when its identity (process + class) still matches the pre-action
    snapshot; a handle that now belongs to a different window proves the original
    was destroyed.
    """
    hwnd = int(c.result.get("hwnd") or c.params.get("hwnd") or 0)
    if not hwnd:
        return Outcome(False, "no window handle to verify", {"hwnd": hwnd})
    before = next((w for w in _windows_of(c.before) if w.get("hwnd") == hwnd), None)
    after = next((w for w in _windows_of(c.after) if w.get("hwnd") == hwnd), None)
    try:
        alive = bool(win.is_window(hwnd))
    except Exception:
        alive = False
    if after is None:
        if not alive:
            return Outcome(True, f"window {hwnd} is closed (destroyed)",
                           {"hwnd": hwnd, "exists": False})
        if before is not None and (
                (before.get("process") or "") != "" or (before.get("class_name") or "") != ""):
            # A live handle that is no longer in the snapshot and whose identity we
            # cannot re-confirm means the original window is gone.
            return Outcome(True, f"window {hwnd} is closed (handle no longer the same window)",
                           {"hwnd": hwnd, "exists": True, "reused": True})
        return Outcome(False, f"window {hwnd} still exists after close",
                       {"hwnd": hwnd, "exists": True})
    if after.get("minimized"):
        return Outcome(False, f"window {hwnd} is minimized, not closed",
                       {"hwnd": hwnd, "minimized": True, "exists": alive})
    if not after.get("visible"):
        return Outcome(False, f"window {hwnd} is hidden, not closed",
                       {"hwnd": hwnd, "hidden": True, "exists": alive})
    return Outcome(False, f"window {hwnd} still exists after close",
                   {"hwnd": hwnd, "exists": alive,
                    "title": after.get("title"), "process": after.get("process")})


def _verify_volume(c: VerifyContext) -> Outcome:
    level = c.params.get("level")
    if level is None:
        return Outcome(False, "no target level")
    actual = (c.after.get("audio") or {}).get("volume")
    if actual is None:
        return Outcome(False, "volume is not readable (no Core Audio endpoint)",
                       {"audio": c.after.get("audio")})
    if abs(int(actual) - int(level)) <= 2:
        return Outcome(True, f"volume is {actual} (requested {level})",
                       {"volume": actual, "requested": level})
    return Outcome(False, f"volume is {actual}, expected {level}", {"volume": actual})


def _verify_mute(c: VerifyContext) -> Outcome:
    want = c.params.get("muted")
    actual = (c.after.get("audio") or {}).get("muted")
    if actual is None:
        return Outcome(False, "mute state is not readable")
    if want is None or bool(actual) == bool(want):
        return Outcome(True, f"mute state is {actual}", {"muted": actual})
    return Outcome(False, f"mute state is {actual}, expected {want}", {"muted": actual})


def _verify_volume_relative(c: VerifyContext, direction: str) -> Outcome:
    before = (c.before.get("audio") or {}).get("volume")
    after = (c.after.get("audio") or {}).get("volume")
    if before is None or after is None:
        return Outcome(False, "volume not readable for before/after comparison")
    if direction == "up" and after >= before:
        return Outcome(True, f"volume {before} -> {after}", {"before": before, "after": after})
    if direction == "down" and after <= before:
        return Outcome(True, f"volume {before} -> {after}", {"before": before, "after": after})
    return Outcome(False, f"volume did not move {direction}: {before} -> {after}",
                   {"before": before, "after": after})


def _resolved_path(c: VerifyContext) -> Path:
    """The executor resolves workspace-relative paths; always verify the path it actually
    used, falling back to the raw parameter only when no result path exists."""
    return Path(str(c.result.get("path") or c.params.get("path") or ""))


def _verify_file_written(c: VerifyContext) -> Outcome:
    path = _resolved_path(c)
    if not path.exists():
        return Outcome(False, f"{path} does not exist after write")
    st = path.stat()
    expected_text = c.params.get("text")
    if expected_text is not None and path.suffix.lower() in (".txt", ".md", ".json", ".log", ""):
        try:
            actual = path.read_text(encoding="utf-8", errors="ignore")
            if actual == expected_text:
                return Outcome(True, f"{path} written with exact content ({st.st_size} bytes)",
                               {"size": st.st_size, "content_match": True})
            return Outcome(False, f"{path} content differs from what was requested",
                           {"size": st.st_size, "expected_len": len(expected_text),
                            "actual_len": len(actual)})
        except OSError as exc:
            return Outcome(False, f"could not read back {path}: {exc}")
    return Outcome(True, f"{path} exists ({st.st_size} bytes)", {"size": st.st_size})


def _verify_file_appended(c: VerifyContext) -> Outcome:
    """Append must be verified against the APPENDED fragment, not the whole file.

    files.append receives only the text being added. Verifying it with the
    "exact content" rule (correct for files.write) fails every time, because the
    file legitimately contains the original content plus the appended text. That
    produced a false verification failure: the append had actually succeeded.
    """
    path = _resolved_path(c)
    if not path.exists():
        return Outcome(False, f"{path} does not exist after append")
    expected_text = c.params.get("text")
    size = path.stat().st_size
    if expected_text is None:
        return Outcome(True, f"{path} exists ({size} bytes)")
    try:
        actual = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return Outcome(False, f"could not read back {path}: {exc}")
    if not actual.endswith(str(expected_text)):
        return Outcome(False, f"{path} does not end with the appended text",
                       {"size": size, "actual_tail": actual[-64:]})
    return Outcome(True,
                   f"{path} appended ({size} bytes, ends with the requested text)",
                   {"size": size, "appended": True})


def _verify_file_exists(c: VerifyContext) -> Outcome:
    path = _resolved_path(c)
    if path.exists():
        return Outcome(True, f"{path} exists", {"size": path.stat().st_size if path.is_file() else 0})
    return Outcome(False, f"{path} does not exist")


def _verify_file_gone(c: VerifyContext) -> Outcome:
    path = _resolved_path(c)
    if not path.exists():
        return Outcome(True, f"{path} is gone")
    return Outcome(False, f"{path} still exists")


def _verify_move(c: VerifyContext) -> Outcome:
    src = Path(str(c.result.get("src") or c.params.get("src") or ""))
    dst = Path(str(c.result.get("dst") or c.params.get("dst") or ""))
    if not dst.exists():
        return Outcome(False, f"destination {dst} does not exist")
    if src.exists():
        return Outcome(False, f"source {src} still exists after move")
    return Outcome(True, f"{src.name} moved to {dst}", {"dst": str(dst),
                                                        "size": dst.stat().st_size})


def _verify_copy(c: VerifyContext) -> Outcome:
    src = Path(str(c.result.get("src") or c.params.get("src") or ""))
    dst = Path(str(c.result.get("dst") or c.params.get("dst") or ""))
    if not dst.exists():
        return Outcome(False, f"destination {dst} does not exist")
    if src.is_file() and dst.is_file():
        same = files.sha256(src) == files.sha256(dst)
        return Outcome(same, f"copy hash match: {same}",
                       {"src": str(src), "dst": str(dst), "sha256_match": same})
    return Outcome(True, f"directory copied to {dst}")


def _verify_mkdir(c: VerifyContext) -> Outcome:
    path = _resolved_path(c)
    if path.is_dir():
        return Outcome(True, f"{path} is a directory")
    return Outcome(False, f"{path} is not a directory")


def _verify_clipboard_set(c: VerifyContext) -> Outcome:
    expected = c.params.get("text", "")
    actual = win.clipboard_get_text()
    if actual == expected:
        return Outcome(True, "clipboard content matches", {"len": len(actual)})
    return Outcome(False, "clipboard content differs",
                   {"expected_len": len(expected), "actual_len": len(actual)})


def _verify_shell(c: VerifyContext) -> Outcome:
    if c.result.get("refused"):
        return Outcome(False, f"refused by safety policy: {c.result.get('reason')}",
                       {"refused": True, "reason": c.result.get("reason")})
    code = c.result.get("exit_code")
    if c.result.get("ok") and (code in (0, None)):
        return Outcome(True, "command completed with exit code 0",
                       {"exit_code": code, "stdout_len": len(c.result.get("stdout") or "")})
    return Outcome(False, f"command failed (exit {code}): {(c.result.get('stderr') or '')[:200]}",
                   {"exit_code": code})


def _verify_input_delivered(c: VerifyContext) -> Outcome:
    """Raw input has no state of its own to verify: we can only confirm the OS accepted
    the input event. The *effect* must be confirmed by a follow-up observation
    (window state, UIA value, clipboard). Callers that need a hard guarantee should chain
    an observable capability and verify that instead."""
    if c.result.get("ok"):
        return Outcome(True, f"{c.capability} delivered to the OS (effect verified separately)",
                       {"delivery_only": True})
    return Outcome(False, f"{c.capability} was not delivered: {c.result.get('error')}")


def _verify_capture(c: VerifyContext) -> Outcome:
    path = Path(str(c.result.get("path") or ""))
    if path.exists() and path.stat().st_size > 1000:
        return Outcome(True, f"capture written ({path.stat().st_size} bytes)",
                       {"path": str(path), "bytes": path.stat().st_size})
    return Outcome(False, "capture file missing or empty")


def _verify_uia_action(c: VerifyContext) -> Outcome:
    element_id = c.result.get("element_id")
    if not element_id:
        return Outcome(False, "no element handle to verify")
    if c.capability in ("uia.set_toggle", "uia.select"):
        got = uia.control_state(element_id)
        key = "toggle_state" if c.capability == "uia.set_toggle" else "selected"
        expected = int(c.params["enabled"]) if key == "toggle_state" else True
        matches = got.get("ok") and key in got and got[key] == expected
        return Outcome(bool(matches), "control state matches" if matches else "control state did not match",
                       {"actual": got.get(key), "expected": expected})
    if c.capability == "uia.set_value":
        got = uia.get_value(element_id)
        expected = c.params.get("value")
        if got.get("ok") and got.get("value") == expected:
            return Outcome(True, "element value matches", {"value": got.get("value")})
        return Outcome(False, f"element value is {got.get('value')!r}, expected {expected!r}",
                       {"actual": got.get("value")})
    if c.capability == "uia.focus":
        got = uia.control_state(element_id)
        focused = got.get("ok") and got.get("focused") is True
        return Outcome(bool(focused), "Target control has keyboard focus" if focused else "Target control is not focused",
                       {"element_id": element_id})
    # invoke/click/focus: success means the call was accepted; the caller should assert
    # a higher-level expectation (window opened, text changed) where possible
    if c.result.get("ok"):
        return Outcome(True, f"{c.result.get('action', 'action')} accepted by the control",
                       {"element_id": element_id, "delivery_only": True})
    return Outcome(False, "control action was rejected")


def _verify_typed_text(c: VerifyContext) -> Outcome:
    """Typing succeeds only when the exact target's current UIA value contains it."""
    expected = c.params.get("text", "")
    window_title = c.params.get("verify_in_window") or ""
    window_id = int(c.params.get("window_id") or 0)
    if not window_title or not window_id:
        return Outcome(False, "typed text has no grounded target window",
                       {"unverified_target": True})
    current = next((w for w in win.list_windows() if w.hwnd == window_id), None)
    if current is None or (c.params.get("target_pid") and current.pid != c.params["target_pid"]):
        return Outcome(False, "Target window identity changed during text verification")
    window_title = current.title
    typed_element = c.result.get("typed_element_id")
    if typed_element:
        target = uia.describe_registered(typed_element)
        got = uia.get_value(typed_element)
        matches = (target.get("window_id") == window_id and target.get("window_process_id", target.get("process_id")) == current.pid
                   and got.get("ok") and bool(expected) and expected in (got.get("value") or ""))
        return Outcome(bool(matches), "Exact focused editor contains the requested text" if matches
                       else "Exact focused editor did not verify the requested text",
                       {"element_id": typed_element, "hwnd": window_id, "pid": current.pid})
    if uia.available():
        elements = uia.find_elements(control_type="Document", window_title=window_title,
                                       window_id=window_id, limit=25, depth=32)
        if not elements:
            elements = uia.find_elements(control_type="Edit", window_title=window_title,
                                           window_id=window_id, limit=25, depth=32)
        for el in elements:
            got = uia.get_value(el.element_id)
            if got.get("ok") and expected and expected in (got.get("value") or ""):
                return Outcome(True, "typed text found in the target document",
                               {"window": window_title, "hwnd": window_id,
                                "target_pid": c.params.get("target_pid")})
        return Outcome(False, "typed text not found in the target document",
                       {"window": window_title, "hwnd": window_id,
                        "expected_len": len(expected), "uia_available": True})
    return Outcome(False, "target window text cannot be freshly read through UI Automation",
                   {"window": window_title, "hwnd": window_id,
                    "uia_available": False})


def _verify_workspace(c: VerifyContext) -> Outcome:
    return Outcome(bool(c.result.get("ok", True)), "workspace query completed",
                   {k: v for k, v in (c.result or {}).items() if k != "entries"})


def _verify_process_kill(c: VerifyContext) -> Outcome:
    pid = c.params.get("pid")
    procs = c.after.get("processes") or []
    if pid and all(int(p.get("pid", -1)) != int(pid) for p in procs):
        return Outcome(True, f"process {pid} is gone", {"pid": pid})
    return Outcome(False, f"process {pid} is still running", {"pid": pid})


def _verify_browser(c: VerifyContext) -> Outcome:
    evidence = c.result.get("verify") or {}
    if evidence.get("verified"):
        return Outcome(True, evidence.get("detail", "browser state verified"), evidence)
    return Outcome(False, evidence.get("detail", "browser state not verified"), evidence)


# -------------------------------------------------------------------- registry
REGISTRY: Dict[str, Callable[[VerifyContext], Outcome]] = {
    "application.open": _verify_app_open,
    "application.close": _verify_app_close,
    "window.focus": lambda c: _verify_window_state(c, "focus"),
    "window.minimize": lambda c: _verify_window_state(c, "minimize"),
    "window.minimize_all": _verify_minimize_all,
    "window.maximize": lambda c: _verify_window_state(c, "maximize"),
    "window.restore": lambda c: _verify_window_state(c, "restore"),
    "window.move": _verify_window_moved,
    "window.close": _verify_window_close,
    "system.volume.set": _verify_volume,
    "system.volume.up": lambda c: _verify_volume_relative(c, "up"),
    "system.volume.down": lambda c: _verify_volume_relative(c, "down"),
    "system.volume.mute": _verify_mute,
    "files.write": _verify_file_written,
    "files.append": _verify_file_appended,
    "files.exists": _verify_file_exists,
    "files.delete": _verify_file_gone,
    "files.move": _verify_move,
    "files.copy": _verify_copy,
    "files.mkdir": _verify_mkdir,
    "clipboard.set": _verify_clipboard_set,
    "shell.run": _verify_shell,
    "screen.capture": _verify_capture,
    "screen.capture_window": _verify_capture,
    "uia.invoke": _verify_uia_action,
    "uia.click": _verify_uia_action,
    "uia.set_value": _verify_uia_action,
    "uia.focus": _verify_uia_action,
    "uia.set_toggle": _verify_uia_action,
    "uia.select": _verify_uia_action,
    "input.type_text": _verify_typed_text,
    "input.key": _verify_input_delivered,
    "input.hotkey": _verify_input_delivered,
    "input.click": _verify_input_delivered,
    "input.move": _verify_input_delivered,
    "input.scroll": _verify_input_delivered,
    "process.kill": _verify_process_kill,
    # every browser capability carries its own verification block (Phase 2 + Phase 4)
    # every browser capability carries its own verification block (Phase 2 + Phase 4)
    "browser.session": _verify_browser,
    "browser.navigate": _verify_browser,
    "browser.open_default": _verify_browser,
    "system.settings.open": _verify_browser,
    "browser.open_named": _verify_browser,
    "browser.installed": _verify_browser,
    "browser.click": _verify_browser,
    "browser.type": _verify_browser,
    "browser.extract": _verify_browser,
    "browser.tabs_list": _verify_browser,
    "browser.tab_new": _verify_browser,
    "browser.tab_switch": _verify_browser,
    "browser.tab_close": _verify_browser,
    "browser.wait": _verify_browser,
    "browser.select": _verify_browser,
    "browser.checkbox": _verify_browser,
    "browser.scroll": _verify_browser,
    "browser.reload": _verify_browser,
    "browser.upload": _verify_browser,
    "browser.download": _verify_browser,
    "browser.downloads": _verify_browser,
    "browser.dialog": _verify_browser,
    "browser.history": _verify_browser,
    "browser.accessibility": _verify_browser,
    "browser.cookies": _verify_browser,
    "browser.leases": _verify_browser,
    # media (search + verified playback) and fullscreen reuse the same evidence
    # contract: they carry a verify block with a real observation.
    "browser.media.play": _verify_browser,
    "browser.fullscreen": _verify_browser,
    "browser.observe": _verify_browser,
    "browser.detect_gate": _verify_browser,
    "browser.act": _verify_browser,
    "browser.fill": _verify_browser,
    "browser.verify": _verify_browser,
    "browser.chatgpt.image": _verify_browser,
    "browser.website_task": _verify_browser,
    "workspace.list": _verify_workspace,
    "workspace.usage": _verify_workspace,
    # --- Desktop Awareness (Phase 1 extension) ---
    "desktop.pause": lambda c: Outcome(bool(c.result.get("ok")),
                                         c.result.get("detail", "paused")),
    "desktop.resume": lambda c: Outcome(bool(c.result.get("ok")),
                                          c.result.get("detail", "resumed")),
    "desktop.settings.set": lambda c: Outcome(
        bool(c.result.get("ok")),
        f"settings updated: {c.result.get('updated', [])}"),
}


def _verify_workspace_claim(c: VerifyContext) -> Outcome:
    r = c.result or {}
    if not r.get("ok"):
        return Outcome(False, f"mission claim failed: {r.get('error')}", {})
    path = r.get("path")
    if not path or not Path(path).is_dir():
        return Outcome(False, f"claimed mission directory missing: {path}", {})
    return Outcome(True, f"mission workspace ready at {Path(path).name}", {"path": path})


def _verify_workspace_release(c: VerifyContext) -> Outcome:
    r = c.result or {}
    if not r.get("ok"):
        return Outcome(False, f"release failed: {r.get('error')}", {})
    if c.params.get("purge") and r.get("purged"):
        return Outcome(True, f"mission workspace released and purged "
                             f"({r.get('bytes_freed', 0)} B freed)", {})
    return Outcome(True, "mission workspace released", {})


def _verify_workspace_quota_set(c: VerifyContext) -> Outcome:
    r = c.result or {}
    if not r.get("ok"):
        return Outcome(False, f"quota update failed: {r.get('error')}", {})
    want = c.params.get("cap_bytes")
    if want is not None and r.get("cap_bytes") != want:
        return Outcome(False, f"quota not applied: asked {want}, got {r.get('cap_bytes')}", {})
    return Outcome(True, f"quota cap now {r.get('cap_bytes')}", {})


def _verify_workspace_cleanup(c: VerifyContext) -> Outcome:
    r = c.result or {}
    if not r.get("ok"):
        return Outcome(False, f"cleanup failed: {r.get('error')}", {})
    dry = bool(r.get("dry_run"))
    return Outcome(True, f"cleanup {'reported' if dry else 'removed'} "
                         f"{r.get('removed', 0)} item(s)", {"removed": r.get("removed", 0)})


def _verify_workspace_shell(c: VerifyContext) -> Outcome:
    r = c.result or {}
    if not r.get("ok"):
        detail = r.get("error") or (r.get("stderr") or "").strip()[:160]
        return Outcome(False, f"workspace shell failed: {detail}", {})
    return Outcome(True, f"command exited {r.get('returncode')} inside the workspace",
                   {"returncode": r.get("returncode")})

# ---- GENIE Workspace / background computer (§11C) ----
REGISTRY.update({
    "message.prepare": lambda c: Outcome(c.result.get("verified") is True and c.result.get("status") == "drafted", c.result.get("detail", "Draft not verified")),
    "message.send": lambda c: Outcome(c.result.get("verified") is True and c.result.get("status") == "submitted", c.result.get("detail", "Submission not verified")),
    "desktop.visual_click": lambda c: Outcome(c.result.get("verified") is True, c.result.get("detail", "Visual outcome not verified")),
    "desktop.visual_action": lambda c: Outcome(c.result.get("verified") is True, c.result.get("detail", "Visual outcome not verified")),
    "workspace.mission_claim": _verify_workspace_claim,
    "workspace.mission_release": _verify_workspace_release,
    "workspace.set_quota": _verify_workspace_quota_set,
    "workspace.cleanup": _verify_workspace_cleanup,
    "workspace.shell": _verify_workspace_shell,
})

# Capabilities that only read state: nothing to verify beyond "the call succeeded"
READ_ONLY = {
    "desktop.visual_observe",
    "files.locations", "uia.state",
    "system.audio.devices", "system.network.state",
    "application.list", "application.resolve", "apps.discover", "window.list",
    "app.context.status", "app.context.bind",
    "processes.list", "files.read", "files.list", "files.find", "files.disk_usage",
    "clipboard.get", "system.audio.state", "uia.windows", "uia.find", "uia.get_value",
    "uia.tree", "input.state", "workspace.info", "shell.powershell_json", "browser.dom_query",
    "browser.tabs", "browser.screenshot", "screen.state", "computer.state", "desktop.lock_state",
    # §11C workspace read-only
    "workspace.identity", "workspace.missions", "workspace.quota", "workspace.session_dir",
    # --- Desktop Awareness read-only ---
    "desktop.observe", "desktop.observe_display", "desktop.capture_display",
    "desktop.list_displays", "desktop.settings.get",
}


def verify(capability: str, params: Dict[str, Any], result: Dict[str, Any],
           before: Dict[str, Any], after: Dict[str, Any]) -> Outcome:
    if capability in READ_ONLY:
        ok = bool(result.get("ok", True))
        return Outcome(ok, "read-only capability returned successfully" if ok
                       else f"read failed: {result.get('error')}", {})
    fn = REGISTRY.get(capability)
    if fn is None:
        # unknown capability: never assume success
        return Outcome(False, f"no verifier registered for {capability} "
                              f"(unverified actions cannot complete a mission)")
    try:
        return fn(VerifyContext(capability=capability, params=params, result=result,
                                before=before, after=after))
    except Exception as exc:
        log.error("verifier error for %s: %s", capability, exc)
        return Outcome(False, f"verifier raised: {exc}")


def has_verifier(capability: str) -> bool:
    return capability in REGISTRY or capability in READ_ONLY
