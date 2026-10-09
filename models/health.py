"""Provider health + circuit breaker (models/health).

State is persisted (provider_state table) so a bad provider stays quarantined across
restarts instead of being retried forever.
"""
from __future__ import annotations

from typing import Dict, List

from core.contracts import EventType, now_ms
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("models.health")

FAILURE_THRESHOLD = 3          # consecutive failures before opening the breaker
COOLDOWN_MS = 60_000           # how long a breaker stays open


class HealthMonitor:
    def __init__(self, db, failure_threshold: int = FAILURE_THRESHOLD, cooldown_ms: int = COOLDOWN_MS):
        self.db = db
        self.threshold = failure_threshold
        self.cooldown = cooldown_ms
        self._bus = get_bus()

    def _row(self, provider_id: str, model_id: str):
        return self.db.query_one(
            "SELECT * FROM provider_state WHERE provider_id=? AND model_id=?",
            (provider_id, model_id))

    def record_success(self, provider_id: str, model_id: str, latency_ms: int = 0) -> None:
        self.db.execute(
            "INSERT INTO provider_state(provider_id, model_id, state, error_count, last_error,"
            " last_ok, last_fail, total_calls, total_cost) VALUES(?,?, 'healthy',0,'',?,0,1,0)"
            " ON CONFLICT(provider_id, model_id) DO UPDATE SET state='healthy',"
            " error_count=0, last_error='', last_ok=excluded.last_ok, total_calls=total_calls+1",
            (provider_id, model_id, now_ms()))

    def record_failure(self, provider_id: str, model_id: str, error: str) -> None:
        row = self._row(provider_id, model_id)
        count = (row["error_count"] if row else 0) + 1
        state = "healthy"
        if count >= self.threshold:
            state = "open"
            self._bus.publish(EventType.MODEL_UNAVAILABLE,
                              {"provider_id": provider_id, "model_id": model_id, "error": error})
        self.db.execute(
            "INSERT INTO provider_state(provider_id, model_id, state, error_count, last_error,"
            " last_ok, last_fail, total_calls, total_cost) VALUES(?,?,?,?,?,0,?,1,0)"
            " ON CONFLICT(provider_id, model_id) DO UPDATE SET state=excluded.state,"
            " error_count=excluded.error_count, last_error=excluded.last_error,"
            " last_fail=excluded.last_fail, total_calls=total_calls+1",
            (provider_id, model_id, state, count, error[:400], now_ms()))
        log.warning("provider failure %s/%s: %s (count=%s)", provider_id, model_id, error, count)

    def available(self, provider_id: str, model_id: str) -> bool:
        row = self._row(provider_id, model_id)
        if not row:
            return True
        if row["state"] == "open":
            if (row["last_fail"] or 0) + self.cooldown < now_ms():
                self.db.execute(
                    "UPDATE provider_state SET state='half_open' WHERE provider_id=? AND model_id=?",
                    (provider_id, model_id))
                return True
            return False
        return True

    def state(self, provider_id: str, model_id: str) -> Dict[str, object]:
        row = self._row(provider_id, model_id)
        return dict(row) if row else {"provider_id": provider_id, "model_id": model_id,
                                      "state": "unknown", "error_count": 0}

    def snapshot(self) -> List[Dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM provider_state")]
