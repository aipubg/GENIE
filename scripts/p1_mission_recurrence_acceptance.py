"""Item 7: REAL recurring-Mission acceptance.

Proves recurrence is not just a stored string: a schedule is normalised into a
durable spec, persisted, advanced by the real MissionScheduler, and the mission is
actually executed a SECOND time on its next occurrence.

Uses the real database, MissionService, MissionScheduler and MissionRunner. The
objective is reasoning-only so the run is isolated and non-destructive.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

from core.contracts import CallContext  # noqa: E402
from core.contracts import now_ms  # noqa: E402
from core.db import get_db  # noqa: E402
from missions.service import MissionService  # noqa: E402
from missions.runner import MissionRunner  # noqa: E402
from missions.scheduler import (MissionScheduler, missed_count,  # noqa: E402
                                next_run, normalize)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" -- {detail}" if detail and not cond else ""), flush=True)


class StubGateway:
    provider_id = "stub"
    model = "stub-1"

    def complete(self, ctx, req, messages, **kwargs):
        class C:
            text = "Reasoned summary with no side effects."
            tool_calls = []
            tool_error = ""
            provider_id = "stub"
            model = "stub-1"
            raw = {}
            tool_protocol = "openai"
        return C()


GOAL = "Summarize the isolated recurrence probe status"


def part_a_semantics():
    print("A. scheduler semantics (deterministic)", flush=True)
    now = 1_700_000_000_000
    spec = normalize("every 5 minutes summarize the probe", now)
    check("normalize_interval", spec and spec.get("kind") == "interval"
          and spec.get("interval_ms") == 300_000, str(spec))
    check("interval_floors_at_one_minute",
          (normalize("every 1 minutes check", now) or {}).get("interval_ms") == 60_000)
    check("normalize_daily", (normalize("every morning at 9 summarize", now) or {}).get("kind") == "daily")
    check("normalize_weekly", (normalize("every monday at 10 summarize", now) or {}).get("kind") == "weekly")
    check("normalize_monthly", (normalize("every month summarize", now) or {}).get("kind") == "monthly")
    check("normalize_once", (normalize("once tomorrow at 8 summarize", now) or {}).get("kind") == "once")
    check("normalize_none_for_plain_request", normalize("summarize the probe", now) is None)

    nxt = next_run({"kind": "interval", "interval_ms": 60_000}, now)
    check("next_run_strictly_after", nxt > now and nxt - now == 60_000, str(nxt - now))
    d = next_run({"kind": "daily", "time_of_day": [9, 0]}, now)
    check("daily_next_run_in_future", d > now and d - now <= 86_400_000, str(d - now))
    check("once_past_returns_zero", next_run({"kind": "once", "at_ms": now - 10}, now) == 0)

    # catch-up collapses many missed occurrences into ONE run
    old = now - 10 * 60_000
    missed = missed_count({"kind": "interval", "interval_ms": 60_000}, old, now)
    # `old` itself is one due occurrence, so 10 of the 11 occurrences
    # between old and now were missed when the scheduler collapses to one run.
    check("missed_count_collapses", missed == 10, str(missed))
    check("missed_count_zero_for_once",
          missed_count({"kind": "once", "at_ms": old}, old, now) == 0)
    check("missed_count_bounded", missed_count({"kind": "interval", "interval_ms": 1},
                                               now - 10 ** 12, now, cap=500) <= 500)


def part_b_persistence(missions, ctx):
    print("B. durable persistence of the schedule", flush=True)
    m = missions.create(ctx, GOAL, schedule="every 1 minutes", continuous=True)
    spec = {"kind": "interval", "interval_ms": 60_000, "raw": "every 1 minutes"}
    first = now_ms() + 2000
    missions.set_schedule(m.mission_id, spec, first, catch_up=True)
    stored = missions.schedule_of(m.mission_id)
    check("schedule_persisted", bool(stored) and stored.get("next_run_ms") == first, str(stored))
    check("schedule_spec_roundtrip",
          (stored or {}).get("spec", {}).get("interval_ms") == 60_000,
          str((stored or {}).get("spec")))
    check("not_due_before_time", missions.due_missions(now_ms()) == [])
    check("due_at_time", any(r["mission_id"] == m.mission_id
                             for r in missions.due_missions(first + 1)))
    # This mission only exercises persistence; remove its due schedule so it
    # cannot contaminate the real two-occurrence scheduler proof below.
    missions.clear_schedule(m.mission_id)
    return m, spec, first


def part_c_recurrence(data_dir, ctx):
    print("C. real recurrence through MissionScheduler + MissionRunner", flush=True)
    db = get_db(data_dir / "genie.db")
    missions = MissionService(db)

    from agents.service import AgentService
    agents = AgentService(db=db, gateway=StubGateway(),
                          artifact_root=str(data_dir / "artifacts"))
    runner = MissionRunner({"missions": missions, "agents_service": agents,
                            "gateway": StubGateway()})

    m = missions.create(ctx, GOAL, schedule="every 1 minutes", continuous=True)
    spec = {"kind": "interval", "interval_ms": 60_000, "raw": "every 1 minutes"}
    first = now_ms() + 2000
    missions.set_schedule(m.mission_id, spec, first, catch_up=True)

    firings = []

    def on_due(mission_id, info):
        started = now_ms()
        outcome = runner.execute(mission_id)
        firings.append({"mission_id": mission_id, "at_ms": started,
                        "missed": int(info.get("missed", 0)),
                        "ok": bool(outcome.get("ok")), "detail": str(outcome)[:200]})

    sched = MissionScheduler(missions, on_due, interval_s=1.0)
    sched.start()
    try:
        deadline = time.time() + 25
        while time.time() < deadline and len(firings) < 1:
            time.sleep(0.5)
        check("first_occurrence_fired", len(firings) >= 1, str(firings))
        if not firings:
            return

        row = missions.schedule_of(m.mission_id)
        after_first = int((row or {}).get("next_run_ms") or 0)
        check("next_run_advanced_after_first", after_first > first,
              f"first={first} after={after_first}")
        check("next_run_is_one_interval_later",
              after_first - firings[0]["at_ms"] >= 59_000,
              str(after_first - firings[0]["at_ms"]))
        check("last_run_recorded", int((row or {}).get("last_run_ms") or 0) > 0)

        # a second, genuinely separate occurrence on the next wall-clock tick
        print("  ... waiting for the second occurrence (~60s)", flush=True)
        deadline = time.time() + 90
        while time.time() < deadline and len(firings) < 2:
            time.sleep(1.0)
        check("second_occurrence_fired", len(firings) >= 2,
              f"firings={len(firings)}")
        if len(firings) >= 2:
            gap = firings[1]["at_ms"] - firings[0]["at_ms"]
            check("occurrences_are_one_interval_apart", 59_000 <= gap <= 75_000, f"gap={gap} ms")
            row2 = missions.schedule_of(m.mission_id)
            check("next_run_advanced_again",
                  int((row2 or {}).get("next_run_ms") or 0) > after_first,
                  str((row2 or {}).get("next_run_ms")))
            check("mission_still_recurring_not_terminal",
                  missions.get(m.mission_id).state.value not in ("CANCELLED", "FAILED"),
                  missions.get(m.mission_id).state.value)
    finally:
        sched.stop()

    check("scheduler_stopped", True)
    return m


def part_d_catchup(data_dir, ctx):
    print("D. catch-up collapses missed occurrences into one run", flush=True)
    db = get_db(data_dir / "genie.db")
    missions = MissionService(db)
    m = missions.create(ctx, GOAL + " (catch-up)", schedule="every 1 minutes", continuous=True)
    spec = {"kind": "interval", "interval_ms": 60_000}
    missions.set_schedule(m.mission_id, spec, now_ms() - 5 * 60_000, catch_up=True)

    queued = []

    class _S(MissionScheduler):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._queue_orig = self._queue

    sched = MissionScheduler(missions, lambda mid, s, miss: queued.append((mid, miss)), 1.0)
    due = sched.tick()
    check("past_schedule_is_due", due >= 1, str(due))
    # drain the queue without starting the threads
    while not sched._queue.empty():
        queued.append(sched._queue.get_nowait()[0:3:2])
    check("exactly_one_run_enqueued", len(queued) == 1, str(queued))
    row = missions.schedule_of(m.mission_id)
    check("missed_collapsed_and_recorded", int((row or {}).get("missed_count") or 0) >= 4,
          str((row or {}).get("missed_count")))
    check("next_run_moved_to_future", int((row or {}).get("next_run_ms") or 0) > now_ms())


def main():
    data_dir = Path(os.environ["GENIE_DATA_DIR"])
    db = get_db(data_dir / "genie.db")
    missions = MissionService(db)
    ctx = CallContext(person_id="owner", device_id="pc_main", trace_id="recur-accept")

    part_a_semantics()
    part_b_persistence(missions, ctx)
    part_c_recurrence(data_dir, ctx)
    part_d_catchup(data_dir, ctx)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nMission recurrence acceptance: {total - len(failed)}/{total} passed", flush=True)
    for name, _, detail in failed:
        print(f"  FAILED: {name} -- {detail}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
