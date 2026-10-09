"""Desktop control locks + USER_TAKEOVER handling (computer/locks).

Concurrency rule (spec §2.9/§2.11): two agents must never drive mouse/keyboard at the same
time, and a human always wins over automation.

    acquire(holder) -> exclusive desktop lease (DB-backed, lease TTL)
    takeover()      -> user touched the machine: pause conflicting automation, release the
                       lease immediately, never fight the human
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.contracts import EventType
from core.events import get_bus
from core.logging_setup import get_logger

from . import input as input_mod

log = get_logger("computer.locks")

DESKTOP_LOCK = "computer:desktop-input"


@dataclass
class LockState:
    held: bool = False
    holder: str = ""
    acquired_at: float = 0.0
    lease_until: float = 0.0
    paused: bool = False
    pause_reason: str = ""
    takeover_count: int = 0


class DesktopLock:
    """Exclusive desktop input ownership with lease renewal and takeover awareness."""

    def __init__(self, locks_service=None, ttl_ms: int = 30_000,
                 detector: Optional[input_mod.TakeoverDetector] = None):
        self.locks = locks_service
        self.ttl_ms = ttl_ms
        self.detector = detector or input_mod.DETECTOR
        self._state = LockState()
        self._lock = threading.RLock()
        self._bus = get_bus()

    # ---------------------------------------------------------------- acquire
    def acquire(self, holder: str, ttl_ms: Optional[int] = None) -> Dict[str, Any]:
        ttl = ttl_ms or self.ttl_ms
        with self._lock:
            if self._state.paused:
                return {"ok": False, "error": "desktop control paused after user takeover",
                        "reason": self._state.pause_reason}
            if self.locks is not None:
                if not self.locks.acquire(DESKTOP_LOCK, holder, ttl):
                    return {"ok": False, "error": "desktop input is locked by another holder"}
            self._state.held = True
            self._state.holder = holder
            self._state.acquired_at = time.time()
            self._state.lease_until = time.time() + ttl / 1000
            self.detector.arm()
            log.debug("desktop lock acquired by %s (ttl=%sms)", holder, ttl)
            return {"ok": True, "holder": holder, "lease_until": self._state.lease_until}

    def renew(self, holder: str, ttl_ms: Optional[int] = None) -> bool:
        ttl = ttl_ms or self.ttl_ms
        with self._lock:
            if not self._state.held or self._state.holder != holder:
                return False
            if self.locks is not None:
                self.locks.acquire(DESKTOP_LOCK, holder, ttl)
            self._state.lease_until = time.time() + ttl / 1000
            return True

    def release(self, holder: str) -> bool:
        with self._lock:
            if self._state.held and self._state.holder != holder:
                return False
            if self.locks is not None:
                self.locks.release(DESKTOP_LOCK, holder)
            self._state.held = False
            self._state.holder = ""
            self.detector.disarm()
            return True

    def is_held_by(self, holder: str) -> bool:
        with self._lock:
            return self._state.held and self._state.holder == holder

    # --------------------------------------------------------------- takeover
    def check_takeover(self) -> Optional[Dict[str, Any]]:
        """Poll for user takeover; on detection release the lease and pause automation."""
        event = self.detector.check()
        if not event:
            return None
        with self._lock:
            holder = self._state.holder
            if self.locks is not None and holder:
                self.locks.release(DESKTOP_LOCK, holder)
            self._state.held = False
            self._state.holder = ""
            self._state.paused = True
            self._state.pause_reason = "USER_TAKEOVER"
            self._state.takeover_count += 1
        self.detector.disarm()
        payload = {**event.to_dict(), "released_holder": holder,
                   "action": "paused_conflicting_automation"}
        self._bus.publish(EventType.USER_TAKEOVER, payload)
        log.info("USER_TAKEOVER: released desktop lock (was held by %s)", holder or "nobody")
        return payload

    def resume(self, reason: str = "user resumed GENIE") -> None:
        with self._lock:
            self._state.paused = False
            self._state.pause_reason = ""
        log.info("desktop automation resumed: %s", reason)

    # ------------------------------------------------------------------ state
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "held": self._state.held,
                "holder": self._state.holder,
                "lease_remaining_ms": max(0, int((self._state.lease_until - time.time()) * 1000)),
                "paused": self._state.paused,
                "pause_reason": self._state.pause_reason,
                "takeover_count": self._state.takeover_count,
                "takeovers": self.detector.takeovers(10),
            }

    def purge_stale(self) -> int:
        return self.locks.purge_stale() if self.locks is not None else 0
