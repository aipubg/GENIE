"""Durable batch / subagent execution (agents/batch.py) — re-audit 14.5.

Capability donor: **DeerFlow** (durable ``batch_task`` with SQL state, leases, retries,
pause/resume/cancel and restart recovery).

When GENIE fans work out to many subagents, three things go wrong without this:

1. **unbounded fan-out** — 200 workers at once exhausts budget and context;
2. **lost work on crash** — a worker dies holding a task and the task vanishes;
3. **duplicate work** — the same task gets delegated twice by two agents.

This module addresses all three:

* **capacity** — at most ``max_live`` tasks may be running concurrently;
* **leases** — a claimed task is held for ``lease_ms``; if the worker dies, the lease expires and
  the task is **re-queued**, so a crash costs one attempt, not the work;
* **duplicate ledger** — submitting a key already seen returns the existing task instead of
  creating a second one.

State transitions: queued -> running -> {done, failed} plus cancelled. A failed task retries up to
``max_attempts`` before being marked failed permanently.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.batch")

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class BatchTask:
    task_id: str
    key: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = QUEUED
    attempts: int = 0
    max_attempts: int = 3
    lease_until_ms: int = 0
    result: Any = None
    error: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "key": self.key, "payload": self.payload,
                "status": self.status, "attempts": self.attempts,
                "max_attempts": self.max_attempts,
                "lease_until_ms": self.lease_until_ms, "result": self.result,
                "error": self.error, "created_ms": self.created_ms}


class BatchService:
    """Durable, capacity-limited, crash-tolerant task batch."""

    def __init__(self, *, max_live: int = 4, lease_ms: int = 60_000,
                 max_attempts: int = 3):
        self.max_live = int(max_live)
        self.lease_ms = int(lease_ms)
        self.max_attempts = int(max_attempts)
        self._tasks: Dict[str, BatchTask] = {}
        self._order: List[str] = []
        self._by_key: Dict[str, str] = {}
        self.paused = False

    # ---------------------------------------------------------------- submit
    def submit(self, key: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add work. Submitting an existing key returns the original task (no duplicate work)."""
        existing_id = self._by_key.get(key)
        if existing_id:
            return {"task": self._tasks[existing_id], "duplicate": True}

        task = BatchTask(task_id=f"bt-{uuid.uuid4().hex[:12]}", key=key,
                         payload=dict(payload or {}), max_attempts=self.max_attempts)
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        self._by_key[key] = task.task_id
        return {"task": task, "duplicate": False}

    def get(self, task_id: str) -> Optional[BatchTask]:
        return self._tasks.get(task_id)

    def tasks(self) -> List[BatchTask]:
        return [self._tasks[t] for t in self._order]

    # ----------------------------------------------------------------- claim
    def claim(self, worker: str = "") -> Optional[BatchTask]:
        """Take the next queued task, if capacity allows."""
        if self.paused:
            return None
        running = len([t for t in self._tasks.values() if t.status == RUNNING])
        if running >= self.max_live:
            return None                      # capacity gate — no unbounded fan-out
        for task_id in self._order:
            task = self._tasks[task_id]
            if task.status != QUEUED:
                continue
            task.status = RUNNING
            task.attempts += 1
            task.lease_until_ms = int(time.time() * 1000) + self.lease_ms
            log.debug("batch task %s claimed (attempt %s)", task.task_id, task.attempts)
            return task
        return None

    # ------------------------------------------------------------- outcomes
    def complete(self, task_id: str, result: Any = None) -> Optional[BatchTask]:
        task = self._tasks.get(task_id)
        if task is None or task.status != RUNNING:
            return None
        task.status = DONE
        task.result = result
        task.lease_until_ms = 0
        return task

    def fail(self, task_id: str, error: str = "") -> Optional[BatchTask]:
        """Fail an attempt. Retries while attempts remain, then fails permanently."""
        task = self._tasks.get(task_id)
        if task is None or task.status != RUNNING:
            return None
        task.error = str(error)[:300]
        task.lease_until_ms = 0
        if task.attempts >= task.max_attempts:
            task.status = FAILED
        else:
            task.status = QUEUED             # available for retry
        return task

    def cancel(self, task_id: str) -> Optional[BatchTask]:
        task = self._tasks.get(task_id)
        if task is None or task.status in (DONE, CANCELLED):
            return None
        task.status = CANCELLED
        task.lease_until_ms = 0
        return task

    # -------------------------------------------------------------- recovery
    def recover_expired_leases(self) -> List[str]:
        """Re-queue tasks whose worker died. This is what makes the batch durable."""
        now = int(time.time() * 1000)
        recovered = []
        for task in self._tasks.values():
            if task.status == RUNNING and task.lease_until_ms and now > task.lease_until_ms:
                task.status = QUEUED
                task.lease_until_ms = 0
                recovered.append(task.task_id)
        if recovered:
            log.warning("recovered %s expired batch task(s)", len(recovered))
        return recovered

    def requeue_all_running(self) -> List[str]:
        """Restart recovery: after a restart no task may still be 'running' — its worker is gone.

        Unlike :meth:`recover_expired_leases` this ignores the lease entirely, because after a
        process restart every in-flight task is orphaned whether or not its lease has expired.
        """
        recovered = []
        for task in self._tasks.values():
            if task.status == RUNNING:
                task.status = QUEUED
                task.lease_until_ms = 0
                recovered.append(task.task_id)
        if recovered:
            log.warning("re-queued %s orphaned batch task(s) after restart", len(recovered))
        return recovered

    # ----------------------------------------------------------------- pause
    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    # ---------------------------------------------------------------- report
    def status(self) -> Dict[str, Any]:
        counts = {QUEUED: 0, RUNNING: 0, DONE: 0, FAILED: 0, CANCELLED: 0}
        for task in self._tasks.values():
            counts[task.status] = counts.get(task.status, 0) + 1
        total = len(self._tasks)
        outstanding = counts[QUEUED] + counts[RUNNING]
        return {"total": total, "by_status": counts, "paused": self.paused,
                "max_live": self.max_live,
                # "complete" = no outstanding work left. A cancelled or permanently failed task
                # is settled, not pending — otherwise a batch could never finish after a cancel.
                "outstanding": outstanding,
                "complete": total > 0 and outstanding == 0,
                "all_succeeded": total > 0 and counts[DONE] == total}
