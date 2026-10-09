"""Recipient/draft-bound transactions over existing accessible application controls."""
import threading
import time
import uuid

from . import uia
from .capability_manifest import looks_like_action_token


def failure(code, detail, **extra):
    return {"ok": False, "error_code": code, "error": detail, **extra}


class MessageTransactions:
    def __init__(self, approvals, desktop_lock=None):
        self.approvals = approvals
        self.desktop_lock = desktop_lock
        self._pending = {}
        self._lock = threading.Lock()

    def _snapshot(self, ids, *, preparing=False):
        # A blank composer commonly disables Send. It can still be identified
        # before drafting; authorization and invocation require an enabled button.
        controls = [uia.describe_registered(i, allow_disabled=bool(preparing and n == 2))
                    for n, i in enumerate(ids)]
        if not all(controls):
            raise ValueError("One of the observed controls expired.")
        identity = [(c.get("window_id"), c.get("window_process_id", c.get("process_id"))) for c in controls]
        if not identity[0][0] or not identity[0][1] or len(set(identity)) != 1:
            raise ValueError("Recipient, composer and Send must belong to the same actual window.")
        recipient = uia.get_value(ids[0])
        draft = uia.get_value(ids[1])
        if not recipient.get("ok") or not draft.get("ok"):
            raise ValueError("Cannot read the recipient and draft reliably.")
        # Conversation identity is bound separately from the message text so a
        # later send can prove it is still the SAME conversation (B05-style
        # binding for messages). A stale HWND/PID/header invalidates the tx.
        conversation = {
            "window": controls[0].get("window"),
            "window_id": controls[0].get("window_id"),
            "process_id": controls[0].get("window_process_id", controls[0].get("process_id")),
            "header": recipient.get("value"),
        }
        return {"controls": controls, "recipient": recipient.get("value"),
                "message": draft.get("value"), "conversation": conversation}

    @staticmethod
    def _owner(ctx):
        return (ctx.person_id, ctx.device_id, ctx.session_id, ctx.agent_id)

    def prepare(self, ctx, params):
        keys = ("recipient_element_id", "composer_element_id", "send_element_id")
        ids = [params.get(k) for k in keys]
        recipient, message = params.get("recipient"), params.get("message")
        if (not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != 3
                or not isinstance(recipient, str) or not recipient.strip()
                or not isinstance(message, str) or not message or len(message) > 4000):
            return failure("invalid_arguments", "Choose three distinct observed controls and exact recipient/message.")
        # A keyboard action is NOT message content. This is the observed defect
        # where "Exact message: {Enter}" was confirmed: the model put an input
        # action in the payload slot. Enter/Send is a separate, later action.
        if looks_like_action_token(message):
            return failure(
                "action_token_not_message",
                f"{message!r} is a keyboard action, not message text. Send the exact "
                "wording you want delivered; the Enter/Send key is issued separately "
                "by desktop_send_message.")
        try:
            before = self._snapshot(ids, preparing=True)
            if before["recipient"] != recipient:
                return failure("recipient_mismatch", "The current conversation is not the requested recipient.")
            if before["message"] not in ("", message):
                return failure("existing_draft", "The composer contains a different draft; it was not overwritten.")
            if before["message"] != message:
                written = uia.set_value(ids[1], message)
                if not written.get("ok"):
                    return written
            snapshot = self._snapshot(ids)
            if snapshot["recipient"] != recipient or snapshot["message"] != message:
                return failure("draft_unverified", "Recipient or exact draft could not be verified.")
        except ValueError as exc:
            return failure("message_target_unavailable", str(exc))
        now = time.monotonic()
        token = uuid.uuid4().hex
        # The send control is recorded as a SEPARATE action bound to this frozen
        # transaction, never as part of the payload.
        send_action = {"element_id": ids[2], "kind": "invoke",
                       "label": snapshot["controls"][2].get("name")}
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if now - v["created"] < 300}
            if len(self._pending) >= 16:
                self._pending.pop(next(iter(self._pending)))
            self._pending[token] = {"ids": ids, "snapshot": snapshot, "send_action": send_action,
                                    "owner": self._owner(ctx), "created": now}
        return {"ok": True, "verified": True, "status": "drafted", "transaction_id": token,
                "recipient": recipient, "message": message,
                "conversation": snapshot["conversation"],
                "send_action": {"kind": "invoke", "label": send_action["label"]},
                "detail": "Exact draft verified. Nothing sent; request owner confirmation with desktop_send_message."}

    def send(self, ctx, params, cancel):
        token = params.get("transaction_id")
        if not isinstance(token, str):
            return failure("invalid_arguments", "A prepared transaction is required.")
        with self._lock:
            tx = self._pending.get(token)
            if not tx or tx["owner"] != self._owner(ctx):
                return failure("stale_transaction", "Prepare the message in this owner session first.")
            self._pending.pop(token)
        if time.monotonic() - tx["created"] > 300:
            return failure("stale_transaction", "The draft confirmation expired. Observe and prepare again.")
        snapshot, ids = tx["snapshot"], tx["ids"]
        invoked = False
        lock_taken = False
        holder = ctx.mission_id or ctx.trace_id
        try:
            if self._snapshot(ids) != snapshot:
                return failure("message_changed", "Conversation or draft changed. Nothing sent.")
            if cancel.is_set():
                return failure("cancelled", "Cancelled before confirmation.")
            if self.approvals is None:
                return failure("owner_confirmation_required", "Owner confirmation UI unavailable.")
            action = tx.get("send_action") or {}
            summary = (f"Send message\nApplication: {snapshot['controls'][0]['window']}\n"
                       f"Recipient: {snapshot['recipient']}\n\n"
                       f"Exact message ({len(snapshot['message'])} characters):\n"
                       f"{snapshot['message']}\n\n"
                       f"Then press: {action.get('label') or 'Send'} "
                       f"(a separate key/button action, not part of the message)")
            from .owner_policy import OwnerPolicy
            approved = self.approvals.request(ctx, summary, cancel,
                scope=OwnerPolicy.message_scope(snapshot['controls'][0]['window'],
                                               snapshot['recipient'], snapshot['message']))
            if not approved.get("ok"):
                return approved
            if cancel.is_set():
                return failure("cancelled", "Cancelled before sending.")
            if self.desktop_lock is not None:
                acquired = self.desktop_lock.acquire(holder)
                if not acquired.get("ok"):
                    return failure("desktop_busy", acquired.get("error", "Desktop lease unavailable."))
                lock_taken = True
            if self._snapshot(ids) != snapshot:
                return failure("message_changed", "Conversation or exact draft changed during confirmation. Nothing sent.")
            before = self._matching_messages(snapshot)
            if cancel.is_set() or (self.desktop_lock is not None and self.desktop_lock.check_takeover()):
                return failure("cancelled", "Owner activity or cancellation stopped sending.")
            invoked = True
            outcome = uia.invoke_once(ids[2])
            if not outcome.get("ok"):
                return outcome
            # A visible new copy plus an empty composer is evidence of submission,
            # not a server acknowledgement, delivery or a read receipt.
            for _ in range(8):
                # Sending normally disables/removes Send. Verify the frozen
                # header and composer without requiring that button to remain enabled.
                controls = [uia.describe_registered(i) for i in ids[:2]]
                if any(not control for control in controls) or any(
                        (control.get("window_id"), control.get("process_id")) !=
                        (original.get("window_id"), original.get("process_id"))
                        for control, original in zip(controls, snapshot["controls"][:2])):
                    break
                recipient = uia.get_value(ids[0])
                draft = uia.get_value(ids[1])
                if not recipient.get("ok") or not draft.get("ok") or recipient.get("value") != snapshot["recipient"]:
                    break
                if draft.get("value") == "" and self._matching_messages(snapshot) > before:
                    return {"ok": True, "verified": True, "status": "submitted",
                            "recipient": snapshot["recipient"], "message": snapshot["message"],
                            "sent": None, "delivered": None, "read": None,
                            "detail": "New message visible in the same conversation; composer cleared. Delivery/read unknown."}
                if cancel.wait(0.25):
                    break
        except (ValueError, RuntimeError):
            # After invoking, never turn an observation error into a resend.
            return failure("action_outcome_unknown" if invoked else "message_target_unavailable",
                           "Could not verify the transaction. Observe before any retry.", action_may_have_run=invoked)
        finally:
            if lock_taken:
                self.desktop_lock.release(holder)
        return failure("action_outcome_unknown", "Send was invoked once; outgoing message not verified. Do not resend.",
                       action_may_have_run=True)

    @staticmethod
    def _matching_messages(snapshot):
        control = snapshot["controls"][0]
        found = uia.find_elements(window_title=control["window"], window_id=control["window_id"],
                                  name=snapshot["message"], limit=80, depth=32)
        return sum(item.name == snapshot["message"] and item.control_type not in ("Edit", "Button")
                   for item in found)
