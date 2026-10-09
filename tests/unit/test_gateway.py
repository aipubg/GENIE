"""Provider gateway: selection, credential gating, failover, health, cost, custom providers."""
from __future__ import annotations

import pytest

from core.contracts import DataClass, ModelRequirement


def test_registry_has_predefined_providers(app):
    ids = {p["id"] for p in app.registry.providers()}
    for expected in ("deepseek", "gemini", "openrouter", "together", "glm", "kimi",
                     "minimax", "tencent_cloud"):
        assert expected in ids


def test_add_custom_openai_compatible_provider_at_runtime(app):
    prov = app.registry.add_provider({
        "id": "my_gateway", "display_name": "My Gateway", "protocol": "openai_chat",
        "base_url": "https://provider.example/v1", "priority": 5,
    })
    app.registry.add_model("my_gateway", {
        "model_id": "my-model", "display_name": "My Model",
        "capabilities": ["general", "tools"], "context_window": 32000})
    spec = app.registry.model("my_gateway", "my-model")
    assert spec is not None and spec.protocol == "openai_chat"
    assert app.registry.base_url("my_gateway") == "https://provider.example/v1"
    # persisted so it survives restart without touching source
    reloaded = type(app.registry)(user_file=app.registry.user_file)
    assert reloaded.provider("my_gateway") is not None


def test_remove_model_without_code_change(app):
    app.registry.add_provider({"id": "tmp_p", "protocol": "openai_chat", "base_url": "https://x/v1"})
    app.registry.add_model("tmp_p", {"model_id": "m1", "capabilities": ["general"]})
    assert app.registry.remove_model("tmp_p", "m1") is True
    assert app.registry.model("tmp_p", "m1") is None


def test_provider_without_key_is_not_selected(app):
    req = ModelRequirement(capability="general", data_class=DataClass.INTERNAL)
    cands = app.gateway.candidates(req)
    assert all(c.protocol == "mock" for c in cands)


def test_degraded_mock_completion_when_no_keys(app):
    ctx = app.ctx()
    req = ModelRequirement(capability="reasoning", data_class=DataClass.INTERNAL)
    out = app.gateway.complete(ctx, req, [{"role": "user", "content": "hello"}])
    assert "mock" in out.text
    assert out.provider_id == "mock"


def test_production_gateway_reports_missing_real_model_instead_of_mock(app):
    from core.config import DEFAULTS
    from core.contracts import ProviderError
    from models.gateway import Gateway

    assert DEFAULTS["models"]["mock_mode"] is False
    gateway = Gateway(app.registry, app.vault, app.gateway.policy, app.db,
                      health=app.health, eligibility=app.gateway.eligibility,
                      allow_mock_fallback=False)
    req = ModelRequirement(capability="reasoning", data_class=DataClass.INTERNAL)
    assert all(spec.protocol != "mock" for spec in gateway.candidates(req))
    with pytest.raises(ProviderError, match="no real model available"):
        gateway.complete(app.ctx(), req, [{"role": "user", "content": "hello"}])
    with pytest.raises(ProviderError, match="no real model available"):
        list(gateway.stream(app.ctx(), req, [{"role": "user", "content": "hello"}]))


def test_production_gateway_surfaces_real_provider_failure_without_mock(app):
    from core.contracts import ProviderError
    from models.gateway import Gateway

    app.registry.add_provider({"id": "broken", "display_name": "Broken",
        "protocol": "openai_chat", "base_url": "http://127.0.0.1:9/v1", "priority": 1})
    app.registry.add_model("broken", {"model_id": "b1", "capabilities": ["reasoning"],
                                        "priority": 1})
    app.vault.store("secret://provider/broken/key", "sk-fake")
    gateway = Gateway(app.registry, app.vault, app.gateway.policy, app.db,
                      health=app.health, eligibility=app.gateway.eligibility,
                      allow_mock_fallback=False)
    req = ModelRequirement(capability="reasoning", data_class=DataClass.INTERNAL)
    with pytest.raises(ProviderError) as error:
        gateway.complete(app.ctx(), req, [{"role": "user", "content": "hello"}])
    assert "broken/b1" in str(error.value)
    assert "mock" not in str(error.value).casefold()


def test_failover_to_next_provider_and_health_recorded(app):
    ctx = app.ctx()
    app.registry.add_provider({
        "id": "broken", "display_name": "Broken", "protocol": "openai_chat",
        "base_url": "http://127.0.0.1:9/v1", "priority": 1})
    app.registry.add_model("broken", {"model_id": "b1", "capabilities": ["general"], "priority": 1})
    app.vault.store("secret://provider/broken/key", "sk-fake")

    req = ModelRequirement(capability="general", data_class=DataClass.INTERNAL)
    cands = app.gateway.candidates(req)
    assert cands[0].provider_id == "broken"          # preferred, credentials present
    out = app.gateway.complete(ctx, req, [{"role": "user", "content": "hi"}])
    assert out.provider_id == "mock"                 # failed over
    assert app.health.state("broken", "b1")["error_count"] >= 1


def test_circuit_breaker_opens_after_threshold(app):
    for i in range(3):
        app.health.record_failure("p", "m", f"fail {i}")
    assert app.health.available("p", "m") is False
    app.health.record_success("p", "m")
    assert app.health.available("p", "m") is True


def test_test_connection_reports_missing_key(app):
    res = app.gateway.test_connection("deepseek")
    assert res["ok"] is False and "key" in res["error"]


def test_policy_blocks_restricted_data_from_gateway(app):
    req = ModelRequirement(capability="reasoning", data_class=DataClass.SECRET)
    assert app.gateway.candidates(req) == []


def test_explicit_anonymous_endpoint_can_run_and_test_connection(app, monkeypatch):
    from models.providers.openai_compat import OpenAICompatAdapter
    app.registry.add_provider({"id": "local_worker", "protocol": "openai_chat",
        "base_url": "http://127.0.0.1:11434/v1", "kind": "local",
        "secret_ref": "", "priority": 1})
    app.registry.add_model("local_worker", {"model_id": "local-code",
        "capabilities": ["general"], "priority": 1})
    monkeypatch.setattr(OpenAICompatAdapter, "_post", lambda *args: {
        "choices": [{"message": {"content": "local reply"}}]})
    req = ModelRequirement(capability="general", data_class=DataClass.INTERNAL)
    assert "local_worker" in [s.provider_id for s in app.gateway.candidates(req)]
    result = app.gateway.complete(app.ctx(), req, [{"role": "user", "content": "hello"}])
    assert result.provider_id == "local_worker"
    assert app.gateway.test_connection("local_worker")["ok"]
