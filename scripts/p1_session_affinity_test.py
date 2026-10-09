"""Task-scoped browser session affinity tests (E37/B05) + handoff annotation."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from browser.service import BrowserService  # noqa: E402
from director.heuristics import plan_web_action  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    svc = BrowserService()

    # 1. invalid mode is rejected
    r = svc.select_session({"mode": "bogus"})
    check("invalid_mode_rejected", not r.get("ok"))

    # 2. owner_existing fails closed when attachment cannot be verified
    r = svc.select_session({"mode": "owner_existing", "browser": "brave"})
    check("owner_existing_fails_closed",
          (not r.get("ok")) and r.get("blocked") and r.get("needs_owner_action"))
    check("owner_existing_error_code", r.get("error_code") == "owner_browser_not_attachable")

    # 3. no silent substitution: session stays unbound after a failed attach
    check("no_session_after_failed_attach", svc.session_context() == {})

    # 4. session_context is always a copy, never the live dict
    svc._task_session = {"mode": "genie_owned", "control": "cdp"}
    ctx = svc.session_context()
    ctx["mode"] = "tampered"
    check("session_context_returns_copy", svc.session_context()["mode"] == "genie_owned")

    # 5. handoff capabilities carry expect/verify and the attach requirement
    plan = plan_web_action("chrome mein github kholo aur wikipedia kholo")
    check("handoff_plan_has_expect", all(str(s.get("expect", "")) for s in plan),
          str([s.get("expect") for s in plan]))
    check("handoff_plan_has_verify",
          all((s.get("verify") or {}).get("url_contains") for s in plan),
          str([s.get("verify") for s in plan]))
    check("handoff_plan_requires_attach",
          all(s.get("requires_attach") is True for s in plan),
          str([s.get("requires_attach") for s in plan]))
    check("handoff_plan_expects_attach",
          all(s.get("expect_attach") for s in plan))

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Browser Session Affinity Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
