"""Teaching recorder (teaching/recorder.py).

Records a user demonstration **semantically**, not as a macro:

    recorded:  app = Notepad, element.role = menu_item, element.name = Save
    not:       mouse click (844, 512)

Raw coordinates are kept only as fallback/debug evidence. Values that are task-specific are
turned into variables, and secrets/passwords are **never** recorded — sensitive fields are
redacted at capture time, not at export time.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.contracts import new_id
from core.logging_setup import get_logger

log = get_logger("teaching.recorder")

SENSITIVE_FIELD_HINTS = ("password", "passwd", "pwd", "pin", "secret", "token", "apikey",
                         "api_key", "credential", "cvv", "card", "otp", "private")
SENSITIVE_VALUE_PATTERNS = [
    (r"\b\d{13,19}\b", "[redacted-card]"),
    (r"\b(?:sk|pk|ghp|xoxb)-[A-Za-z0-9_\-]{8,}", "[redacted-token]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[redacted-email]"),
    (r"(?i)\b(password|passwd|pwd|pin|token|secret)\b\s*[:=]\s*\S+", r"\1=[redacted]"),
]

# Values that look task-specific and should become variables
PATH_RE = re.compile(r"([A-Za-z]:\\[^\s]+|/[^\s]+|\\\\[^\s]+)")
NUMERIC_RE = re.compile(r"^\d+(\.\d+)?$")


@dataclass
class TeachingEvent:
    kind: str
    ts: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)
    redacted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ts": self.ts, "redacted": self.redacted, **self.data}


class TeachingRecorder:
    def __init__(self, *, audit=None, max_events: int = 2000):
        self.audit = audit
        self.max_events = max_events
        self.events: List[TeachingEvent] = []
        self.recording = False
        self.started_at = 0.0
        self.stopped_at = 0.0
        self.application = ""
        self.goal = ""
        self.before_state: Dict[str, Any] = {}
        self.after_state: Dict[str, Any] = {}
        self.stats = {"events": 0, "redacted": 0, "raw_coordinates": 0}

    # ------------------------------------------------------------------ control
    def start(self, *, goal: str = "", application: str = "") -> Dict[str, Any]:
        self.events = []
        self.recording = True
        self.started_at = time.time()
        self.stopped_at = 0.0
        self.goal = goal
        self.application = application or self._foreground_app()
        self.before_state = self._snapshot()
        self.stats = {"events": 0, "redacted": 0, "raw_coordinates": 0}
        if self.audit:
            self.audit.record(who="owner", action="teaching.start", why=goal[:120],
                              result=f"app={self.application}")
        log.info("teaching recording started (app=%s goal=%s)", self.application, goal)
        return {"ok": True, "started_at": self.started_at, "application": self.application}

    def stop(self) -> Dict[str, Any]:
        self.recording = False
        self.stopped_at = time.time()
        self.after_state = self._snapshot()
        if self.audit:
            self.audit.record(who="owner", action="teaching.stop",
                              why=self.goal[:120], result=f"{len(self.events)} events")
        return {"ok": True, "events": len(self.events),
                "duration_s": round(self.stopped_at - self.started_at, 2),
                "stats": dict(self.stats)}

    # ------------------------------------------------------------------ capture
    def record(self, kind: str, **data: Any) -> Optional[Dict[str, Any]]:
        if not self.recording:
            return None
        if len(self.events) >= self.max_events:
            log.warning("teaching recorder reached its event limit")
            return None
        clean, redacted = self._redact(data)
        event = TeachingEvent(kind=kind, data=clean, redacted=redacted)
        self.events.append(event)
        self.stats["events"] += 1
        if redacted:
            self.stats["redacted"] += 1
        return event.to_dict()

    # ---- semantic helpers (what GENIE should prefer) -------------------------
    def note_app_launch(self, target: str, window_hint: str = "") -> Optional[Dict[str, Any]]:
        return self.record("app_launch", target=target, window_hint=window_hint or target)

    def note_uia_invoke(self, window: str, element_name: str, control_type: str = "",
                        role: str = "") -> Optional[Dict[str, Any]]:
        return self.record("uia_invoke", window=window, element_name=element_name,
                           control_type=control_type or role,
                           wait_for={"condition": "element_visible",
                                     "selector": f"[name='{element_name}']", "timeout_s": 10})

    def note_typing(self, text: str, target_field: str = "", semantic: bool = True) -> Optional[Dict[str, Any]]:
        return self.record("typing", text=text, target_field=target_field, semantic=semantic)

    def note_file_save(self, path: str, content: str = "") -> Optional[Dict[str, Any]]:
        return self.record("file_save", path=path, content=content)

    def note_keystroke(self, chord: str, semantic: bool = False, description: str = "") -> Optional[Dict[str, Any]]:
        return self.record("keystroke", chord=chord, semantic=semantic,
                           description=description or chord)

    def note_browser_action(self, capability: str, params: Dict[str, Any],
                            description: str = "") -> Optional[Dict[str, Any]]:
        return self.record("browser_action", capability=capability, params=params,
                           description=description or capability)

    def note_plugin_action(self, capability: str, params: Dict[str, Any],
                           description: str = "") -> Optional[Dict[str, Any]]:
        return self.record("plugin_action", capability=capability, params=params,
                           description=description or capability)

    def note_mouse(self, x: int, y: int, target: str = "") -> Optional[Dict[str, Any]]:
        """Raw coordinates: fallback/debug evidence only (§5.11)."""
        self.stats["raw_coordinates"] += 1
        return self.record("mouse", x=x, y=y, target=target,
                           note="raw coordinates are debug evidence, not skill logic")

    # ------------------------------------------------------------------ redaction
    # Keys whose VALUE names the UI field being written to. If that field is sensitive, the
    # payload written into it must be redacted even though the payload key ("text") is not
    # itself sensitive (A-056). Locator keys (selector/element_name) are deliberately NOT
    # included: a CSS selector like "#card" is a location, not a sensitive field name.
    TARGET_FIELD_KEYS = ("target_field", "field", "field_name")
    PAYLOAD_KEYS = ("text", "value", "content", "params", "typed")
    # Stricter than SENSITIVE_FIELD_HINTS: "card" alone would match an innocent locator.
    SENSITIVE_TARGET_HINTS = ("password", "passwd", "pwd", "pin", "secret", "token",
                              "apikey", "api_key", "credential", "cvv", "otp",
                              "card_number", "cardnumber", "private_key")

    @classmethod
    def _is_sensitive_target(cls, name: str) -> bool:
        lowered = (name or "").lower()
        return any(hint in lowered for hint in cls.SENSITIVE_TARGET_HINTS)

    def _redact(self, data: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        redacted = False
        # A sensitive target field taints the payload regardless of the payload's own key.
        tainted = any(self._is_sensitive_target(str(data.get(key) or ""))
                      for key in self.TARGET_FIELD_KEYS)
        clean: Dict[str, Any] = {}
        for key, value in data.items():
            if any(hint in key.lower() for hint in SENSITIVE_FIELD_HINTS):
                clean[key] = "[redacted]"
                redacted = True
                continue
            if tainted and key in self.PAYLOAD_KEYS:
                clean[key] = "[redacted]"
                redacted = True
                continue
            if isinstance(value, str):
                text = value
                for pattern, replacement in SENSITIVE_VALUE_PATTERNS:
                    if re.search(pattern, text):
                        text = re.sub(pattern, replacement, text)
                        redacted = True
                clean[key] = text
            elif isinstance(value, dict):
                nested, nested_redacted = self._redact(value)
                clean[key] = nested
                redacted = redacted or nested_redacted
            else:
                clean[key] = value
        return clean, redacted

    @staticmethod
    def is_sensitive_field(field_name: str) -> bool:
        return any(hint in (field_name or "").lower() for hint in SENSITIVE_FIELD_HINTS)

    # -------------------------------------------------------------------- state
    def _snapshot(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {"ts": time.time()}
        try:
            from computer import windows_api as win
            foreground = win.foreground_window()
            state["foreground"] = foreground.title if foreground else ""
            state["foreground_process"] = foreground.process if foreground else ""
            state["windows"] = len(win.list_windows())
            monitors = win.list_monitors()
            if monitors:
                rect = monitors[0].rect
                state["screen"] = f"{rect[2] - rect[0]}x{rect[3] - rect[1]}"
        except Exception as exc:
            log.debug("state snapshot failed: %s", exc)
        return state

    def _foreground_app(self) -> str:
        try:
            from computer import windows_api as win
            foreground = win.foreground_window()
            return foreground.process if foreground else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ summary
    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for event in self.events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return {
            "recording": self.recording,
            "application": self.application,
            "goal": self.goal,
            "events": len(self.events),
            "by_kind": counts,
            "redacted": self.stats["redacted"],
            "raw_coordinates": self.stats["raw_coordinates"],
            "duration_s": round((self.stopped_at or time.time()) - self.started_at, 2)
            if self.started_at else 0,
            "semantic_events": sum(v for k, v in counts.items()
                                   if k in ("uia_invoke", "app_launch", "file_save",
                                            "browser_action", "plugin_action")),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": getattr(self, "session_id", ""),
                "application": self.application, "goal": self.goal,
                "events": [e.to_dict() for e in self.events],
                "before_state": self.before_state, "after_state": self.after_state,
                "summary": self.summary()}
