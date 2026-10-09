"""Event bus (core/events).

Guarantees (contract C13):
  * at-least-once delivery, handlers run on a worker pool (never block the publisher)
  * wildcard subscriptions: "MISSION_*" or "*"
  * poison events land in a dead-letter queue instead of killing the bus
  * optional subscriber callbacks receive every event (used by SSE stream to the UI)
"""
from __future__ import annotations

import fnmatch
import queue
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from .contracts import Event, now_ms

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, workers: int = 2, ring_size: int = 500):
        self._subs: Dict[str, List[tuple[str, Handler]]] = {}
        self._lock = threading.RLock()
        self._queue: "queue.Queue[Event]" = queue.Queue()
        self._dlq: List[tuple[Event, str]] = []
        self._ring: List[Event] = []
        self._ring_size = ring_size
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()
        self._seq = 0
        self._stats = {"published": 0, "delivered": 0, "failed": 0}
        for i in range(workers):
            t = threading.Thread(target=self._run, name=f"eventbus-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    # ------------------------------------------------------------------ API
    def subscribe(self, pattern: str, handler: Handler) -> str:
        sub_id = f"sub_{len(self._subs)}_{id(handler)}"
        with self._lock:
            self._subs.setdefault(pattern, []).append((sub_id, handler))
        return sub_id

    def unsubscribe(self, sub_id: str) -> None:
        with self._lock:
            for pattern, handlers in list(self._subs.items()):
                self._subs[pattern] = [h for h in handlers if h[0] != sub_id]

    def publish(self, event_type: str, payload: Dict[str, Any] | None = None,
                trace_id: Optional[str] = None, sync: bool = False) -> Event:
        event = Event(type=event_type, payload=payload or {}, trace_id=trace_id, ts_ms=now_ms())
        with self._lock:
            self._stats["published"] += 1
            self._ring.append(event)
            if len(self._ring) > self._ring_size:
                self._ring.pop(0)
        if sync:
            self._deliver(event)
        else:
            self._queue.put(event)
        return event

    def recent(self, limit: int = 50, since_ms: int | None = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = [e for e in self._ring if since_ms is None or e.ts_ms >= since_ms]
        return [e.to_dict() for e in items[-limit:]]

    @property
    def dlq(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"event": e.to_dict(), "error": err} for e, err in self._dlq]

    @property
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def drain(self, timeout: float = 1.0) -> None:
        """Wait until queued events are processed (used by tests)."""
        self._queue.join() if hasattr(self._queue, "join") else None
        deadline = _deadline(timeout)
        while not self._queue.empty() and _remaining(deadline):
            threading.Event().wait(0.01)

    def shutdown(self) -> None:
        self._stop.set()
        for t in self._workers:
            if t.is_alive():
                t.join(timeout=0.5)

    # --------------------------------------------------------------- internals
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._deliver(event)
            finally:
                self._queue.task_done()

    def _deliver(self, event: Event) -> None:
        with self._lock:
            matched = [(h, p) for p, handlers in self._subs.items()
                       if fnmatch.fnmatch(event.type, p) for (_, h) in handlers]
        for handler, pattern in matched:
            try:
                handler(event)
                with self._lock:
                    self._stats["delivered"] += 1
            except Exception as exc:  # never let a bad handler kill the bus
                with self._lock:
                    self._stats["failed"] += 1
                    self._dlq.append((event, f"{pattern}: {exc}: {traceback.format_exc(limit=2)}"))


def _deadline(timeout: float) -> float:
    import time
    return time.time() + timeout


def _remaining(deadline: float) -> bool:
    import time
    return time.time() < deadline


_BUS: EventBus | None = None


def get_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS
