"""Short-lived owner decisions for the exact conversational UI action being awaited."""
import secrets
import threading
import time
import hashlib
import json

from core.contracts import Persona


class ActionApprovals:
    def __init__(self, policy=None, audit=None):
        self._lock = threading.Lock()
        self._pending = {}
        self._execution_grants = {}
        self.policy = policy
        self.audit = audit

    @staticmethod
    def _execution_identity(ctx, capability, params):
        clean = {k: v for k, v in params.items() if k not in ("_approval_receipt", "confirmed")}
        fingerprint = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return (ctx.interaction_id, ctx.trace_id, capability, fingerprint)

    def grant_execution(self, ctx, capability, params):
        """Bridge-only handoff after an actual owner decision; no model schema."""
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._execution_grants = {k: v for k, v in self._execution_grants.items() if v[1] > now}
            self._execution_grants[token] = (self._execution_identity(ctx, capability, params), now + 15)
        return token

    def consume_execution(self, ctx, capability, params):
        with self._lock:
            grant = self._execution_grants.pop(params.get("_approval_receipt"), None)
        return bool(grant and grant[1] > time.monotonic()
                    and grant[0] == self._execution_identity(ctx, capability, params))

    def pending(self):
        with self._lock:
            return [{"id": key, "summary": item["summary"]}
                    for key, item in self._pending.items()
                    if item["decision"] is None and time.monotonic() < item["deadline"]]

    def resolve(self, request_id, allow):
        if type(allow) is not bool:
            return False
        with self._lock:
            item = self._pending.get(request_id)
            if item is None or item["decision"] is not None or time.monotonic() >= item["deadline"]:
                return False
            item["decision"] = allow
            item["event"].set()
            return True

    def request(self, ctx, summary, cancel_event, timeout_s=90, *, scope=None):
        if ctx.person_id != "owner" or ctx.persona != Persona.OWNER or ctx.agent_id:
            return {"ok": False, "error_code": "owner_required", "error": "An owner conversation is required."}
        if ctx.dry_run or cancel_event.is_set():
            return {"ok": False, "error_code": "not_executed", "error": "No action was approved or executed."}
        decision = self.policy.decide(ctx, scope) if self.policy is not None else {}
        if decision.get("decision") == "DENY_WITH_REASON":
            return {"ok": False, "error_code": "owner_required", "error": decision["reason"]}
        if decision.get("decision") == "ALLOW_SILENT":
            if self.audit is not None:
                self.audit.record(who=ctx.person_id, device=ctx.device_id,
                                  action="owner_policy.grant_used", why=json.dumps(scope, ensure_ascii=False),
                                  result="authorized", trace_id=ctx.trace_id)
            return {"ok": not cancel_event.is_set(), "authorization": decision["reason"]}
        request_id = secrets.token_urlsafe(24)
        item = {"summary": summary, "deadline": time.monotonic() + timeout_s,
                "decision": None, "event": threading.Event()}
        with self._lock:
            if len(self._pending) >= 4:
                return {"ok": False, "error_code": "approval_busy", "error": "Finish the pending action confirmation first."}
            self._pending[request_id] = item
        try:
            while time.monotonic() < item["deadline"] and not cancel_event.is_set():
                if item["event"].wait(0.1):
                    allowed = item["decision"] is True and not cancel_event.is_set()
                    return {"ok": allowed, "error_code": "" if allowed else "owner_declined",
                            "error": "" if allowed else "The owner cancelled this action."}
            return {"ok": False, "error_code": "approval_expired", "error": "Action confirmation expired or was cancelled; nothing was executed."}
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
