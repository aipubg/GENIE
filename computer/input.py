"""Desktop input + USER_TAKEOVER detection (computer/input).

Raw mouse/keyboard is the LAST resort in the automation priority chain, but it must be
correct when it is used — and it must never fight the human.

USER_TAKEOVER: while GENIE owns desktop input, if the user physically moves the mouse or
types, GENIE must notice immediately, pause conflicting automation and release control.
Detection uses GetLastInputInfo, which reports the tick of the last *real* input event
(ours included), so we can tell "input happened that we did not send".
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

from . import windows_api as win

log = get_logger("computer.input")


@dataclass
class TakeoverEvent:
    ts: float
    idle_ms: int
    ours_tick: int
    observed_tick: int
    reason: str = "user input detected while GENIE owned desktop input"

    def to_dict(self) -> Dict[str, Any]:
        return {"ts": self.ts, "idle_ms": self.idle_ms, "ours_tick": self.ours_tick,
                "observed_tick": self.observed_tick, "reason": self.reason}


class TakeoverDetector:
    """Tracks whether the user has taken over since GENIE last sent input."""

    def __init__(self, grace_ms: int = 120) -> None:
        self.grace_ms = grace_ms
        self._ours_tick = 0
        self._ours_at = 0.0
        self._takeovers: List[TakeoverEvent] = []
        self._lock = threading.Lock()
        self._armed = False

    # ------------------------------------------------------------------ state
    def arm(self) -> None:
        """Called when GENIE takes desktop ownership."""
        with self._lock:
            self._armed = True
            self._ours_tick = win.last_input_tick()
            self._ours_at = time.time()

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def mark_ours(self) -> None:
        """Record that GENIE just sent input (so it is not mistaken for the user's)."""
        with self._lock:
            self._ours_tick = win.last_input_tick()
            self._ours_at = time.time()

    def check(self) -> Optional[TakeoverEvent]:
        """Returns a TakeoverEvent if the user has acted since our last input."""
        with self._lock:
            if not self._armed:
                return None
            observed = win.last_input_tick()
            # Windows tick wraps at ~49 days; treat a large backwards jump as a wrap
            if observed == self._ours_tick:
                return None
            if observed < self._ours_tick and (self._ours_tick - observed) < 0x7FFFFFFF:
                # counter wrapped
                pass
            if (time.time() - self._ours_at) * 1000 < self.grace_ms:
                return None
            event = TakeoverEvent(ts=time.time(), idle_ms=win.idle_ms(),
                                  ours_tick=self._ours_tick, observed_tick=observed)
            self._takeovers.append(event)
            # re-arm at the new tick so one takeover is reported once
            self._ours_tick = observed
            self._ours_at = time.time()
            log.info("USER_TAKEOVER detected (idle=%sms)", event.idle_ms)
            return event

    def takeovers(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._takeovers[-limit:]]

    def reset(self) -> None:
        with self._lock:
            self._takeovers.clear()
            self._armed = False


DETECTOR = TakeoverDetector()


# --------------------------------------------------------------------- actions
def type_text(text: str, detector: TakeoverDetector | None = None) -> Dict[str, Any]:
    d = detector or DETECTOR
    d.arm()
    ok = win.type_text(text)
    d.mark_ours()
    return {"ok": ok, "chars": len(text), "text_len": len(text)}


def press_key(name: str, times: int = 1) -> Dict[str, Any]:
    DETECTOR.arm()
    ok = win._keybd_scancode(name, times)
    DETECTOR.mark_ours()
    return {"ok": ok, "key": name, "times": times}


def hotkey(*keys: str) -> Dict[str, Any]:
    """Press a chord such as ('ctrl', 's')."""
    DETECTOR.arm()
    vks = [win.resolve_vk(k) for k in keys]
    if any(v is None for v in vks):
        return {"ok": False, "error": f"unknown key in {keys}"}
    try:
        for vk in vks:
            _key_down(vk)
        for vk in reversed(vks):
            _key_up(vk)
        DETECTOR.mark_ours()
        return {"ok": True, "chord": "+".join(keys)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _key_down(vk: int) -> None:
    from .windows_api import INPUT, INPUT_KEYBOARD, KEYBDINPUT, _INPUTunion, _send_inputs
    _send_inputs([INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk)))])


def _key_up(vk: int) -> None:
    from .windows_api import (INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP,
                              _INPUTunion, _send_inputs)
    _send_inputs([INPUT(type=INPUT_KEYBOARD,
                        u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)))])


def click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left",
          double: bool = False) -> Dict[str, Any]:
    DETECTOR.arm()
    ok = win.mouse_click(x, y, button=button, double=double)
    DETECTOR.mark_ours()
    return {"ok": ok, "x": x, "y": y, "button": button, "double": double}


def move(x: int, y: int) -> Dict[str, Any]:
    DETECTOR.arm()
    ok = win.mouse_move(x, y)
    DETECTOR.mark_ours()
    return {"ok": ok, "x": x, "y": y}


def scroll(delta: int) -> Dict[str, Any]:
    DETECTOR.arm()
    ok = win.mouse_wheel(delta)
    DETECTOR.mark_ours()
    return {"ok": ok, "delta": delta}


def click_element_rect(rect: List[int], button: str = "left") -> Dict[str, Any]:
    """Click the centre of a UI element rectangle (semantic target -> safe coordinates)."""
    if len(rect) != 4:
        return {"ok": False, "error": "invalid rect"}
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    return click(cx, cy, button=button)


def state() -> Dict[str, Any]:
    return {"idle_ms": win.idle_ms(), "cursor": list(win.cursor_pos()),
            "takeovers": DETECTOR.takeovers(5)}
