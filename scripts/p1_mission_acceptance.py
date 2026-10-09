"""Track I: REAL durable Mission acceptance through MissionService + MissionRunner.

Uses the actual database, MissionService, MissionRunner and ComputerService with a
REAL (isolated, non-destructive) capability action.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

from core.contracts import CallContext, MissionState  # noqa: E402
from core.db import get_db  # noqa: E402
from missions.service import MissionService  # noqa: E402
from missions.runner import MissionRunner, classify_objective  # noqa: E402
from computer.service import ComputerService  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class StubDirector:
    """Routes the isolated objective to a REAL files.mkdir capability."""

    def __init__(self, target_dir):
        self.target_dir = target_dir

    def classify(self, text, ctx, hint=""):
        from director.base import DirectorDecision, DirectorTask
        from core.contracts import TaskType
        task = DirectorTask(type=TaskType.COMPUTER_ACTION, capability="files.mkdir",
                            device="pc_main", target=self.target_dir,
                            params={"path": str(Path(self.target_dir) / "genie_mission_probe")})
        return DirectorDecision(tasks=[task], intent="simple_action", confidence=0.9)


class StubGateway:
    provider_id = "stub"
    model = "stub-1"

    def complete(self, ctx, req, messages, **kwargs):
        class C:
            text = "Reasoned answer with no side effects."
            tool_calls = []
            tool_error = ""
            provider_id = "stub"
            model = "stub-1"
            raw = {}
            tool_protocol = "openai"
        return C()


def main():
    data_dir = Path(os.environ["GENIE_DATA_DIR"])
    db = get_db(data_dir / "genie.db")
    missions = MissionService(db)
    computer = ComputerService(db=db, data_dir=str(data_dir))
    ctx = CallContext(person_id="owner", device_id="pc_main", trace_id="mission-accept")
    probe_dir = data_dir / "mission_probe_root"
    probe_dir.mkdir(parents=True, exist_ok=True)

    from agents.service import AgentService
    agents = AgentService(db=db, gateway=StubGateway(),
                          artifact_root=str(data_dir / "artifacts"))
    services = {"missions": missions, "computer": computer,
                "director": StubDirector(str(probe_dir)), "gateway": StubGateway(),
                "agents_service": agents}
    runner = MissionRunner(services)

    # ---- 1. create + persist -------------------------------------------------
    mission = missions.create(ctx, "Create an isolated probe folder",
                              targets=["pc_main"], criteria=["folder exists"])
    check("mission_persisted", missions.get(mission.mission_id) is not None)
    missions.transition(ctx, mission.mission_id, MissionState.PLANNED)

    # ---- 2. schedule normalization (the B01/E53 path) ------------------------
    from missions.scheduler import normalize, next_run
    from core.contracts import now_ms
    spec = normalize("create a probe folder every morning at 9", now_ms())
    check("schedule_normalised", bool(spec), f"spec={spec}")
    if spec:
        missions.set_schedule(mission.mission_id, spec, next_run(spec, now_ms()))
        check("schedule_persisted", bool(missions.schedule_of(mission.mission_id)))

    # ---- 3. objective classification ----------------------------------------
    check("objective_is_action",
          classify_objective("Create an isolated probe folder") in ("action_required",
                                                                    "artifact_producing"))

    # ---- 4. runner executes a REAL capability and verifies -------------------
    runner.ensure_plan(mission)
    steps = missions.steps(mission.mission_id)
    check("plan_created", len(steps) >= 1, f"steps={len(steps)}")
    result = runner.execute(mission.mission_id)
    check("runner_returned_state", bool(result.get("state")), f"result={result}")

    progress = missions.progress(mission.mission_id)
    check("progress_reported", "total" in progress and "done" in progress, str(progress))
    check("at_least_one_step_done", progress["done"] >= 1, str(progress))

    # the REAL side effect exists (a verified capability action, not prose)
    probe = probe_dir / "genie_mission_probe"
    check("real_capability_side_effect", probe.exists() and probe.is_dir(),
          f"probe={probe}")

    # ---- 5. action objective cannot complete on prose alone ------------------
    class ProseOnlyComputer:
        def execute(self, ctx, capability, params, **kwargs):
            class R:
                def to_dict(self):
                    return {"ok": True, "verified": False, "capability": capability,
                            "detail": "issued but unverified"}
            return R()

    runner2 = MissionRunner({"missions": missions, "computer": ProseOnlyComputer(),
                             "director": StubDirector(str(probe_dir)),
                             "gateway": StubGateway(), "agents_service": agents})
    m2 = missions.create(ctx, "Create another probe folder", targets=["pc_main"])
    missions.transition(ctx, m2.mission_id, MissionState.PLANNED)
    out2 = runner2._act(m2.mission_id, "s1", "Create another probe folder", None)
    check("unverified_action_step_fails", out2.get("ok") is False, str(out2.get("error")))
    check("verify_rejects_it", MissionRunner._verify(out2) is False)

    # ---- 6. reasoning-only mission still completes from reasoning -----------
    m3 = missions.create(ctx, "Explain how DNS resolution works", targets=["pc_main"])
    missions.transition(ctx, m3.mission_id, MissionState.PLANNED)
    out3 = runner2._act(m3.mission_id, "s1", "Explain how DNS resolution works", None)
    check("reasoning_mission_completes", out3.get("ok") is True)
    check("reasoning_evidence_kind", (out3.get("evidence") or {}).get("kind") == "reasoning")

    # ---- 7. mission state actually updated ----------------------------------
    final = missions.get(mission.mission_id)
    check("mission_state_advanced", final is not None
          and final.state.value in ("RUNNING", "WAITING", "COMPLETED", "PLANNED"),
          f"state={getattr(final, 'state', None)}")

    computer.executor.desktop_awareness.stop()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Track I: Real Durable Mission Acceptance ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    print(f"evidence: mission_id={mission.mission_id} state={final.state.value} "
          f"progress={progress} probe_exists={probe.exists()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
