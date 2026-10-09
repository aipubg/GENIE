"""Phase 1 corrective — targeted execution-contract tests (§10).

Covers the checks the gate asks for that the example tests do not:
  * an intentionally failing app-resolution
  * a dependent-step failure (later steps do NOT run)
  * Stop / cancellation during a multi-step action
  * a simple one-time task creates no durable Mission
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_targeted_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)

    import importlib
    import core.config
    importlib.reload(core.config)
    from core.lifecycle import Daemon
    from core.config import get_config
    from core.contracts import CallContext, TaskType
    from director.base import DirectorDecision, DirectorTask

    cfg = get_config()
    d = Daemon(cfg)
    d.start()
    from core.ipc.server import IPCServer
    port = int(cfg.get("ipc.port", 8787))
    srv = IPCServer(d, host=cfg.get("ipc.host", "127.0.0.1"), port=port)
    srv.start(background=True)
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)

    orch = d.orchestrator
    ctx = CallContext(person_id="owner", session_id="owner", trace_id="targeted")
    ms = d.services.get("missions")

    print("=" * 70)
    print("Phase 1 — targeted execution-contract tests")
    print("=" * 70)

    # 1) intentionally failing app-resolution
    dec = DirectorDecision(tasks=[DirectorTask(TaskType.APPLICATION_ACTION, "pc_main",
                                               "application.open", target="notarealapp")])
    res = orch._run_tasks("open notarealapp", ctx, dec, [])
    st = (res.get("steps") or [{}])[0]
    check("failing app-resolution is reported as failed",
          st.get("status") == "failed" and res.get("state") == "FAILED",
          f"status={st.get('status')} receipt={str(st.get('receipt'))[:70]}")
    check("failure names the real reason (not installed)",
          "install" in str(st.get("receipt", "")).lower()
          or "install" in str(st.get("error", "")).lower(),
          str(st.get("receipt"))[:80])

    # 2) dependent-step failure: step 1 fails -> steps 2,3 must NOT run
    ran: list[str] = []
    orig_exec = orch._execute_task

    def spy(ctx_, task):
        ran.append(task.get("capability", ""))
        if len(ran) == 1:
            return {"ok": False, "verified": False, "error": "boom"}
        return orig_exec(ctx_, task)

    orch._execute_task = spy
    dec2 = DirectorDecision(tasks=[
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 10}),
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 20}),
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 30}),
    ])
    res2 = orch._run_tasks("multi", ctx, dec2, [])
    orch._execute_task = orig_exec
    check("dependent steps do NOT run after an unrecovered failure",
          len(ran) == 1 and len(res2.get("not_attempted", [])) == 2,
          f"executed={len(ran)} not_attempted={len(res2.get('not_attempted'))}")
    check("failed run state is FAILED", res2.get("state") == "FAILED", str(res2.get("state")))
    check("reply distinguishes done vs not-done",
          "nahi" in str(res2.get("reply", "")).lower(),
          str(res2.get("reply"))[:90])

    # 3) Stop / cancellation during a multi-step action
    orch.cancel_event.clear()
    ran2: list[str] = []

    def spy_cancel(ctx_, task):
        ran2.append(task.get("capability", ""))
        out = orig_exec(ctx_, task)
        orch.cancel_event.set()          # owner hits Stop during step 1
        return out

    orch._execute_task = spy_cancel
    dec3 = DirectorDecision(tasks=[
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 11}),
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 22}),
        DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.set", params={"level": 33}),
    ])
    res3 = orch._run_tasks("multi", ctx, dec3, [])
    orch._execute_task = orig_exec
    orch.cancel_event.clear()
    statuses = [s.get("status") for s in (res3.get("steps") or [])]
    check("cancellation stops the remaining steps",
          len(ran2) == 1 and res3.get("state") == "CANCELLED",
          f"executed={len(ran2)} state={res3.get('state')}")
    check("cancelled step is marked cancelled, rest not attempted",
          "cancelled" in statuses and len(res3.get("not_attempted", [])) >= 1,
          f"statuses={statuses} not_attempted={len(res3.get('not_attempted', []))}")
    check("cancel reply tells the owner it was stopped",
          "rok" in str(res3.get("reply", "")).lower(),
          str(res3.get("reply"))[:90])

    # 4) simple one-time task creates no durable Mission
    before = len(ms.list(limit=200)) if ms else 0
    orch._run_tasks("volume 30", ctx,
                    DirectorDecision(tasks=[DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                                         "system.volume.set", params={"level": 30})]),
                    [])
    after = len(ms.list(limit=200)) if ms else 0
    check("simple action creates NO durable Mission", after == before,
          f"missions {before} -> {after}")

    # 5) the IPC cancel endpoint exists and is wired
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/action/cancel",
                                     data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as r:
            body = r.read().decode()
        check("IPC /api/action/cancel responds", '"ok"' in body, body[:80])
    except Exception as exc:
        check("IPC /api/action/cancel responds", False, str(exc))

    d.stop()
    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
