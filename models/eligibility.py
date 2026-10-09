"""Automatic provider eligibility + runtime status (models/eligibility).

Point 1 — replaces the owner-facing manual Enable/Disable toggle with a
runtime health state that GENIE manages itself.

Design rules (from the owner spec):
  * This is RUNTIME ONLY. It never overwrites the owner's saved provider
    configuration (enabled/base_url/key/models). A transient failure is not the
    same thing as permanent disablement.
  * Healthy + credential valid            -> eligible (ready / in_use)
  * Explicit quota exhausted              -> runtime ineligible until a later
                                             success / health check
  * Rate limited                         -> cooldown, then eligible again
  * Auth invalid (401/403)               -> ineligible until credential replaced
  * Network outage / timeout / 5xx       -> temporarily unhealthy (cooldown)
  * A later success / Test success       -> automatically returns to eligible

Owner-facing status vocabulary (what Settings shows as a badge):
  ready | in_use | rate_limited | quota_exhausted | auth_required |
  temporarily_unavailable | connection_failed
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core.contracts import now_ms
from core.logging_setup import get_logger

from . import failures

log = get_logger("models.eligibility")

# Cooldown applied to transient conditions before the provider is eligible again.
RATE_LIMIT_COOLDOWN_MS = 60_000
TRANSIENT_COOLDOWN_MS = 30_000

# States that block routing until something positive happens (a success or a
# new Test/health check). These are NOT time-expired automatically.
_HARD_INELIGIBLE = {"quota_exhausted", "auth_required"}

# States that recover on their own after `until_ms`.
_SOFT_INELIGIBLE = {"rate_limited", "temporarily_unavailable", "connection_failed"}


class EligibilityTracker:
    def __init__(self, db, rate_limit_cooldown_ms: int = RATE_LIMIT_COOLDOWN_MS,
                 transient_cooldown_ms: int = TRANSIENT_COOLDOWN_MS):
        self.db = db
        self.rate_cooldown = rate_limit_cooldown_ms
        self.transient_cooldown = transient_cooldown_ms

    # ------------------------------------------------------------- recording
    def record_outcome(self, provider_id: str, kind: "failures.FailureKind",
                       detail: str = "") -> None:
        """Apply a failure classification to runtime eligibility."""
        if kind == failures.FailureKind.RATE_LIMIT:
            self._set(provider_id, "rate_limited", detail or "rate limited",
                      now_ms() + self.rate_cooldown)
        elif kind == failures.FailureKind.QUOTA_EXHAUSTED:
            self._set(provider_id, "quota_exhausted",
                      detail or "quota/credit exhausted", 0)
        elif kind in (failures.FailureKind.MISSING_CREDENTIAL,
                      failures.FailureKind.PERMISSION_DENIED):
            self._set(provider_id, "auth_required",
                      detail or "authentication required", 0)
        elif kind == failures.FailureKind.UNREACHABLE:
            self._set(provider_id, "connection_failed",
                      detail or "connection failed", now_ms() + self.transient_cooldown)
        elif kind in (failures.FailureKind.TIMEOUT, failures.FailureKind.MODEL_UNAVAILABLE,
                      failures.FailureKind.MALFORMED, failures.FailureKind.UNKNOWN):
            self._set(provider_id, "temporarily_unavailable",
                      detail or "temporarily unavailable", now_ms() + self.transient_cooldown)
        else:
            self._set(provider_id, "temporarily_unavailable",
                      detail or "temporarily unavailable", now_ms() + self.transient_cooldown)

    def record_success(self, provider_id: str, in_use: bool = False) -> None:
        """A successful call (or Test) returns the provider to the eligible pool."""
        status = "in_use" if in_use else "ready"
        self._set(provider_id, status, "", 0)

    def mark_in_use(self, provider_id: str) -> None:
        """Lightweight marker while a request is actively being served.

        Never promotes a hard-ineligible provider back to eligible.
        """
        row = self._row(provider_id)
        if row and row["status"] in _HARD_INELIGIBLE:
            return
        if row and row["status"] in _SOFT_INELIGIBLE and row["until_ms"] > now_ms():
            return
        self._set(provider_id, "in_use", "", 0)

    # --------------------------------------------------------------- querying
    def status(self, provider_id: str) -> Dict[str, object]:
        row = self._row(provider_id)
        if not row:
            return {"provider_id": provider_id, "status": "ready",
                    "detail": "", "eligible": True}
        st = row["status"]
        # A soft-ineligible state that has cooled down recovers implicitly.
        if st in _SOFT_INELIGIBLE and (row["until_ms"] or 0) <= now_ms():
            st = "ready"
        return {"provider_id": provider_id, "status": st,
                "detail": row["detail"] or "", "eligible": self._eligible(st, row)}

    def is_eligible(self, provider_id: str) -> bool:
        return self.status(provider_id)["eligible"]

    def _eligible(self, st: str, row) -> bool:
        if st in _HARD_INELIGIBLE:
            return False
        if st in _SOFT_INELIGIBLE:
            return (row["until_ms"] or 0) <= now_ms()
        return True

    def snapshot(self) -> List[Dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM provider_runtime")]

    # ------------------------------------------------------------------ store
    def _row(self, provider_id: str):
        return self.db.query_one(
            "SELECT * FROM provider_runtime WHERE provider_id=?", (provider_id,))

    def _set(self, provider_id: str, status: str, detail: str, until_ms: int) -> None:
        self.db.execute(
            "INSERT INTO provider_runtime(provider_id, status, detail, since_ms, until_ms)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(provider_id) DO UPDATE SET status=excluded.status,"
            " detail=excluded.detail, since_ms=excluded.since_ms, until_ms=excluded.until_ms",
            (provider_id, status, detail[:400], now_ms(), until_ms))
