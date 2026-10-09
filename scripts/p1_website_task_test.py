"""Phase 1 closure — general bounded website-task execution.

PLAN -> ACT -> OBSERVE -> VERIFY -> NEXT STEP, on a page that is NOT ChatGPT, so
the engine is proven general rather than site-specific.

Covers:
  * the plan carries capabilities, dependencies, expected observations and
    verification conditions;
  * every step is executed and observed (a real receipt, never narration);
  * a dependent step is NOT attempted after an unrecovered failure;
  * cancellation belongs to the active run and does NOT cancel the next one;
  * the step bound is enforced and overflow is reported as NOT ATTEMPTED.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_wtask_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    from director.heuristics import plan_web_action

    print("=" * 70)
    print("Phase 1 — general bounded website-task execution")
    print("=" * 70)

    # ---- 1) the planner emits an annotated, bounded, ordered plan ----
    plan = plan_web_action("chrome mein github kholo aur wikipedia kholo")
    check("multi-clause web request yields an ordered plan", len(plan) == 2,
          f"{len(plan)} steps: {[s.get('capability') for s in plan]}")
    check("every step carries an id and a dependency",
          all(s.get("id") for s in plan) and plan[1].get("depends_on") == ["s1"],
          f"ids={[s.get('id') for s in plan]} deps={[s.get('depends_on') for s in plan]}")
    check("every step carries an expected observation",
          all(str(s.get("expect", "")) for s in plan),
          "; ".join(str(s.get("expect"))[:40] for s in plan))
    check("navigate steps carry a verification condition",
          all((s.get("verify") or {}).get("url_contains") for s in plan
              if s.get("capability") == "browser.navigate"),
          str([s.get("verify") for s in plan]))
    yt = plan_web_action("YouTube open karo aur Barsaat song play karo.")
    check("media request routes to the browser authority",
          [s.get("capability") for s in yt] == ["browser.media.play"],
          str([s.get("capability") for s in yt]))
    cg = plan_web_action("Brave browser mein ChatGPT kholo, new chat shuru karo aur "
                         "swimming pool mein AI character ki image generate karwao.")
    check("owner's Brave/ChatGPT example plans the adapter with browser=brave",
          [s.get("capability") for s in cg] == ["browser.chatgpt.image"]
          and (cg[0].get("params") or {}).get("browser") == "brave",
          str(cg[0].get("params")))

    # ---- engine, against a NON-ChatGPT page (generality) ----
    from core.config import get_config
    from browser.service import get_browser
    from browser.website_task import WebsiteTaskEngine

    cfg = get_config()
    b = get_browser(workspace_root=cfg.data_dir / cfg.get("computer.workspace", "workspace"))
    local = (REPO / "tests" / "fixtures" / "primitives_test.html").as_uri()
    marker = "clicked:genie website task"

    task_plan = [
        {"id": "s1", "capability": "browser.navigate",
         "params": {"url": local}, "depends_on": [],
         "expect": "the local test page is loaded",
         "verify": {"url_contains": "primitives_test.html"}},
        {"id": "s2", "capability": "browser.fill",
         "params": {"selector": "#q", "text": "genie website task"}, "depends_on": ["s1"],
         "expect": "the search field holds the text"},
        {"id": "s3", "capability": "browser.act",
         "params": {"selector": "#go"}, "depends_on": ["s2"],
         "expect": "the button is activated and the result appears",
         "verify": {"text": marker}},
    ]

    eng = WebsiteTaskEngine(b.handle)
    run = eng.run(task_plan)
    for s in run.get("steps") or []:
        print(f"    {s['index']} {s['capability']:18} {s['status']:12} "
              f"obs={s.get('observation', {}).get('title')!r} "
              f"{str(s.get('receipt'))[:60]}")

    check("bounded plan executed to completion", run.get("state") == "COMPLETED",
          str(run.get("state")))
    check("every step has an observation from the live page",
          all((s.get("observation") or {}).get("ok") for s in run.get("steps") or []),
          str([(s.get("observation") or {}).get("url", "")[:40]
               for s in run.get("steps") or []]))
    check("verification conditions were actually checked",
          bool((run["steps"][0].get("checks") or []))
          and bool((run["steps"][2].get("checks") or [])),
          str(run["steps"][2].get("checks"))[:80])
    check("no step is claimed without a receipt",
          all(str(s.get("receipt") or "") for s in run.get("steps") or []), "receipts present")

    # ---- 2) dependent-step failure: step 2 must NOT run ----
    bad_plan = [
        {"id": "s1", "capability": "browser.act",
         "params": {"selector": "#does-not-exist"}, "depends_on": [],
         "expect": "a control that does not exist"},
        {"id": "s2", "capability": "browser.verify",
         "params": {"text": "never checked"}, "depends_on": ["s1"],
         "expect": "must not run because s1 failed"},
    ]
    run2 = WebsiteTaskEngine(b.handle).run(bad_plan)
    st = {s["id"]: s["status"] for s in run2.get("steps") or []}
    na = run2.get("not_attempted") or []
    check("a failing step is reported FAILED (not success)",
          st.get("s1") in ("failed", "blocked"), str(st))
    check("the dependent step is NOT attempted",
          "s2" not in st and any(n.get("index") == 2 for n in na),
          f"executed={list(st)} not_attempted={[n.get('index') for n in na]}")
    check("the run state is FAILED, never COMPLETED", run2.get("state") == "FAILED",
          str(run2.get("state")))

    # ---- 3) cancellation belongs to THIS run only ----
    ev = threading.Event()
    ev.set()
    run3 = WebsiteTaskEngine(b.handle, cancel_event=ev).run(task_plan)
    check("a pre-set cancel stops the run instead of executing it",
          run3.get("state") == "CANCELLED",
          f"state={run3.get('state')} boundary={run3.get('cancel_boundary')}")
    check("cancelled steps are reported, remaining ones NOT ATTEMPTED",
          any(s.get("status") == "cancelled" for s in run3.get("steps") or [])
          and len(run3.get("not_attempted") or []) >= 1,
          f"steps={[s.get('status') for s in run3.get('steps') or []]} "
          f"not_attempted={len(run3.get('not_attempted') or [])}")

    # the SAME event cleared -> the next request must run normally (no global flag)
    ev.clear()
    run4 = WebsiteTaskEngine(b.handle, cancel_event=ev).run(task_plan)
    check("clearing the cancel lets the NEXT request run (not a global flag)",
          run4.get("state") == "COMPLETED",
          f"state={run4.get('state')} cancel_requested={run4.get('cancel_requested')}")

    # ---- 4) Stop DURING a bounded multi-step run ----
    # A step that already completed must keep its real status; only work that was
    # not started may be called cancelled.
    mid = threading.Event()

    def _stop_soon() -> None:
        # lands DURING step 1, so step 1 completes and step 2 is the boundary
        time.sleep(0.05)
        mid.set()

    threading.Thread(target=_stop_soon, daemon=True).start()
    slow = [
        {"id": "s1", "capability": "browser.navigate", "params": {"url": local},
         "depends_on": [], "expect": "page loaded"},
        {"id": "s2", "capability": "browser.observe", "params": {}, "depends_on": ["s1"],
         "expect": "observed"},
        {"id": "s3", "capability": "browser.observe", "params": {}, "depends_on": ["s2"],
         "expect": "observed"},
    ]
    run6 = WebsiteTaskEngine(b.handle, cancel_event=mid).run(slow)
    statuses = [s.get("status") for s in run6.get("steps") or []]
    check("Stop during the run is reported as a cancellation of THIS run",
          run6.get("state") == "CANCELLED" and run6.get("cancel_requested") is True,
          f"state={run6.get('state')} boundary={run6.get('cancel_boundary')}")
    check("a step that already completed is NOT relabelled CANCELLED",
          "cancelled" not in statuses[:1] and statuses[:1] != ["cancelled"],
          f"statuses={statuses}")
    check("the remaining steps were NOT attempted",
          len(run6.get("not_attempted") or []) >= 1
          or "cancelled" in statuses,
          f"statuses={statuses} not_attempted={len(run6.get('not_attempted') or [])}")

    # ---- 5) routing regressions: fullscreen and media control stay in their lane ----
    fs = plan_web_action("browser mein video fullscreen karo")
    check("fullscreen against the browser routes to browser.fullscreen",
          [s.get("capability") for s in fs] == ["browser.fullscreen"],
          str([s.get("capability") for s in fs]))
    check("media CONTROL (pause) is not turned into a browser play action",
          plan_web_action("song pause karo") == [],
          str([s.get("capability") for s in plan_web_action("song pause karo")]))
    check("a plain song request still routes to verified browser playback",
          [s.get("capability") for s in plan_web_action("Barsaat song bajao")]
          == ["browser.media.play"],
          str([s.get("capability") for s in plan_web_action("Barsaat song bajao")]))

    # ---- 6) the step bound is enforced ----
    many = [{"id": f"s{i}", "capability": "browser.observe", "params": {},
             "depends_on": ([f"s{i-1}"] if i > 1 else [])} for i in range(1, 12)]
    run5 = WebsiteTaskEngine(b.handle, max_steps=3).run(many)
    check("the plan is bounded (max_steps enforced)",
          len(run5.get("steps") or []) <= 3 and len(run5.get("not_attempted") or []) >= 1,
          f"executed={len(run5.get('steps') or [])} "
          f"not_attempted={len(run5.get('not_attempted') or [])}")

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
