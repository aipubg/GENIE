"""Provider / model-role settings contract (rc14, section 23).

These tests lock the API/UI contract:
  * the backend is the authority for provider status (a canonical enum),
  * credential presence is a boolean, never a secret value,
  * custom providers can be created / updated / enabled / removed,
  * model roles persist in the backend,
  * test results are recorded without ever storing a secret.

No fixture in this file contains a real secret value.
"""
from __future__ import annotations

import json

from models.registry import ModelRegistry

CANONICAL = {
    "ready", "configured", "needs_api_key", "needs_configuration",
    "unreachable", "disabled", "local", "test_only",
}


# --------------------------------------------------------------------- helpers
def _reg(vault, tmp_path, defaults_file=None):
    kwargs = {"user_file": tmp_path / "providers.user.json"}
    if defaults_file:
        kwargs["defaults_file"] = defaults_file
    return ModelRegistry(vault=vault, **kwargs)


def _by_id(providers, pid):
    return next((p for p in providers if p["id"] == pid), None)


# ------------------------------------------------------------ canonical status
def test_every_provider_returns_a_canonical_status(app):
    for p in app.registry.summary():
        assert p.get("status") in CANONICAL, f"{p['id']} -> {p.get('status')!r}"


def test_mock_provider_is_test_only_and_local(app):
    p = _by_id(app.registry.summary(), "mock")
    assert p is not None, "mock provider missing"
    assert p["status"] == "test_only"
    assert p["local"] is True
    # a test provider must never be reported as needing a credential
    assert p["status"] != "needs_api_key"
    assert p["credential_present"] is False


def test_disabled_provider_is_disabled_not_ready(app):
    app.registry.update_provider("openrouter", {"enabled": False})
    p = _by_id(app.registry.summary(), "openrouter")
    assert p["status"] == "disabled"


def test_credential_absent_is_needs_api_key_not_ready(app):
    """Credentials present + endpoint present must NOT equal Ready (section 12)."""
    p = _by_id(app.registry.summary(), "deepseek")
    assert p["credential_present"] is False
    assert p["status"] == "needs_api_key"


def test_ready_only_after_successful_validation(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "c1", "display_name": "C1",
                      "base_url": "https://example.test/v1", "models": []})
    app.vault.store("secret://provider/c1/key", "test-value-not-a-secret")
    reg.set_vault(app.vault)

    before = _by_id(reg.summary(), "c1")
    assert before["credential_present"] is True
    assert before["status"] == "configured", "credentials alone must not be Ready"

    reg.record_test_result("c1", True)
    after = _by_id(reg.summary(), "c1")
    assert after["status"] == "ready"
    assert after["last_test_status"] == "ok"
    assert after["last_test_at"]


def test_failed_validation_is_unreachable_and_records_time(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "c2", "display_name": "C2",
                      "base_url": "https://example.test/v1", "models": []})
    app.vault.store("secret://provider/c2/key", "test-value-not-a-secret")
    reg.set_vault(app.vault)
    reg.record_test_result("c2", True)
    assert _by_id(reg.summary(), "c2")["status"] == "ready"

    reg.record_test_result("c2", False, "HTTP 401")
    p = _by_id(reg.summary(), "c2")
    assert p["status"] == "unreachable"
    assert p["last_test_status"] == "fail"
    assert p["last_test_at"]


def test_provider_missing_endpoint_is_needs_configuration(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "c3", "display_name": "C3", "models": []})
    app.vault.store("secret://provider/c3/key", "test-value-not-a-secret")
    reg.set_vault(app.vault)
    assert _by_id(reg.summary(), "c3")["status"] == "needs_configuration"


# ------------------------------------------------------------------- secrets
def test_secret_value_is_never_returned(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "c4", "display_name": "C4",
                      "base_url": "https://example.test/v1", "models": []})
    app.vault.store("secret://provider/c4/key", "super-secret-do-not-leak")
    reg.set_vault(app.vault)

    blob = json.dumps(reg.summary())
    assert "super-secret-do-not-leak" not in blob
    p = _by_id(reg.summary(), "c4")
    assert p["credential_present"] is True
    # only a boolean/state, never the value
    assert not any("super-secret" in str(v) for v in p.values())


def test_nedle2_has_no_credential_requirement(app):
    """NEDLE2 is the local Director, not an LLM provider (sections 15/17)."""
    ids = {p["id"] for p in app.registry.summary()}
    assert not any("needle" in i.lower() or i.lower() == "nedle2" for i in ids), \
        "NEDLE2 must not appear as a selectable provider"


# --------------------------------------------------------- custom provider CRUD
def test_custom_provider_create_update_disable_remove(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "mine", "display_name": "My Provider",
                      "protocol": "openai_chat", "custom": True,
                      "base_url": "https://example.test/v1", "models": []})
    assert _by_id(reg.summary(), "mine") is not None

    reg.update_provider("mine", {"display_name": "Renamed"})
    assert reg.provider("mine")["display_name"] == "Renamed"

    reg.update_provider("mine", {"enabled": False})
    assert _by_id(reg.summary(), "mine")["status"] == "disabled"

    reg.update_provider("mine", {"enabled": True})
    assert _by_id(reg.summary(), "mine")["status"] != "disabled"

    assert reg.remove_provider("mine") is True
    assert reg.provider("mine") is None


def test_base_url_persists(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "urlp", "display_name": "URL", "custom": True,
                      "base_url": "https://example.test/v1", "models": []})
    assert reg.provider("urlp")["base_url"] == "https://example.test/v1"
    reg.update_provider("urlp", {"base_url": "https://other.test/v1"})
    assert reg.provider("urlp")["base_url"] == "https://other.test/v1"


def test_duplicate_provider_id_rejected(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "dup", "display_name": "A", "models": []})
    try:
        reg.add_provider({"id": "dup", "display_name": "B", "models": []})
    except ValueError:
        return
    raise AssertionError("duplicate provider id must be rejected")


# ---------------------------------------------------------------------- models
def test_add_and_remove_model(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.add_provider({"id": "mp", "display_name": "MP", "custom": True,
                      "base_url": "https://example.test/v1", "models": []})
    reg.add_model("mp", {"model_id": "m1", "display_name": "M1"})
    assert [m["model_id"] for m in reg.provider("mp")["models"]] == ["m1"]

    assert reg.remove_model("mp", "m1") is True
    assert reg.provider("mp")["models"] == []


# ----------------------------------------------------------------- model roles
def test_roles_default_empty_and_settable(app):
    roles = app.registry.roles()
    assert set(roles) == {"everyday", "fast", "deep_work"}
    app.registry.set_role("everyday", "deepseek", "deepseek-chat")
    app.registry.set_role("fast", "gemini", "gemini-2.5-flash")
    app.registry.set_role("deep_work", "deepseek", "deepseek-reasoner")

    roles = app.registry.roles()
    assert roles["everyday"] == {"provider_id": "deepseek", "model_id": "deepseek-chat"}
    assert roles["fast"] == {"provider_id": "gemini", "model_id": "gemini-2.5-flash"}
    assert roles["deep_work"] == {"provider_id": "deepseek", "model_id": "deepseek-reasoner"}


def test_role_rejects_unknown_role_and_unregistered_model(app):
    for bad in ("bogus", "ultra", ""):
        try:
            app.registry.set_role(bad, "deepseek", "deepseek-chat")
        except ValueError:
            continue
        raise AssertionError(f"unknown role {bad!r} must be rejected")
    try:
        app.registry.set_role("everyday", "deepseek", "not-a-real-model")
    except ValueError:
        return
    raise AssertionError("unregistered model must be rejected")


def test_roles_survive_restart(app, tmp_path):
    """Section 19: backend is the authority, not localStorage."""
    reg = _reg(app.vault, tmp_path)
    reg.set_role("everyday", "deepseek", "deepseek-chat")
    reg.set_role("fast", "gemini", "gemini-2.5-flash")

    reloaded = _reg(app.vault, tmp_path)
    assert reloaded.roles()["everyday"] == {
        "provider_id": "deepseek", "model_id": "deepseek-chat"}
    assert reloaded.roles()["fast"] == {
        "provider_id": "gemini", "model_id": "gemini-2.5-flash"}


def test_clearing_a_role_persists(app, tmp_path):
    reg = _reg(app.vault, tmp_path)
    reg.set_role("everyday", "deepseek", "deepseek-chat")
    reg.set_role("everyday", "", "")
    assert reg.roles()["everyday"]["provider_id"] == ""
    assert _reg(app.vault, tmp_path).roles()["everyday"]["provider_id"] == ""
