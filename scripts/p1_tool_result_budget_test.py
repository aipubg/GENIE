"""B02 (no ungrounded done) + B10 (compact tool results) tests."""
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from core import tool_dialogue  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    # ---- B10: compact results -------------------------------------------------
    big = {
        "ok": True, "verified": True, "capability": "browser.observe",
        "detail": "observed",
        "data": {"url": "https://example.com/x", "title": "T", "tab_id": "t1",
                 "document_id": "d1",
                 "controls": [{"element_id": f"e{i}", "label": "L" * 80, "text": "x" * 200}
                              for i in range(500)]},
        "verification": {"verified": True, "method": "dom", "detail": "ok"},
    }
    s = tool_dialogue._compact_result(big)
    parsed = json.loads(s)          # must be valid JSON, never truncated mid-structure
    check("compact_is_valid_json", isinstance(parsed, dict))
    check("compact_preserves_outcome", parsed.get("ok") is True and parsed.get("verified") is True)
    check("compact_preserves_identity",
          parsed.get("url") == "https://example.com/x" and parsed.get("tab_id") == "t1")
    check("compact_preserves_verification", parsed.get("verification", {}).get("method") == "dom")
    check("compact_is_bounded", len(s) < 8000, f"len={len(s)}")

    fail = {"ok": False, "capability": "uia.find",
            "error_code": "TARGET_NOT_EXPOSED_BY_UIA",
            "detail": "d" * 20000, "data": {"window_id": 99}}
    fs = json.loads(tool_dialogue._compact_result(fail))
    check("compact_preserves_failure_code",
          fs.get("error_code") == "TARGET_NOT_EXPOSED_BY_UIA")
    check("compact_preserves_continuation_identity", fs.get("window_id") == 99)

    # escalation payload survives compaction so the model can continue
    esc = {"ok": False, "capability": "uia.invoke", "error_code": "TARGET_NOT_EXPOSED_BY_UIA",
           "result": {"escalation": {"from": "uia.invoke", "to": "desktop.visual_observe",
                                     "next_tool": "desktop_visual_action"},
                      "visual": {"ok": True}}}
    es = json.loads(tool_dialogue._compact_result(esc))
    check("compact_keeps_escalation", bool(es.get("escalation")))

    # a pathological tiny budget still returns valid JSON
    tiny = tool_dialogue._compact_result(big, budget=200)
    check("compact_valid_under_tiny_budget", isinstance(json.loads(tiny), dict))

    # ---- B02: action intent + no-receipt guard --------------------------------
    check("action_intent_detected", tool_dialogue.action_requested("notifications kholo"))
    check("conversation_not_action", not tool_dialogue.action_requested("what is the capital of France"))
    src = inspect.getsource(tool_dialogue.run)
    check("run_guards_ungrounded_done", "action_reprompted" in src)
    check("run_uses_compact_results", "_compact_result(result)" in src)
    check("run_no_raw_18000_slice", "[:18000]" not in src)
    check("run_reports_no_receipt_honestly",
          "no tool ran, so there is no verified" in src)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== B02 / B10 Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
