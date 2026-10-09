"""Message transaction semantics tests (the {Enter} payload defect)."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from core.contracts import CallContext  # noqa: E402
from computer import messages as messages_mod  # noqa: E402
from computer.capability_manifest import looks_like_action_token  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class FakeUia:
    """Minimal stand-in for computer.uia, with mutable conversation state."""

    def __init__(self):
        self.window = "WhatsApp"
        self.window_id = 111
        self.process_id = 222
        self.values = {"r": "Aman", "c": "", "s": ""}
        self.sent = 0
        self.history = []

    def describe_registered(self, element_id):
        if element_id not in ("r", "c", "s"):
            return None
        names = {"r": "Recipient header", "c": "Composer", "s": "Send"}
        return {"element_id": element_id, "window": self.window,
                "window_id": self.window_id, "process_id": self.process_id,
                "name": names[element_id], "control_type": "Edit" if element_id != "s" else "Button"}

    def get_value(self, element_id):
        return {"ok": True, "value": self.values.get(element_id, "")}

    def set_value(self, element_id, value):
        self.values[element_id] = value
        return {"ok": True}

    def invoke_once(self, element_id):
        self.sent += 1
        text = self.values.get("c", "")
        self.history.append((self.values.get("r"), text))
        self.values["c"] = ""
        return {"ok": True}

    def find_elements(self, **kwargs):
        name = kwargs.get("name", "")

        class Item:
            def __init__(self, n, t):
                self.name = n
                self.control_type = t
        return [Item(n, "Text") for (r, n) in self.history if n == name]


class FakeApprovals:
    def __init__(self, approve=True):
        self.approve = approve
        self.last_summary = ""

    def request(self, ctx, summary, cancel):
        self.last_summary = summary
        if not self.approve:
            return {"ok": False, "error": "owner declined"}
        return {"ok": True}


def main():
    ctx = CallContext(person_id="o", device_id="d", trace_id="t1")

    def setup(approve=True):
        fake = FakeUia()
        messages_mod.uia = fake
        approvals = FakeApprovals(approve)
        return messages_mod.MessageTransactions(approvals, None), fake, approvals

    # ---- 1. action tokens can never become message text ---------------------
    for token in ("{Enter}", "{Tab}", " {Esc} ", "{F5}", "{Ctrl+v}", "^s", "+{Tab}"):
        tx, fake, _ = setup()
        r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                             "send_element_id": "s", "recipient": "Aman", "message": token})
        check(f"rejects_action_token_{token.strip('{}^+ ')}",
              (not r.get("ok")) and r.get("error_code") == "action_token_not_message",
              str(r.get("error_code")))

    # ---- 2. message content cannot be replaced by an input action -----------
    tx, fake, _ = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman", "message": "{Enter}"})
    check("no_transaction_created_for_action_token", not r.get("ok"))
    check("composer_not_written_with_action_token", fake.values["c"] == "")
    check("nothing_sent_for_action_token", fake.sent == 0)

    # ---- 3. a genuine message still works and is frozen ----------------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman",
                         "message": "Meeting at 5pm"})
    check("valid_message_prepares", r.get("ok") is True and r.get("verified") is True)
    check("composer_holds_exact_message", fake.values["c"] == "Meeting at 5pm")
    check("transaction_has_id", bool(r.get("transaction_id")))
    check("transaction_binds_conversation",
          (r.get("conversation") or {}).get("header") == "Aman"
          and (r.get("conversation") or {}).get("window_id") == 111)
    check("send_action_is_separate",
          (r.get("send_action") or {}).get("kind") == "invoke")
    check("prepared_message_is_not_an_action",
          not looks_like_action_token(r.get("message")))

    # ---- 4. recipient cannot change after approval ---------------------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman",
                         "message": "Hello"})
    token = r["transaction_id"]
    fake.values["r"] = "Someone else"      # conversation changed before send
    out = tx.send(ctx, {"transaction_id": token}, _Cancel())
    check("recipient_change_blocks_send",
          (not out.get("ok")) and out.get("error_code") == "message_changed",
          str(out.get("error_code")))
    check("no_send_after_recipient_change", fake.sent == 0)

    # ---- 5. stale HWND / window invalidates the transaction -----------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman",
                         "message": "Hello"})
    token = r["transaction_id"]
    fake.window_id = 999                    # window replaced
    out = tx.send(ctx, {"transaction_id": token}, _Cancel())
    check("stale_hwnd_blocks_send",
          (not out.get("ok")) and out.get("error_code") == "message_changed",
          str(out.get("error_code")))
    check("no_send_after_hwnd_change", fake.sent == 0)

    # ---- 6. draft change invalidates the transaction -------------------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman",
                         "message": "Hello"})
    token = r["transaction_id"]
    fake.values["c"] = "tampered draft"
    out = tx.send(ctx, {"transaction_id": token}, _Cancel())
    check("draft_change_blocks_send", (not out.get("ok")) and fake.sent == 0)

    # ---- 7. the approval summary shows the MESSAGE, never a key token --------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman",
                         "message": "Meeting at 5pm"})
    tx.send(ctx, {"transaction_id": r["transaction_id"]}, _Cancel())
    check("summary_shows_exact_message", "Meeting at 5pm" in approvals.last_summary)
    check("summary_labels_send_as_action",
          "not part of the message" in approvals.last_summary)
    check("summary_has_no_bare_action_token_as_message",
          "Exact message" in approvals.last_summary
          and "{Enter}" not in approvals.last_summary)

    # ---- 8. owner decline sends nothing -------------------------------------
    tx, fake, approvals = setup(approve=False)
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman", "message": "Hello"})
    out = tx.send(ctx, {"transaction_id": r["transaction_id"]}, _Cancel())
    check("owner_decline_blocks_send", (not out.get("ok")) and fake.sent == 0)

    # ---- 9. exactly-once send ------------------------------------------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman", "message": "Hello"})
    tx.send(ctx, {"transaction_id": r["transaction_id"]}, _Cancel())
    tx.send(ctx, {"transaction_id": r["transaction_id"]}, _Cancel())
    check("transaction_is_single_use", fake.sent == 1, f"sent={fake.sent}")

    # ---- 10. transaction is owner-bound -------------------------------------
    tx, fake, approvals = setup()
    r = tx.prepare(ctx, {"recipient_element_id": "r", "composer_element_id": "c",
                         "send_element_id": "s", "recipient": "Aman", "message": "Hello"})
    other = CallContext(person_id="intruder", device_id="d", trace_id="t2")
    out = tx.send(other, {"transaction_id": r["transaction_id"]}, _Cancel())
    check("transaction_owner_bound",
          (not out.get("ok")) and out.get("error_code") == "stale_transaction")
    check("no_send_for_other_owner", fake.sent == 0)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Message Transaction Semantics Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


class _Cancel:
    def is_set(self):
        return False

    def wait(self, timeout=None):
        return False


if __name__ == "__main__":
    sys.exit(main())
