from core.lifecycle import Daemon


class _Health:
    def snapshot(self):
        return [
            {"provider_id": "gemini", "model_id": "gemini-old", "last_ok": 100},
            {"provider_id": "gemini", "model_id": "gemini-current", "last_ok": 250},
        ]


class _Gateway:
    def last_selection(self):
        return {"selected_provider": "gemini", "selected_model": "gemini-live",
                "capability": "general", "ts_ms": 300}


def _daemon(services):
    daemon = object.__new__(Daemon)
    daemon.services = services
    return daemon


def test_source_of_truth_prefers_latest_gateway_selection():
    status = _daemon({"health": _Health(), "gateway": _Gateway()})._source_of_truth()
    assert status["active_provider"] == "gemini"
    assert status["active_model"] == "gemini-live"
    assert status["active_capability"] == "general"


def test_source_of_truth_fallback_uses_latest_success_timestamp():
    status = _daemon({"health": _Health()})._source_of_truth()
    assert status["active_model"] == "gemini-current"
