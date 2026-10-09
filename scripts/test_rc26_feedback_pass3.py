"""RC26 Owner Feedback PASS 3 — mission execution engine (targeted tests only).

Covers: durable step DAG, execution + verify + checkpoint, dynamic agents,
scheduler (normalized + next-run + catch-up), continuous/waiting, chat/voice
mission control, restart/resume, and an end-to-end local mission.

Run:  PYTHONPATH="." python scripts/test_rc26_feedback_pass3.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def _db(tmp: Path):
    from core.db import Database
    return Database(tmp / "genie.db")


def _ms(tmp: Path):
    from missions.service import MissionService
    return MissionService(_db(tmp))


# ===========================================================================
# Scheduler: normalization + next-run
# ===========================================================================
def test_schedule_normalize() -> None:
    from missions.scheduler import normalize, next_run
    now = 1_700_000_000_000
    cases = {
        "every morning check the sites": "daily",
        "every day upload content": "daily",
        "every monday prepare the report": "weekly",
        "every 6 hours poll the feed": "interval",
        "once tomorrow at 5 pm": "once",
        "at 9 am send the digest": "daily",
    }
    for text, kind in cases.items():
        spec = normalize(text, now)
        check(f"normalize: {text!r} -> {kind}", bool(spec) and spec.get("kind") == kind,
              (spec or {}).get("kind", "None"))

    daily = normalize("every day at 9 am", now)
    nxt = next_run(daily, now)
    check("next_run: daily is in the future", nxt > now, nxt)
    # and the following one is exactly a day later
    check("next_run: daily advances by a day", next_run(daily, nxt) - nxt == 86_400_000)
    interval = normalize("every 6 hours", now)
    check("next_run: interval = 6h", next_run(interval, now) - now == 6 * 3_600_000)
    once = normalize("once tomorrow at 5 pm", now)
    check("next_run: one-off fires once", next_run(once, now) > now
          and next_run(once, next_run(once, now)) == 0)


def test_catchup_policy() -> None:
    from missions.scheduler import normalize, missed_count, MissionScheduler
    now = 1_700_000_000_000
    spec = normalize("every day at 9 am", now)
    # stored next-run 10 days in the past -> ~10 missed occurrences
    stored = now - 10 * 86_400_000
    missed = missed_count(spec, stored, now)
    check("catch-up: missed occurrences counted", missed >= 9, missed)

    ms = _ms(Path(tempfile.mkdtemp(prefix="genie-cu-")))
    from core.contracts import CallContext, MissionState
    m = ms.create(CallContext(), "daily report")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    ms.set_schedule(m.mission_id, spec, stored)
    dispatched = []
    sch = MissionScheduler(ms, lambda mid, info: dispatched.append((mid, info)))
    n = sch.tick(now)
    check("catch-up: due mission dispatched exactly once", n == 1 and sch._queue.qsize() == 1,
          f"due={n} queued={sch._queue.qsize()}")
    queued = sch._queue.queue[0] if sch._queue.qsize() else ("", {}, 0)
    check("catch-up: skipped occurrences reported", queued[2] >= 9, queued[2])
    check("catch-up: next run advanced past now",
          (ms.schedule_of(m.mission_id) or {}).get("next_run_ms", 0) > now)


# ===========================================================================
# Durable step DAG
# ===========================================================================
def test_dag_persist_and_ready() -> None:
    from core.contracts import CallContext, MissionState, MissionStep
    ms = _ms(Path(tempfile.mkdtemp(prefix="genie-dag-")))
    m = ms.create(CallContext(), "do three things")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    ms.replace_steps(m.mission_id, [
        MissionStep(step_id="t1", objective="first", status="pending"),
        MissionStep(step_id="t2", objective="second", status="pending", depends_on=["t1"]),
        MissionStep(step_id="t3", objective="third", status="pending", depends_on=["t2"]),
    ])
    steps = ms.steps(m.mission_id)
    check("dag: three steps persisted", len(steps) == 3)
    check("dag: dependencies survive the DB",
          json.loads(steps[1]["depends_on"]) == ["t1"] and
          json.loads(steps[2]["depends_on"]) == ["t2"])
    ready = ms.ready_steps(m.mission_id)
    check("dag: only the root is ready first",
          [r["step_id"] for r in ready] == ["t1"])
    ms.set_step_status(m.mission_id, "t1", "completed", evidence={"ok": True})
    ready = ms.ready_steps(m.mission_id)
    check("dag: dependent becomes ready after its dep completes",
          [r["step_id"] for r in ready] == ["t2"])
    prog = ms.progress(m.mission_id)
    check("dag: progress counts", prog["done"] == 1 and prog["total"] == 3)

    # resume: a NEW service over the same DB sees the same DAG + statuses
    ms2 = _ms(Path(str(ms.db.path).rsplit("\\", 1)[0] if False else str(ms.db.path.parent)))
    ms2.db = ms.db
    steps2 = ms2.steps(m.mission_id)
    check("dag: survives a fresh service (restart)",
          len(steps2) == 3 and steps2[0]["status"] == "completed")


# ===========================================================================
# Runner: plan -> execute -> verify -> checkpoint (fake agent engine)
# ===========================================================================
class _FakeDirector:
    def classify(self, text, ctx, hint=None):
        from core.contracts import TaskType
        from director.base import DirectorDecision, DirectorTask
        return DirectorDecision(tasks=[DirectorTask(
            type=TaskType.COMPUTER_ACTION, capability="test.echo",
            params={"text": (text or "")[:40]})])


class _FakeWorker:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.calls = []

    def __call__(self, ctx, task):
        self.calls.append(task.get("capability"))
        p = self.root / f"{ctx.mission_id}-{len(self.calls)}.txt"
        p.write_text("done", encoding="utf-8")
        return {"ok": True, "path": str(p), "capability": task.get("capability")}


class _FakeGateway:
    def complete(self, *a, **k):
        return SimpleNamespace(text="ok", provider_id="fake")


class _FakeAgents:
    """Minimal stand-in for AgentService that runs the DAG via the real runner."""

    def __init__(self):
        self._teams = {}

    def team(self, mid):
        return self._teams.get(mid)

    def start_team(self, *, mission_id, objective, plan, runner=None, **kw):
        self._teams[mission_id] = {"plan": plan, "runner": runner}
        return {"ok": True, "mission_id": mission_id,
                "agents": [t.get("role") for t in plan.team]}

    def run(self, mission_id, **kw):
        t = self._teams[mission_id]
        done = set()
        tasks = list(t["plan"].tasks)
        for _ in range(len(tasks) + 2):
            for spec in tasks:
                if spec["task_id"] in done:
                    continue
                if all(d in done for d in spec.get("depends_on", [])):
                    node = SimpleNamespace(task_id=spec["task_id"],
                                           objective=spec["objective"],
                                           required_role=spec.get("required_role", "worker"))
                    agent = SimpleNamespace(role=spec.get("required_role", "worker"))
                    out = t["runner"](agent, node, {"mission_id": mission_id})
                    if out.get("ok"):
                        done.add(spec["task_id"])
        return {"ok": True}

    def pause(self, *a, **k):
        pass

    def resume(self, *a, **k):
        pass

    def cancel(self, *a, **k):
        pass


def _services(tmp: Path):
    ms = _ms(tmp)
    worker = _FakeWorker(tmp / "artifacts")
    return {"missions": ms, "director": _FakeDirector(), "worker": worker,
            "gateway": _FakeGateway(), "agents_service": _FakeAgents()}, ms, worker


def test_runner_plan_execute() -> None:
    from core.contracts import CallContext, MissionState
    from missions.runner import MissionRunner
    tmp = Path(tempfile.mkdtemp(prefix="genie-run-"))
    services, ms, worker = _services(tmp)
    runner = MissionRunner(services)

    m = ms.create(CallContext(), "research the market and write a report")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    plan_steps = runner.ensure_plan(m)
    check("runner: plan derives a step DAG", len(plan_steps) >= 3, len(plan_steps))
    check("runner: plan has dependency edges",
          any(json.loads(s["depends_on"] or "[]") for s in plan_steps))

    out = runner.execute(m.mission_id)
    steps = ms.steps(m.mission_id)
    check("runner: every step completed", all(s["status"] == "completed" for s in steps))
    check("runner: mission completed", out.get("state") == MissionState.COMPLETED.value,
          out.get("state"))
    check("runner: worker actually executed", len(worker.calls) >= len(steps))
    check("runner: evidence persisted per step",
          all(json.loads(s["evidence"] or "{}") for s in steps))
    check("runner: provider recorded", all(s["provider_used"] is not None for s in steps))


def test_runner_verify_and_idempotency() -> None:
    from core.contracts import CallContext, MissionState
    from missions.runner import MissionRunner
    tmp = Path(tempfile.mkdtemp(prefix="genie-idem-"))
    services, ms, worker = _services(tmp)
    runner = MissionRunner(services)
    m = ms.create(CallContext(), "write a report")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    runner.ensure_plan(m)
    runner.execute(m.mission_id)
    before = len(worker.calls)
    # Re-running a completed mission must NOT re-execute verified steps.
    runner.execute(m.mission_id)
    check("idempotency: completed steps are not re-run", len(worker.calls) == before,
          f"{before} -> {len(worker.calls)}")

    # A step whose evidence names a missing file must NOT verify.
    from missions.runner import MissionRunner as MR
    check("verify: missing artifact fails verification",
          MR._verify({"ok": True, "evidence": {"results": [
              {"path": str(tmp / "does-not-exist.txt")}]}}) is False)
    check("verify: present artifact passes",
          MR._verify({"ok": True, "evidence": {"results": [
              {"path": str(next((tmp / "artifacts").iterdir()))}]}}) is True)


def test_resume_from_checkpoint() -> None:
    from core.contracts import CallContext, MissionState
    from missions.runner import MissionRunner
    tmp = Path(tempfile.mkdtemp(prefix="genie-resume-"))
    services, ms, worker = _services(tmp)
    runner = MissionRunner(services)
    m = ms.create(CallContext(), "code a small feature")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    steps = runner.ensure_plan(m)
    runner.execute(m.mission_id)
    before = len(worker.calls)

    # Simulate a crash: the last step never finished.
    last = steps[-1]["step_id"]
    ms.set_step_status(m.mission_id, last, "pending")
    # "Relaunch": a brand-new runner + fresh agent engine over the same store.
    services2, _, worker2 = _services(tmp)
    services2["missions"] = ms
    services2["worker"] = worker2
    runner2 = MissionRunner(services2)
    out = runner2.execute(m.mission_id)
    steps_after = ms.steps(m.mission_id)
    check("resume: mission completes after resume",
          out.get("state") == MissionState.COMPLETED.value, out.get("state"))
    check("resume: only the unfinished step re-ran", len(worker2.calls) == 1,
          len(worker2.calls))
    check("resume: completed steps stayed completed",
          sum(1 for s in steps_after if s["status"] == "completed") == len(steps_after))


def test_continuous_waiting() -> None:
    from core.contracts import CallContext, MissionState
    from missions.runner import MissionRunner
    tmp = Path(tempfile.mkdtemp(prefix="genie-cont-"))
    services, ms, worker = _services(tmp)
    runner = MissionRunner(services)
    m = ms.create(CallContext(), "continuously monitor these websites",
                  continuous=True)
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    runner.ensure_plan(m)
    out = runner.execute(m.mission_id)
    check("continuous: waits instead of completing",
          out.get("state") == MissionState.WAITING.value, out.get("state"))
    sch = ms.schedule_of(m.mission_id)
    check("continuous: a wake time is scheduled (no busy loop)",
          bool(sch) and int(sch.get("next_run_ms") or 0) > 0)


# ===========================================================================
# Chat / voice mission control
# ===========================================================================
def test_mission_control() -> None:
    from core.contracts import CallContext, MissionState
    from missions.control import parse_control, apply_control
    tmp = Path(tempfile.mkdtemp(prefix="genie-ctl-"))
    ms = _ms(tmp)
    m = ms.create(CallContext(), "manage my YouTube channel and keep running it")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    ms.transition(CallContext(), m.mission_id, MissionState.RUNNING)
    services = {"missions": ms}

    for text, action in [("pause the YouTube mission", "pause"),
                         ("resume it", "resume"),
                         ("stop that mission", "cancel"),
                         ("show me what the YouTube mission is doing", "show")]:
        p = parse_control(text)
        check(f"control: {text!r} -> {action}", bool(p) and p.get("action") == action,
              (p or {}).get("action"))

    # reschedule parses a new recurrence
    p = parse_control("change the YouTube mission to weekly")
    check("control: reschedule recognised",
          bool(p) and p.get("action") == "reschedule" and p.get("spec"))

    r = apply_control(services, "pause the YouTube mission")
    check("control: pause applied", r and r.get("ok") and
          ms.get(m.mission_id).state is MissionState.PAUSED)
    r = apply_control(services, "show me what the YouTube mission is doing")
    check("control: show returns a human summary",
          r and r.get("ok") and "steps done" in r.get("reply", ""))
    r = apply_control(services, "stop that mission")
    check("control: cancel applied", r and r.get("ok") and
          ms.get(m.mission_id).state is MissionState.CANCELLED)
    # ordinary conversation is never hijacked as a mission command
    check("control: normal chat is not a command", parse_control("hello") is None)


# ===========================================================================
# End-to-end local mission
# ===========================================================================
def test_end_to_end_local_mission() -> None:
    from core.contracts import CallContext, MissionState
    from missions.runner import MissionRunner
    tmp = Path(tempfile.mkdtemp(prefix="genie-e2e-"))
    services, ms, worker = _services(tmp)
    runner = MissionRunner(services)

    m = ms.create(CallContext(), "research competitors and write a report")
    ms.transition(CallContext(), m.mission_id, MissionState.PLANNED)
    steps = runner.ensure_plan(m)

    # multiple DAG steps, at least two specialist roles
    roles = {s.get("required_role") for s in steps}
    check("e2e: multiple DAG steps", len(steps) >= 4, len(steps))
    check("e2e: at least two specialist roles", len(roles) >= 2, sorted(roles))

    out = runner.execute(m.mission_id)
    check("e2e: mission completed", out.get("state") == MissionState.COMPLETED.value,
          out.get("state"))
    check("e2e: an artifact was produced",
          any((tmp / "artifacts").iterdir()))
    check("e2e: every step verified with evidence",
          all(json.loads(s["evidence"] or "{}") and s["status"] == "completed"
              for s in ms.steps(m.mission_id)))
    check("e2e: progress is complete",
          ms.progress(m.mission_id)["done"] == len(steps))

    # simulated restart -> resume -> still complete, no duplicate side effects
    before = len(worker.calls)
    services2, _, worker2 = _services(tmp)
    services2["missions"] = ms
    services2["worker"] = worker2
    MissionRunner(services2).execute(m.mission_id)
    check("e2e: restart does not duplicate side effects", len(worker2.calls) == 0,
          len(worker2.calls))


def main() -> int:
    print("RC26 feedback pass 3 — mission engine targeted tests")
    for fn in (test_schedule_normalize, test_catchup_policy, test_dag_persist_and_ready,
               test_runner_plan_execute, test_runner_verify_and_idempotency,
               test_resume_from_checkpoint, test_continuous_waiting,
               test_mission_control, test_end_to_end_local_mission):
        print(f"\n[{fn.__name__}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\nRESULT {_PASS}/{_PASS + _FAIL} passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
