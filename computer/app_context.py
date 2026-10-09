"""Application interaction context (computer/app_context).

One stable identity for an application across a whole multi-step task, so GENIE
does not rediscover the application from zero on every tool call.

This module holds IDENTITY AND STATE ONLY. It never executes anything itself:
every action is delegated to the canonical ComputerService capabilities, so
there remains exactly one computer execution authority and one permission
system. It is deliberately NOT a second desktop controller.

What it carries (owner requirement, Track A):
  application identity, PID, HWND, content/rendered HWND, accessibility root
  identity, current focus, current observation/frame identity, task ID and the
  active transaction identity.

Staleness is explicit: if the bound window disappears, is replaced, or its PID
changes, the context reports itself stale rather than silently acting on a
different window.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

from . import uia, windows_api as win

log = get_logger("computer.app_context")

# How long a bound context stays usable without re-observation.
CONTEXT_TTL_S = 15 * 60


@dataclass
class ApplicationInteractionContext:
    """Stable application identity for one task."""

    task_id: str = ""
    app: str = ""
    pid: int = 0
    hwnd: int = 0
    content_hwnd: int = 0
    window_title: str = ""
    process: str = ""
    accessibility_root: int = 0
    focus: Dict[str, Any] = field(default_factory=dict)
    frame: Dict[str, Any] = field(default_factory=dict)
    transaction_id: str = ""
    created: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    strategy: str = ""
    fallbacks: List[str] = field(default_factory=list)

    # ------------------------------------------------------------- lifecycle
    def touch(self) -> None:
        self.last_used = time.monotonic()

    def expired(self, ttl_s: int = CONTEXT_TTL_S) -> bool:
        return (time.monotonic() - self.last_used) > ttl_s

    def is_stale(self) -> bool:
        """True when the bound window no longer matches this context."""
        if not self.hwnd:
            return True
        current = next((w for w in win.list_windows() if w.hwnd == self.hwnd), None)
        if current is None:
            return True
        if self.pid and current.pid != self.pid:
            return True
        return False

    def note_fallback(self, strategy: str) -> None:
        if strategy and strategy not in self.fallbacks:
            self.fallbacks.append(strategy)

    # ------------------------------------------------------------- binding
    @classmethod
    def bind(cls, window, *, task_id: str = "", app: str = "") -> "ApplicationInteractionContext":
        """Create a context from an observed window, resolving host/content."""
        ctx = cls(task_id=task_id, app=app or (getattr(window, "process", "") or ""),
                  pid=int(getattr(window, "pid", 0) or 0),
                  hwnd=int(getattr(window, "hwnd", 0) or 0),
                  window_title=str(getattr(window, "title", "") or ""),
                  process=str(getattr(window, "process", "") or ""))
        ctx.resolve_content_window()
        return ctx

    def resolve_content_window(self) -> int:
        """Resolve the rendered/content HWND for host+content applications.

        Apps such as WhatsApp expose a host window whose only meaningful
        accessibility surface is a separate rendered child. Resolving it once and
        keeping it on the context is what stops every later step from starting
        from zero.
        """
        self.touch()
        if not self.hwnd:
            return 0
        # UIA descends the selected HWND's tree, including WinUI/WebView children.
        # Same-process top-level windows are siblings, never evidence of children.
        self.accessibility_root = self.hwnd
        self.content_hwnd = self.hwnd
        return self.content_hwnd

    def observe(self, awareness=None) -> Dict[str, Any]:
        """Refresh the observation/frame identity bound to this context."""
        self.touch()
        payload: Dict[str, Any] = {"hwnd": self.hwnd, "content_hwnd": self.content_hwnd,
                                   "pid": self.pid, "stale": self.is_stale()}
        if awareness is not None:
            try:
                observation = awareness.observe()
                payload["display_count"] = observation.display_count
                payload["locked"] = observation.locked
                fg = observation.foreground or {}
                payload["foreground_hwnd"] = fg.get("hwnd")
                payload["foreground_is_target"] = fg.get("hwnd") == self.hwnd
            except Exception as exc:
                payload["awareness_error"] = str(exc)
        self.frame = payload
        return payload

    # ------------------------------------------------------------- delegation
    def run(self, service, ctx, capability: str, params: Optional[Dict[str, Any]] = None,
            **kwargs) -> Dict[str, Any]:
        """Delegate one action to the canonical ComputerService.

        The context never performs input itself. `params` are augmented with the
        bound window identity so the executor targets the SAME window the task
        already resolved, unless the caller explicitly overrides it.
        """
        self.touch()
        payload = dict(params or {})
        if self.hwnd and "window_id" not in payload and "hwnd" not in payload:
            payload["window_id"] = self.hwnd
        if self.content_hwnd and "content_window_id" not in payload:
            payload["content_window_id"] = self.content_hwnd
        if self.task_id and "task_id" not in payload:
            payload["task_id"] = self.task_id
        result = service.execute(ctx, capability, payload, **kwargs)
        return result.to_dict() if hasattr(result, "to_dict") else dict(result or {})

    # ------------------------------------------------------------- serialisation
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "app": self.app, "pid": self.pid,
            "hwnd": self.hwnd, "content_hwnd": self.content_hwnd,
            "window_title": self.window_title, "process": self.process,
            "accessibility_root": self.accessibility_root,
            "focus": dict(self.focus), "frame": dict(self.frame),
            "transaction_id": self.transaction_id,
            "strategy": self.strategy, "fallbacks": list(self.fallbacks),
            "stale": self.is_stale(), "expired": self.expired(),
            "age_s": round(time.monotonic() - self.created, 2),
        }


class ApplicationContextRegistry:
    """Task-scoped registry so one task reuses one application context."""

    def __init__(self, ttl_s: int = CONTEXT_TTL_S):
        self._contexts: Dict[str, ApplicationInteractionContext] = {}
        self._lock = threading.RLock()
        self.ttl_s = ttl_s
        self._active: Dict[str, str] = {}

    @staticmethod
    def _key(task_id: str, app: str) -> str:
        return f"{task_id or 'default'}::{app or 'any'}"

    def get(self, task_id: str, app: str = "") -> Optional[ApplicationInteractionContext]:
        with self._lock:
            context = self._contexts.get(self._key(task_id, app))
            if context is None:
                return None
            if context.expired(self.ttl_s) or context.is_stale():
                self._contexts.pop(self._key(task_id, app), None)
                return None
            context.touch()
            return context

    def get_for_task(self, task_id: str) -> Optional[ApplicationInteractionContext]:
        """Return the one unambiguous live application context for a task."""
        key_task = task_id or "default"
        with self._lock:
            matches = []
            for key, context in list(self._contexts.items()):
                if context.task_id != key_task:
                    continue
                if context.expired(self.ttl_s) or context.is_stale():
                    self._contexts.pop(key, None)
                    continue
                matches.append(context)
            selected = self._contexts.get(self._active.get(key_task, ""))
            if selected not in matches:
                selected = matches[0] if len(matches) == 1 else None
            if selected is not None:
                selected.touch()
            return selected

    def put(self, context: ApplicationInteractionContext) -> ApplicationInteractionContext:
        with self._lock:
            self._contexts[self._key(context.task_id, context.app)] = context
            self._active[context.task_id or "default"] = self._key(context.task_id, context.app)
            return context

    def bind_window(self, window, *, task_id: str = "", app: str = "") -> ApplicationInteractionContext:
        context = ApplicationInteractionContext.bind(window, task_id=task_id, app=app)
        return self.put(context)

    def release(self, task_id: str, app: str = "") -> None:
        with self._lock:
            self._contexts.pop(self._key(task_id, app), None)

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()
            self._active.clear()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {"contexts": len(self._contexts),
                    "tasks": sorted({c.task_id for c in self._contexts.values()}),
                    "ttl_s": self.ttl_s}
