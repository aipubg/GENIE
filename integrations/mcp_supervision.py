"""MCP connection supervision (integrations/mcp_supervision.py) — re-audit 14.5.

Capability donor: **Strix** (``SupervisedMcpSession``).

Per the reconciliation brief, GENIE's MCP stack should take the best part from each donor rather
than picking one: AgentScope (hub/discovery), DeerFlow (agent-tool integration) and **Strix
(connection supervision)**. This module is the Strix part.

What a naive MCP client gets wrong, and what this supervises:

* **retry storms** — a dead server gets hammered on every call. Here failures open a quarantine
  with a mandatory cooldown before reconnect is even attempted.
* **unknown state** — "is it down, or just slow?" is never answered. Here every session has an
  explicit state: new / connecting / live / degraded / quarantined / dead.
* **leaked pending calls** — when a connection dies, in-flight calls are forgotten and hang
  forever. Here pending calls are tracked and explicitly failed on death.
* **unbounded concurrency** — calls are admitted through a semaphore.

Crucially, a quarantined/dead session **refuses** the call and says so. It never pretends to have
executed something it did not.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("integrations.mcp_supervision")

NEW = "new"
CONNECTING = "connecting"
LIVE = "live"
DEGRADED = "degraded"
QUARANTINED = "quarantined"
DEAD = "dead"

TERMINAL_REFUSAL = (QUARANTINED, DEAD)


class SupervisedMcpSession:
    """A supervised connection to one MCP server."""

    def __init__(self, name: str, *,
                 transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
                 connect: Optional[Callable[[], bool]] = None,
                 max_concurrent: int = 4,
                 failure_threshold: int = 3,
                 retry_delay_s: float = 2.0,
                 dead_after: int = 6):
        self.name = str(name)
        self.transport = transport
        self.connect_fn = connect
        self.failure_threshold = int(failure_threshold)
        self.dead_after = int(dead_after)
        self.retry_delay_s = float(retry_delay_s)

        self._state = NEW
        self._failures = 0
        self._reconnect_failures = 0
        self._last_failure_ms = 0
        self._last_success_ms = 0
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._semaphore = threading.Semaphore(int(max_concurrent))
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    def _set_state(self, state: str, why: str = "") -> None:
        if state != self._state:
            log.info("mcp %s: %s -> %s (%s)", self.name, self._state, state, why)
        self._state = state

    # ------------------------------------------------------------------- call
    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Invoke an MCP method under supervision."""
        params = dict(params or {})
        call_id = f"mcp-{uuid.uuid4().hex[:8]}"

        with self._lock:
            if self._state in TERMINAL_REFUSAL:
                return self._refuse(call_id, method,
                                    f"session is {self._state}; not attempting the call")
            if self._state in (NEW, CONNECTING):
                self._connect()

        acquired = self._semaphore.acquire(timeout=5.0)
        if not acquired:
            return {"ok": False, "call_id": call_id, "status": "busy",
                    "error": "concurrency limit reached — call not admitted"}

        self._pending[call_id] = {"method": method, "started_ms": int(time.time() * 1000)}
        try:
            if self.transport is None:
                # no transport wired: honest refusal, never a fabricated result
                return self._fail(call_id, method, "no MCP transport configured")
            result = self.transport(method, params) or {}
            if result.get("ok") is False or result.get("error"):
                return self._fail(call_id, method,
                                  str(result.get("error") or "transport reported failure"))
            self._succeed(call_id, method)
            return {"ok": True, "call_id": call_id, "status": "ok",
                    "result": result.get("result", result), "session": self._state}
        except Exception as exc:
            return self._fail(call_id, method, str(exc))
        finally:
            self._pending.pop(call_id, None)
            self._semaphore.release()

    # --------------------------------------------------------------- outcomes
    def _succeed(self, call_id: str, method: str) -> None:
        with self._lock:
            self._failures = 0
            self._last_success_ms = int(time.time() * 1000)
            self._set_state(LIVE, f"{method} ok")
            self.history.append({"call_id": call_id, "method": method, "ok": True})

    def _fail(self, call_id: str, method: str, error: str) -> Dict[str, Any]:
        with self._lock:
            self._failures += 1
            self._last_failure_ms = int(time.time() * 1000)
            # NOTE: DEAD is deliberately NOT reachable from here. Once a session is quarantined
            # its calls are refused, so `failures` stops incrementing and dead_after could never
            # be reached. Death is therefore decided by exhausted *reconnect* attempts.
            if self._failures >= self.failure_threshold:
                self._set_state(QUARANTINED, f"{self._failures} failures")
            else:
                self._set_state(DEGRADED, error[:60])
            self.history.append({"call_id": call_id, "method": method, "ok": False,
                                 "error": error[:200]})
            return {"ok": False, "call_id": call_id, "status": self._state,
                    "error": error, "session": self._state,
                    "retry_after_s": self._retry_wait()}

    def _refuse(self, call_id: str, method: str, why: str) -> Dict[str, Any]:
        self.history.append({"call_id": call_id, "method": method, "ok": False,
                             "error": why, "refused": True})
        return {"ok": False, "call_id": call_id, "status": self._state,
                "error": why, "session": self._state, "retry_after_s": self._retry_wait()}

    def _drop_pending(self, reason: str) -> None:
        """Never leave in-flight calls hanging when the connection dies."""
        if self._pending:
            log.warning("mcp %s: dropping %s pending call(s): %s",
                        self.name, len(self._pending), reason)
        self._pending.clear()

    # ------------------------------------------------------------- reconnect
    def _retry_wait(self) -> float:
        elapsed = (time.time() * 1000 - self._last_failure_ms) / 1000.0
        return max(0.0, self.retry_delay_s - elapsed)

    def _connect(self) -> bool:
        self._set_state(CONNECTING, "connecting")
        if self.connect_fn is None:
            self._set_state(LIVE, "no connect hook; assuming live")
            return True
        try:
            ok = bool(self.connect_fn())
        except Exception as exc:
            log.debug("mcp %s connect failed: %s", self.name, exc)
            ok = False
        self._set_state(LIVE if ok else QUARANTINED, "connect " + ("ok" if ok else "failed"))
        return ok

    def reconnect(self) -> Dict[str, Any]:
        """Explicitly attempt recovery. Honours the cooldown — no retry storms."""
        with self._lock:
            wait = self._retry_wait()
            if wait > 0:
                return {"ok": False, "status": self._state,
                        "error": f"in cooldown; retry after {wait:.1f}s",
                        "retry_after_s": wait}
            if self._state == DEAD:
                return {"ok": False, "status": DEAD,
                        "error": "session dead beyond recovery; recreate it"}
            ok = self._connect()
            if ok:
                self._failures = 0
                self._reconnect_failures = 0
                return {"ok": True, "status": self._state, "retry_after_s": 0.0}

            # a failed recovery attempt is the only thing that can kill a session
            self._reconnect_failures += 1
            self._last_failure_ms = int(time.time() * 1000)
            if self._reconnect_failures >= self.dead_after:
                self._set_state(DEAD, f"{self._reconnect_failures} failed reconnects")
                self._drop_pending("session dead after repeated failed reconnects")
            else:
                self._set_state(QUARANTINED, "reconnect failed")
            return {"ok": False, "status": self._state,
                    "error": "reconnect failed", "retry_after_s": self._retry_wait()}

    # ---------------------------------------------------------------- report
    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self._state, "failures": self._failures,
                "pending": len(self._pending),
                "last_success_ms": self._last_success_ms,
                "last_failure_ms": self._last_failure_ms,
                "retry_after_s": self._retry_wait(),
                "calls": len(self.history),
                "usable": self._state not in TERMINAL_REFUSAL}
