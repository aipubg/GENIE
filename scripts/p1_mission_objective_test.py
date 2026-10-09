"""B09 tests: action objectives require a verified execution receipt."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from missions.runner import (  # noqa: E402
    MissionRunner, classify_objective, OBJECTIVE_ACTION, OBJECTIVE_ARTIFACT,
    OBJECTIVE_REASONING)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class StubGateway:
    provider_id = "stub"
    model = "stub-1"

    def __init__(self, text="A convincing but unexecuted answer.", tool_calls=None):
        self._text = text
        self._tool_calls = tool_calls or []

    def complete(self, ctx, req, messages, **kwargs):
        class Completion:
            def __init__(self, text, calls):
                self.text = text
                self.tool_calls = calls
                self.tool_error = ""
                self.provider_id = "stub"
                self.model = "stub-1"
                self.raw = {}
                self.tool_protocol = "openai"
        return Completion(self._text, self._tool_calls)


class StubComputer:
    """A computer whose execution is never verified (the B09 hazard)."""

    def __init__(self, verified=False):
        self.verified = verified

    def execute(self, ctx, capability, params, cancel_event=None):
        class R:
            def __init__(self, verified):
                self._v = verified

            def to_dict(self):
                return {"ok": self._v, "verified": self._v, "capability": capability,
                        "detail": "stub"}
        return R(self.verified)


class StubComputerUnverified(StubComputer):
    def execute(self, ctx, capability, params, cancel_event=None):
        class R:
            def to_dict(self):
                return {"ok": True, "verified": False, "capability": capability,
                        "detail": "issued but not verified"}
        return R()


def main():
    # ---- 1. classification ---------------------------------------------------
    check("reasoning_explain", classify_objective("Explain how DNS works") == OBJECTIVE_REASONING)
    check("reasoning_summarize", classify_objective("Summarize the meeting notes") == OBJECTIVE_REASONING)
    check("action_open_send",
          classify_objective("Open WhatsApp and send the message") == OBJECTIVE_ACTION)
    check("artifact_create_doc",
          classify_objective("Create a report document in Downloads") == OBJECTIVE_ARTIFACT)
    check("artifact_download_pdf",
          classify_objective("Download the invoice pdf") == OBJECTIVE_ARTIFACT)
    check("hinglish_action", classify_objective("WhatsApp kholo aur bhejo") == OBJECTIVE_ACTION)
    check("reasoning_not_forced", classify_objective("Compare these two options") == OBJECTIVE_REASONING)

    # ---- 2. action objective with NO execution authority fails honestly -------
    runner = MissionRunner({"gateway": StubGateway(), "missions": None})
    out = runner._act("m1", "s1", "Open WhatsApp and send a message", None)
    check("action_without_executor_fails", out.get("ok") is False)
    check("action_without_executor_not_prose_ok",
          out.get("ok") is not True and "execution authority" in str(out.get("error", "")))

    # ---- 3. action objective whose tool never verifies fails ------------------
    runner2 = MissionRunner({"gateway": StubGateway(), "computer": StubComputerUnverified(),
                             "missions": None})
    out2 = runner2._act("m1", "s1", "Open notepad and type hello", None)
    check("unverified_action_not_complete", out2.get("ok") is False)
    check("unverified_action_reports_reason",
          "no verified execution receipt" in str(out2.get("error", "")).lower())

    # ---- 4. reasoning objective keeps working (prose is the deliverable) ------
    runner3 = MissionRunner({"gateway": StubGateway("DNS resolves names to addresses."),
                             "missions": None})
    out3 = runner3._act("m1", "s1", "Explain how DNS works", None)
    check("reasoning_objective_completes", out3.get("ok") is True)
    check("reasoning_evidence_kind", (out3.get("evidence") or {}).get("kind") == "reasoning")

    # ---- 5. _verify requires a VERIFIED receipt for capability evidence -------
    check("verify_rejects_no_receipts",
          MissionRunner._verify({"ok": True, "evidence": {"kind": "capability", "receipts": []}}) is False)
    check("verify_rejects_unverified_receipt",
          MissionRunner._verify({"ok": True, "evidence": {
              "kind": "capability", "receipts": [{"verified": False}]}}) is False)
    check("verify_accepts_verified_receipt",
          MissionRunner._verify({"ok": True, "evidence": {
              "kind": "capability", "receipts": [{"verified": True}]}}) is True)
    check("verify_accepts_reasoning",
          MissionRunner._verify({"ok": True, "evidence": {"kind": "reasoning"}}) is True)
    check("verify_rejects_not_ok",
          MissionRunner._verify({"ok": False, "evidence": {"kind": "reasoning"}}) is False)

    # ---- 6. artifact existence is still enforced -----------------------------
    missing = str(Path(tempfile.mkdtemp()) / "nope.txt")
    check("verify_rejects_missing_artifact",
          MissionRunner._verify({"ok": True, "evidence": {
              "kind": "capability", "receipts": [{"verified": True}],
              "results": [{"ok": True, "path": missing}]}}) is False)
    existing = Path(tempfile.mkdtemp()) / "yes.txt"
    existing.write_text("x", encoding="utf-8")
    check("verify_accepts_existing_artifact",
          MissionRunner._verify({"ok": True, "evidence": {
              "kind": "capability", "receipts": [{"verified": True}],
              "results": [{"ok": True, "path": str(existing)}]}}) is True)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Mission Action Objective Tests (B09) ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
