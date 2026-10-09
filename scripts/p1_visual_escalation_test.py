"""B06 automatic escalation tests: UIA gap -> grounded visual fallback."""
import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from computer.executor import Executor, ExecutionResult  # noqa: E402
from core.contracts import CallContext  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class FakeSettings:
    def __init__(self, consent):
        self.remote_visual_consent = consent


class FakeAwareness:
    def __init__(self, consent=True):
        self.settings = FakeSettings(consent)
        self.is_paused = False
        self.enabled = True


class FakeVisual:
    def __init__(self, ok=True):
        self._ok = ok
        self.calls = 0

    def observe(self, ctx, params, cancel):
        self.calls += 1
        if not self._ok:
            return {"ok": False, "error": "no eligible vision model"}
        return {"ok": True, "window_id": params["window_id"], "controls": [],
                "description": "stub", "grounding": "vision_model"}


def make_executor(visual_ok=True, consent=True):
    ex = Executor()
    ex.desktop_awareness = FakeAwareness(consent)
    ex.visual = FakeVisual(visual_ok)
    return ex


def main():
    ctx = CallContext(person_id="o", device_id="d", trace_id="t1")

    # 1. A UIA interaction failure with consent escalates automatically.
    ex = make_executor()
    result = ExecutionResult(False, "uia.find")
    result.detail = "no accessible control matched"
    esc = ex._maybe_escalate_to_visual(ctx, "uia.find", {"window_id": 1234}, result,
                                       threading.Event())
    check("escalates_on_uia_find", esc is not None)
    check("escalation_error_code", getattr(esc, "error_code", "") == "TARGET_NOT_EXPOSED_BY_UIA")
    check("escalation_names_next_tool",
          (esc.result.get("escalation") or {}).get("next_tool") == "desktop_visual_action")
    check("escalation_attaches_visual_observation", bool(esc.result.get("visual")))
    check("escalation_did_not_claim_success", esc.ok is False and esc.verified is False)
    check("visual_observe_was_called_once", ex.visual.calls == 1)

    # 2. Non-UIA capabilities never escalate.
    ex = make_executor()
    r2 = ExecutionResult(False, "system.volume.set")
    check("no_escalation_for_non_uia",
          ex._maybe_escalate_to_visual(ctx, "system.volume.set", {}, r2, threading.Event()) is None)

    # 3. No owner consent -> no escalation (privacy preserved).
    ex = make_executor(consent=False)
    r3 = ExecutionResult(False, "uia.invoke")
    check("no_escalation_without_consent",
          ex._maybe_escalate_to_visual(ctx, "uia.invoke", {"window_id": 9}, r3, threading.Event()) is None)

    # 4. Visual route unavailable -> honest structured failure, still bounded.
    ex = make_executor(visual_ok=False)
    r4 = ExecutionResult(False, "uia.set_value")
    r4.detail = "element not found"
    out = ex._maybe_escalate_to_visual(ctx, "uia.set_value", {"window_id": 7}, r4, threading.Event())
    check("unavailable_visual_is_honest", out is not None and out.ok is False)
    check("unavailable_visual_marks_attempted",
          (out.result.get("escalation") or {}).get("available") is False)
    check("unavailable_visual_no_loop", ex.visual.calls == 1)

    # 5. Escalation is bounded: exactly one observe call, never a loop.
    ex = make_executor()
    for _ in range(3):
        r = ExecutionResult(False, "uia.focus")
        ex._maybe_escalate_to_visual(ctx, "uia.focus", {"window_id": 5}, r, threading.Event())
    check("escalation_bounded_per_call", ex.visual.calls == 3)

    # 6. Missing window id falls back to the foreground window without crashing.
    ex = make_executor()
    r6 = ExecutionResult(False, "uia.select")
    ex._maybe_escalate_to_visual(ctx, "uia.select", {}, r6, threading.Event())
    check("missing_hwnd_handled", True)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== B06 Automatic Escalation Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
