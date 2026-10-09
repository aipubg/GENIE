"""Phase 1 closure — login-required and unsupported-feature blocking.

Authentication is NEVER bypassed: when a page needs a session, GENIE must stop
and ask the owner. An unsupported clause must be reported as BLOCKED, never
narrated as done.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_gate_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    from core.config import get_config
    from browser.service import get_browser
    from browser.website_task import WebsiteTaskEngine

    cfg = get_config()
    b = get_browser(workspace_root=cfg.data_dir / cfg.get("computer.workspace", "workspace"))

    print("=" * 70)
    print("Phase 1 — login-required / unsupported-feature blocking")
    print("=" * 70)

    # ---- 1) a local sign-in wall is detected ----
    login_page = (REPO / "tests" / "fixtures" / "login_page.html").as_uri()
    b.navigate({"url": login_page, "wait_s": 20, "content_wait_s": 6})
    obs = b.observe({})
    gate = (obs.get("gate") or {}).get("gate", "")
    check("a sign-in wall is detected as a gate", gate == "sign_in",
          f"gate={gate!r} detail={str((obs.get('gate') or {}).get('detail'))[:70]}")

    # ---- 2) the real ChatGPT surface: session state is reported, not assumed ----
    # The adapter owns browser selection, so it is driven directly (switching
    # browsers by hand leaves a stale CDP client).
    res = b.chatgpt_image({"browser": "brave",
                           "prompt": "swimming pool mein AI character ki image",
                           "timeout_s": 45})
    steps = {s.get("step"): s for s in res.get("steps") or []}
    print("    ---- adapter step receipts ----")
    for s in res.get("steps") or []:
        print(f"      {s.get('step'):15} ok={s.get('ok')}  {str(s.get('detail'))[:80]}")
    check("ChatGPT opens in the authorized Brave browser",
          bool((steps.get("browser") or {}).get("ok"))
          and bool((steps.get("navigate") or {}).get("ok")),
          str((steps.get("navigate") or {}).get("detail", ""))[:80])
    obs_detail = str((steps.get("observe") or {}).get("detail", ""))
    g2 = "sign_in" if "gate=sign_in" in obs_detail else ("" if "gate=none" in obs_detail
                                                         else "unknown")
    print(f"    chatgpt observed gate={g2!r} ({obs_detail[:90]})")
    check("the ChatGPT session state is detected (never assumed)",
          g2 in ("sign_in", ""), f"gate={g2!r}")
    if g2 == "sign_in":
        check("image generation is BLOCKED when sign-in is required",
              bool(res.get("blocked")) and res.get("needs_owner") is True,
              f"blocked={res.get('blocked')} needs_owner={res.get('needs_owner')}")
        check("authentication is never bypassed (no prompt was submitted)",
              not any(s.get("step") == "prompt" and s.get("ok")
                      for s in res.get("steps") or []),
              str([s.get("step") for s in res.get("steps") or []]))
    else:
        check("with a session present, the run is still honestly reported",
              isinstance(res.get("steps"), list) and len(res.get("steps") or []) >= 3,
              f"state detail={str(res.get('detail'))[:80]}")

    # ---- 3) an unsupported clause is BLOCKED, never narrated ----
    plan = [
        {"id": "s1", "capability": "plan.unsupported",
         "params": {"clause": "send an email to the team"}, "depends_on": []},
        {"id": "s2", "capability": "browser.observe", "params": {}, "depends_on": ["s1"]},
    ]
    run = WebsiteTaskEngine(b.handle).run(plan)
    st = {s["id"]: s["status"] for s in run.get("steps") or []}
    check("an unsupported clause is reported BLOCKED", st.get("s1") == "blocked", str(st))
    check("the run is FAILED, not COMPLETED", run.get("state") == "FAILED",
          str(run.get("state")))
    check("the dependent step did not run",
          "s2" not in st and any(n.get("index") == 2 for n in run.get("not_attempted") or []),
          str(run.get("not_attempted")))

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
