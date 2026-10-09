"""Windows UI Automation (computer/uia).

Semantic UI control — find a control by name / control type / automation id and invoke it,
instead of clicking screen coordinates. This sits above browser DOM and below vision in the
automation priority chain.

Implementation: optional, lazily imported provider.
  * preferred : `pywinauto` (UIA backend) — installed on demand, never required by the core
  * fallback  : unavailable -> the planner degrades to keyboard/accelerators, then raw input

Elements are exposed as JSON-safe ids; the live wrappers are held in a short-lived registry
so nothing un-serialisable leaks into contracts or the audit log.
"""
from __future__ import annotations

import threading
import time
import queue
from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from functools import wraps
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("computer.uia")

ELEMENT_TTL_S = 120


@dataclass
class UIElement:
    element_id: str
    name: str = ""
    control_type: str = ""
    automation_id: str = ""
    class_name: str = ""
    window: str = ""
    enabled: bool = True
    rect: List[int] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    window_id: int = 0
    process_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"element_id": self.element_id, "name": self.name,
                "control_type": self.control_type, "automation_id": self.automation_id,
                "class_name": self.class_name, "window": self.window,
                "enabled": self.enabled, "rect": self.rect, "patterns": self.patterns,
                "state": self.state, "window_id": self.window_id, "process_id": self.process_id}


class _Registry:
    def __init__(self) -> None:
        self._items: Dict[str, tuple[Any, float, tuple]] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def put(self, wrapper: Any) -> str:
        with self._lock:
            self._seq += 1
            eid = f"uia_{self._seq}"
            self._items[eid] = (wrapper, time.time(), self._identity(wrapper))
            self._evict()
            return eid

    def get(self, element_id: str) -> Optional[Any]:
        with self._lock:
            entry = self._items.get(element_id)
            if not entry:
                return None
            wrapper, _ts, identity = entry
            if time.time() - _ts > ELEMENT_TTL_S:
                self._items.pop(element_id, None)
                return None
            try:
                if self._identity(wrapper) != identity or not wrapper.is_visible():
                    self._items.pop(element_id, None)
                    return None
            except Exception:
                self._items.pop(element_id, None)
                return None
            self._items[element_id] = (wrapper, time.time(), identity)
            return wrapper

    @staticmethod
    def _identity(wrapper):
        info = wrapper.element_info
        return (int(wrapper.top_level_parent().handle), int(getattr(info, "process_id", 0) or 0),
                tuple(getattr(info, "runtime_id", ()) or ()), str(getattr(info, "control_type", "")),
                str(getattr(info, "automation_id", "")))

    def _evict(self) -> None:
        now = time.time()
        for key, (_w, ts, _identity) in list(self._items.items()):
            if now - ts > ELEMENT_TTL_S:
                self._items.pop(key, None)
        if len(self._items) > 500:
            for key in list(self._items.keys())[:100]:
                self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_REGISTRY = _Registry()
_BACKEND: Optional[str] = None
_IMPORT_ERROR = ""
_BACKEND_LOCK = threading.RLock()
_LAST_ERROR = threading.local()


class UIAOperationTimeout(RuntimeError):
    action_may_have_run = True


class _UIAWorker:
    """Keep pywinauto's COM singleton and element wrappers on one living thread."""
    def __init__(self):
        self.jobs = queue.Queue()
        self.lock = threading.Lock()
        self.thread = None

    def _serve(self):
        com = None
        init_error = None
        try:
            try:
                import pythoncom
            except ImportError:
                pythoncom = None
            if pythoncom is not None:
                pythoncom.CoInitializeEx(0)  # MTA; UIA calls do not need a UI message pump.
                com = pythoncom
        except Exception as exc:
            init_error = exc
        try:
            while True:
                item = self.jobs.get()
                if item is None:
                    return
                future, fn, args, kwargs = item
                if not future.set_running_or_notify_cancel():
                    continue
                _LAST_ERROR.value = {}
                try:
                    if init_error:
                        raise init_error
                    value = fn(*args, **kwargs)
                    future.set_result((value, last_error()))
                except BaseException as exc:
                    future.set_exception(exc)
        finally:
            if com is not None:
                com.CoUninitialize()

    def call(self, fn, args, kwargs):
        if threading.current_thread() is self.thread:
            return fn(*args, **kwargs)
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._serve, name="GENIE-UIA", daemon=True)
                self.thread.start()
        future = Future()
        self.jobs.put((future, fn, args, kwargs))
        try:
            value, error = future.result(timeout=30)
        except FutureTimeout as exc:
            queued = future.cancel()
            message = ("UI Automation request timed out before execution." if queued else
                       "UI Automation timed out during execution; inspect the current state before retrying.")
            raise UIAOperationTimeout(message) from exc
        _LAST_ERROR.value = error
        return value

    def close(self):
        if self.thread is not None:
            self.jobs.put(None)
            self.thread.join(timeout=2)


_WORKER = _UIAWorker()


def _com_sta(fn):
    """Run UIA on its persistent COM worker, including reentrant helper calls."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return _WORKER.call(fn, args, kwargs)
    return wrapped


@_com_sta
def available() -> bool:
    global _BACKEND, _IMPORT_ERROR
    with _BACKEND_LOCK:
        if _BACKEND == "pywinauto":
            return True
        if _BACKEND == "unavailable":
            if "coinitialize has not been called" not in _IMPORT_ERROR.casefold():
                return False
            # A transient COM-apartment failure must not disable UIA for the
            # lifetime of the daemon; this call is now inside the STA wrapper.
            _BACKEND = None
            _IMPORT_ERROR = ""
        try:
            import pywinauto  # noqa: F401
            _BACKEND = "pywinauto"
        except Exception as exc:
            _BACKEND = "unavailable"
            _IMPORT_ERROR = str(exc)
        return _BACKEND != "unavailable"


@_com_sta
def status() -> Dict[str, Any]:
    return {"available": available(), "backend": _BACKEND or "unknown",
            "error": _IMPORT_ERROR, "cached_elements": len(_REGISTRY._items)}


def _error_code(exc: BaseException) -> str:
    text = str(exc).casefold()
    code = getattr(exc, "winerror", None) or getattr(exc, "hresult", None)
    if code in (5, -2147024891, 0x80070005) or "access is denied" in text or "access denied" in text:
        return "access_denied"
    if "element not found" in text or "window not found" in text:
        return "window_not_found"
    return "uia_unavailable"


def last_error() -> Dict[str, str]:
    """Per-request UIA failure detail; never report access denial as an empty tree."""
    return dict(getattr(_LAST_ERROR, "value", {}) or {})


@_com_sta
def password_rectangles(window_id: int) -> List[List[int]]:
    """Best-effort additional masking; window exclusions still govern unavailable UIA."""
    if not available():
        return []
    root = _desktop().window(handle=window_id).wrapper_object()
    rects = []
    for wrapper in _bounded_descendants(root, 32, budget_s=2):
        if getattr(wrapper.element_info, "is_password", False) and wrapper.is_visible():
            r = wrapper.rectangle()
            rects.append([r.left, r.top, r.right, r.bottom])
    return rects


@_com_sta
def describe_registered(element_id: str, *, allow_disabled: bool = False) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None:
        return {}
    try:
        if not wrapper.is_visible() or (not allow_disabled and not wrapper.is_enabled()) or getattr(wrapper.element_info, "is_password", False):
            return {}
        info = wrapper.element_info
        return {"name": info.name or "", "window": wrapper.top_level_parent().window_text(),
                "window_id": int(wrapper.top_level_parent().handle),
                "window_process_id": int(getattr(wrapper.top_level_parent().element_info, "process_id", 0) or 0),
                "automation_id": str(getattr(info, "automation_id", "") or ""),
                "process_id": int(getattr(info, "process_id", 0) or 0)}
    except Exception:
        return {}


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _describe(wrapper, window_title: str = "") -> UIElement:
    info = wrapper.element_info
    rect: List[int] = []
    try:
        r = wrapper.rectangle()
        rect = [r.left, r.top, r.right, r.bottom]
    except Exception:
        pass
    patterns: List[str] = []
    try:
        if wrapper.is_enabled():
            patterns.append("enabled")
    except Exception:
        pass
    control_state = _read_state(wrapper)
    patterns.extend(control_state.pop("patterns"))
    return UIElement(
        element_id=_REGISTRY.put(wrapper),
        name=(info.name or "")[:200],
        control_type=str(getattr(info, "control_type", "") or ""),
        automation_id=str(getattr(info, "automation_id", "") or ""),
        class_name=str(getattr(info, "class_name", "") or ""),
        window=window_title[:120],
        enabled=bool(getattr(info, "enabled", True)),
        rect=rect,
        patterns=patterns,
        state=control_state,
        window_id=int(wrapper.top_level_parent().handle),
        process_id=int(getattr(info, "process_id", 0) or 0),
    )


def _read_state(wrapper) -> Dict[str, Any]:
    result: Dict[str, Any] = {"patterns": []}
    try:
        result["focused"] = bool(wrapper.has_keyboard_focus())
    except Exception:
        pass
    for pattern, attr, key in (("toggle", "iface_toggle", "toggle_state"),
                               ("selection", "iface_selection_item", "selected")):
        try:
            interface = getattr(wrapper, attr)
            value = (int(interface.CurrentToggleState) if pattern == "toggle"
                     else bool(interface.CurrentIsSelected))
            result["patterns"].append(pattern)
            result[key] = value
        except Exception:
            pass
    return result


@_com_sta
def control_state(element_id: str) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None:
        return {"ok": False, "error": "element handle expired or unknown"}
    return {"ok": True, "element_id": element_id, **_read_state(wrapper)}


# ------------------------------------------------------------------ enumeration
@_com_sta
def windows(visible_only: bool = True) -> List[Dict[str, Any]]:
    _LAST_ERROR.value = {}
    if not available():
        _LAST_ERROR.value = {"error_code": "uia_unavailable", "error": _IMPORT_ERROR or "UI Automation provider unavailable"}
        return []
    try:
        out = []
        for w in _desktop().windows():
            try:
                info = w.element_info
                if visible_only and not info.visible:
                    continue
                out.append({"name": (info.name or "")[:120],
                            "control_type": str(getattr(info, "control_type", "")),
                            "automation_id": str(getattr(info, "automation_id", "")),
                            "class_name": str(getattr(info, "class_name", "")),
                            "visible": bool(info.visible),
                            "enabled": bool(info.enabled)})
            except Exception:
                continue
        return out
    except Exception as exc:
        _LAST_ERROR.value = {"error_code": _error_code(exc), "error": str(exc)[:300]}
        log.debug("uia window enumeration failed: %s", exc)
        return []


#: decorations an application may add to its own title as a result of an action
_TITLE_NOISE = "*—-· \t"


def _title_matches(query: str, actual: str) -> bool:
    """True if `query` identifies the window named `actual`.

    Tolerates decoration that applications add or remove dynamically (Notepad's
    leading '*' for an unsaved document, en/em dashes, stray whitespace) so an
    action can still be verified after the title changes.
    """
    q = (query or "").strip(_TITLE_NOISE).lower()
    a = (actual or "").strip(_TITLE_NOISE).lower()
    if not q:
        return False
    if q in a:
        return True
    # compare on the significant characters only, ignoring decoration anywhere
    strip = dict.fromkeys(map(ord, _TITLE_NOISE))
    return q.translate(strip) in a.translate(strip)


def _window_root(desktop, title, window_id=0):
    # Resolve HWNDs without asking unrelated applications' UIA providers.
    from computer import windows_api as win
    metadata = {}
    if win.IS_WINDOWS:
        observed = win.list_windows()
        windows = [(w.title, w.hwnd) for w in observed]
        metadata = {w.hwnd: {"process": getattr(w, "process", ""),
                             "pid": getattr(w, "pid", 0),
                             "class_name": getattr(w, "class_name", "")}
                    for w in observed}
    else:
        windows = []
        for wrapper in desktop.windows():
            try:
                windows.append((wrapper.element_info.name or "", wrapper))
            except Exception:
                continue
    candidates = [(name, target) for name, target in windows if _title_matches(title, name)]
    if window_id:
        candidates = [(name, target) for name, target in windows if target == window_id
                      and (not title or _title_matches(title, name))]
    exact = [(name, target) for name, target in candidates if name.strip().casefold() == title.strip().casefold()]
    candidates = exact or candidates
    if not candidates and not window_id:
        app = title.rsplit(" - ", 1)[-1].strip()
        if app and app != title.strip():
            candidates = [(name, target) for name, target in windows if _title_matches(app, name)]
    if len(candidates) != 1:
        _LAST_ERROR.value = {
            "error_code": "ambiguous_window" if candidates else "window_not_found",
            "error": "Multiple windows share this title. Choose a window_id from desktop_windows." if candidates else "The requested visible window was not found.",
            "candidates": [{"window": name, "window_id": target, **metadata.get(target, {})}
                           for name, target in candidates] if win.IS_WINDOWS else []}
        return None
    target = candidates[0][1]
    return desktop.window(handle=target).wrapper_object() if win.IS_WINDOWS else target


def _bounded_descendants(root, depth, max_nodes=1500, budget_s=10):
    # pywinauto.descendants(depth=N) collects the ENTIRE subtree before applying
    # its depth filter. Walk immediate children instead so limits actually bound work.
    pending = deque([(root, 0)])
    deadline = time.monotonic() + budget_s
    visited = 0
    seen = set()
    while pending:
        if time.monotonic() >= deadline or visited >= max_nodes:
            _LAST_ERROR.value = {"error_code": "uia_scan_incomplete",
                                 "error": "Control scan reached its budget; narrow the search or inspect again."}
            return
        parent, level = pending.popleft()
        if level >= depth:
            continue
        try:
            children = parent.children()
        except Exception as exc:
            _LAST_ERROR.value = {"error_code": _error_code(exc), "error": str(exc)[:300]}
            continue
        for child in children:
            if visited >= max_nodes or time.monotonic() >= deadline:
                _LAST_ERROR.value = {"error_code": "uia_scan_incomplete", "error": "Control scan reached its budget."}
                return
            try:
                identity = tuple(child.element_info.runtime_id)
            except (AttributeError, TypeError):
                identity = (id(child),)
            except Exception:
                continue
            if identity in seen:
                continue
            seen.add(identity)
            visited += 1
            yield child
            pending.append((child, level + 1))


@_com_sta
def find_elements(name: str = "", control_type: str = "", automation_id: str = "",
                  window_title: str = "", limit: int = 25, depth: int = 4,
                  window_id: int = 0) -> List[UIElement]:
    """Semantic element search inside a window (or the whole desktop)."""
    _LAST_ERROR.value = {}
    if not available():
        _LAST_ERROR.value = {"error_code": "uia_unavailable", "error": _IMPORT_ERROR or "UI Automation provider unavailable"}
        return []
    try:
        desktop = _desktop()
        if window_title or window_id:
            root = _window_root(desktop, window_title, window_id)
            if root is None:
                return []
        else:
            root = desktop

        found: List[UIElement] = []
        denied_error = None
        for wrapper in _bounded_descendants(root, max(1, min(32, depth))):
            try:
                info = wrapper.element_info
                if getattr(info, "is_password", False) or not wrapper.is_visible():
                    continue
                if name and name.lower() not in (info.name or "").lower():
                    continue
                if control_type and control_type.lower() != str(
                        getattr(info, "control_type", "")).lower():
                    continue
                if automation_id and automation_id != str(getattr(info, "automation_id", "")):
                    continue
                found.append(_describe(wrapper, window_title))
                if len(found) >= max(1, min(100, limit)):
                    break
            except Exception as exc:
                if _error_code(exc) == "access_denied":
                    denied_error = exc
                continue
        if not found and denied_error is not None:
            _LAST_ERROR.value = {"error_code": "access_denied", "error": str(denied_error)[:300]}
        return found
    except Exception as exc:
        _LAST_ERROR.value = {"error_code": _error_code(exc), "error": str(exc)[:300]}
        log.debug("uia find_elements failed: %s", exc)
        return []


# ---------------------------------------------------------------------- actions
@_com_sta
def set_toggle(element_id: str, enabled: bool) -> Dict[str, Any]:
    """Set an absolute state so retries cannot invert a successful toggle."""
    if type(enabled) is not bool:
        return {"ok": False, "error": "enabled must be boolean"}
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None or not describe_registered(element_id):
        return {"ok": False, "error": "Observe an enabled visible control again."}
    try:
        toggle = wrapper.iface_toggle
        before = int(toggle.CurrentToggleState)
        if before not in (0, 1):
            return {"ok": False, "error": "Indeterminate toggle requires an explicit selection."}
        if before != int(enabled):
            toggle.Toggle()
        actual = int(toggle.CurrentToggleState)
        return {"ok": actual == int(enabled), "element_id": element_id,
                "action": "set_toggle", "before": before, "toggle_state": actual,
                "error": "" if actual == int(enabled) else "Requested toggle state not observed."}
    except Exception as exc:
        return {"ok": False, "error": f"Toggle pattern unavailable: {exc}"}


@_com_sta
def select_element(element_id: str) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None or not describe_registered(element_id):
        return {"ok": False, "error": "Observe an enabled visible control again."}
    try:
        selection = wrapper.iface_selection_item
        if not selection.CurrentIsSelected:
            selection.Select()
        selected = bool(selection.CurrentIsSelected)
        return {"ok": selected, "element_id": element_id, "action": "select",
                "selected": selected, "error": "" if selected else "Selection not observed."}
    except Exception as exc:
        return {"ok": False, "error": f"Selection pattern unavailable: {exc}"}


@_com_sta
def invoke(element_id: str) -> Dict[str, Any]:
    # Select the supported dispatch path before acting. A COM error after
    # invocation cannot establish that the action did not reach the control.
    return invoke_once(element_id)


@_com_sta
def invoke_once(element_id: str) -> Dict[str, Any]:
    """Consequential actions must never retry after an ambiguous COM failure."""
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None or not describe_registered(element_id):
        return {"ok": False, "error_code": "stale_element", "error": "Observe again."}
    try:
        pattern = wrapper.iface_invoke
    except Exception:
        # Choose the observed button's native click before any invocation.
        # Never use this as a retry after an Invoke that may have succeeded.
        if str(getattr(wrapper.element_info, "control_type", "")) != "Button":
            return {"ok": False, "error_code": "invoke_pattern_unavailable",
                    "error": "This control has no verified button invocation path."}
        try:
            wrapper.click_input()
            return {"ok": True, "action": "click_once", "element_id": element_id}
        except Exception:
            return {"ok": False, "error_code": "action_outcome_unknown", "action_may_have_run": True,
                    "error": "Single button click outcome unknown. Observe; do not resend."}
    try:
        pattern.Invoke()
        return {"ok": True, "action": "invoke_once", "element_id": element_id}
    except Exception:
        return {"ok": False, "error_code": "action_outcome_unknown",
                "action_may_have_run": True, "error": "Invocation outcome unknown. Observe; do not resend."}


@_com_sta
def set_value(element_id: str, value: str) -> Dict[str, Any]:
    """Set a control's value using whichever pattern the control actually supports.

    Chain (first that works wins): UIA ValuePattern -> legacy set_text -> focus + type_keys.
    The method used is reported so verification stays honest.
    """
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None or not describe_registered(element_id):
        return {"ok": False, "error_code": "stale_element", "error": "Observe the visible editable control again."}
    if str(getattr(wrapper.element_info, "control_type", "")) not in ("Edit", "Document", "ComboBox"):
        return {"ok": False, "error_code": "control_not_editable", "error": "Selected control is not an editor."}
    attempts: List[str] = []

    # 1. UIA ValuePattern
    try:
        wrapper.iface_value.SetValue(value)
        actual = get_value(element_id)
        if actual.get("value") == value:
            return {"ok": True, "action": "set_value", "method": "value_pattern",
                    "requested": value, "actual": actual.get("value"), "verified": True}
        attempts.append(f"value_pattern wrote {actual.get('value')!r}")
    except Exception as exc:
        attempts.append(f"value_pattern: {exc}")

    # 2. pywinauto text setter
    for method in ("set_text", "set_edit_text"):
        fn = getattr(wrapper, method, None)
        if fn is None:
            continue
        try:
            fn(value)
            actual = get_value(element_id)
            if actual.get("value") == value:
                return {"ok": True, "action": "set_value", "method": method,
                        "requested": value, "actual": actual.get("value"), "verified": True}
            attempts.append(f"{method} wrote {actual.get('value')!r}")
        except Exception as exc:
            attempts.append(f"{method}: {exc}")

    # 3. focus + keystrokes
    try:
        wrapper.set_focus()
        if not wrapper.has_keyboard_focus():
            return {"ok": False, "error_code": "target_focus_failed", "error": "Editor did not obtain keyboard focus; no keys sent."}
        wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
        from .input import type_text
        entered = type_text(value)
        if not entered.get("ok"):
            return {"ok": False, "error_code": "action_outcome_unknown", "action_may_have_run": True,
                    "error": "Input delivery was incomplete; inspect the draft before retrying."}
        actual = get_value(element_id)
        if actual.get("value") == value:
            return {"ok": True, "action": "set_value", "method": "type_keys",
                    "requested": value, "actual": actual.get("value"), "verified": True}
        attempts.append(f"type_keys wrote {actual.get('value')!r}")
    except Exception as exc:
        attempts.append(f"type_keys: {exc}")
    return {"ok": False, "error_code": "action_outcome_unknown", "action_may_have_run": True,
            "error": "Editor replacement could not be read back: " + " | ".join(attempts)}


@_com_sta
def get_value(element_id: str) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None:
        return {"ok": False, "error": "element handle expired or unknown"}
    try:
        return {"ok": True, "value": wrapper.get_value()}
    except Exception:
        try:
            return {"ok": True, "value": wrapper.iface_text.DocumentRange.GetText(-1), "method": "text_pattern"}
        except Exception:
            pass
        if str(getattr(wrapper.element_info, "control_type", "")) in ("Document", "Edit"):
            return {"ok": False, "error_code": "text_readback_unavailable",
                    "error": "Editor exposes neither ValuePattern nor TextPattern; its caption is not document text."}
        try:
            return {"ok": True, "value": wrapper.window_text()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


@_com_sta
def focus_element(element_id: str) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None or not describe_registered(element_id):
        return {"ok": False, "error": "element handle expired or unknown"}
    try:
        wrapper.set_focus()
        focused = bool(wrapper.has_keyboard_focus())
        return {"ok": focused, "action": "focus", "focused": focused,
                "error": "" if focused else "Keyboard focus did not reach the requested control."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@_com_sta
def focused_editor(window_id: int, title: str = "") -> Dict[str, Any]:
    """Identify the actual focused editor, never the first text field in a window."""
    if not available():
        return {"ok": False, "error": "UI Automation unavailable; use authorized visual interaction."}
    root = _window_root(_desktop(), title, window_id)
    if root is None:
        return {"ok": False, **last_error()}
    for wrapper in _bounded_descendants(root, 32):
        try:
            if (str(wrapper.element_info.control_type) in ("Edit", "Document")
                    and wrapper.has_keyboard_focus() and wrapper.is_visible()
                    and wrapper.is_enabled() and not getattr(wrapper.element_info, "is_password", False)):
                return {"ok": True, **_describe(wrapper, title).to_dict()}
        except Exception:
            continue
    return {"ok": False, "error_code": "target_focus_failed",
            "error": "No observed editor has keyboard focus. Focus the exact document control before typing."}


@_com_sta
def click_element(element_id: str) -> Dict[str, Any]:
    wrapper = _REGISTRY.get(element_id)
    if wrapper is None:
        return {"ok": False, "error": "element handle expired or unknown"}
    try:
        wrapper.click_input()
        return {"ok": True, "action": "click_input"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@_com_sta
def dump_tree(window_title: str, depth: int = 3, limit: int = 120) -> Dict[str, Any]:
    """Accessibility-tree dump for diagnostics and semantic targeting."""
    if not available():
        return {"ok": False, "error": "uia unavailable"}
    try:
        cands = [w for w in _desktop().windows()
                 if window_title.lower() in (w.element_info.name or "").lower()]
        if not cands:
            return {"ok": False, "error": "window not found"}
        root = cands[0]
        rows = []
        for w in root.descendants(depth=depth):
            try:
                info = w.element_info
                rows.append({"name": (info.name or "")[:80],
                             "type": str(getattr(info, "control_type", "")),
                             "automation_id": str(getattr(info, "automation_id", "")),
                             "enabled": bool(info.enabled)})
            except Exception:
                continue
            if len(rows) >= limit:
                break
        return {"ok": True, "window": window_title, "count": len(rows), "elements": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def clear_registry() -> None:
    _REGISTRY.clear()
