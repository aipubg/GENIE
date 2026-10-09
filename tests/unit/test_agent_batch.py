"""Durable batch / subagent execution (re-audit 14.5, donor: DeerFlow).

Three failure modes this prevents: unbounded fan-out, work lost when a worker dies, and the same
task delegated twice. The crash tests are the point — a dead worker must cost one attempt, not the
work.
"""
from __future__ import annotations

import time

from agents.batch import (CANCELLED, DONE, FAILED, QUEUED, RUNNING, BatchService)


# ------------------------------------------------------------------- submit
def test_submitting_creates_a_queued_task():
    svc = BatchService()
    out = svc.submit("task-a", {"x": 1})
    assert out["duplicate"] is False
    assert out["task"].status == QUEUED


def test_the_same_key_is_not_delegated_twice():
    """Duplicate delegation ledger."""
    svc = BatchService()
    first = svc.submit("task-a")
    second = svc.submit("task-a", {"different": True})
    assert second["duplicate"] is True
    assert second["task"].task_id == first["task"].task_id
    assert len(svc.tasks()) == 1, "no duplicate work"


def test_different_keys_are_separate_tasks():
    svc = BatchService()
    svc.submit("a")
    svc.submit("b")
    assert len(svc.tasks()) == 2


# ------------------------------------------------------------------- capacity
def test_concurrency_is_capped_by_max_live():
    svc = BatchService(max_live=2)
    for n in range(5):
        svc.submit(f"t{n}")
    assert svc.claim() is not None
    assert svc.claim() is not None
    assert svc.claim() is None, "must not exceed max_live concurrent tasks"


def test_capacity_frees_up_as_tasks_complete():
    svc = BatchService(max_live=1)
    svc.submit("a")
    svc.submit("b")
    first = svc.claim()
    assert svc.claim() is None
    svc.complete(first.task_id, "ok")
    assert svc.claim() is not None, "a finished task frees a slot"


# ------------------------------------------------------------------ outcomes
def test_completing_records_the_result():
    svc = BatchService()
    task = svc.submit("a")["task"]
    svc.claim()
    svc.complete(task.task_id, {"built": True})
    assert svc.get(task.task_id).status == DONE
    assert svc.get(task.task_id).result == {"built": True}


def test_a_failure_is_retried_until_the_attempt_limit():
    svc = BatchService(max_attempts=2)
    task = svc.submit("a")["task"]
    svc.claim()                       # attempt 1
    svc.fail(task.task_id, "boom")
    assert svc.get(task.task_id).status == QUEUED, "retryable"
    svc.claim()                       # attempt 2
    svc.fail(task.task_id, "boom again")
    assert svc.get(task.task_id).status == FAILED, "no infinite retry"
    assert svc.get(task.task_id).error


def test_cancelling_stops_a_task():
    svc = BatchService()
    task = svc.submit("a")["task"]
    svc.cancel(task.task_id)
    assert svc.get(task.task_id).status == CANCELLED
    assert svc.claim() is None, "a cancelled task must not be claimed"


# ------------------------------------------------------------------ durability
def test_an_expired_lease_is_recovered_so_the_work_is_not_lost():
    """The crash case: a worker dies holding a task."""
    svc = BatchService(lease_ms=1)
    task = svc.submit("a")["task"]
    svc.claim()
    assert svc.get(task.task_id).status == RUNNING

    time.sleep(0.01)                  # lease expires
    recovered = svc.recover_expired_leases()

    assert task.task_id in recovered
    assert svc.get(task.task_id).status == QUEUED, "the task is available again"
    assert svc.claim() is not None, "and can be picked up by another worker"


def test_restart_requeues_every_running_task():
    """After a restart every in-flight worker is gone, lease or not."""
    svc = BatchService(lease_ms=10_000_000)   # long lease, still running
    svc.submit("a")
    svc.submit("b")
    svc.claim()
    svc.claim()
    recovered = svc.requeue_all_running()
    assert len(recovered) == 2
    assert all(t.status == QUEUED for t in svc.tasks())


def test_recovering_when_nothing_expired_is_a_no_op():
    svc = BatchService(lease_ms=60_000)
    svc.submit("a")
    svc.claim()
    assert svc.recover_expired_leases() == []


# ---------------------------------------------------------------------- pause
def test_pause_stops_new_claims_and_resume_restores_them():
    svc = BatchService()
    svc.submit("a")
    svc.pause()
    assert svc.claim() is None
    svc.resume()
    assert svc.claim() is not None


# -------------------------------------------------------------------- report
def test_status_reports_progress_and_completion():
    svc = BatchService()
    a = svc.submit("a")["task"]
    svc.submit("b")
    status = svc.status()
    assert status["total"] == 2
    assert status["by_status"][QUEUED] == 2
    assert status["complete"] is False

    svc.claim()
    svc.complete(a.task_id)
    svc.cancel(svc.tasks()[1].task_id)
    assert svc.status()["complete"] is True
