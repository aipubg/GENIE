"""Vault, trust (PTE) and audit chain."""
from __future__ import annotations

from core.contracts import CallContext, GrantType, Persona


def test_vault_roundtrip_and_no_plaintext_in_file(app, tmp_path):
    app.vault.store("secret://provider/deepseek/key", "sk-test-123")
    assert app.vault.resolve("secret://provider/deepseek/key") == "sk-test-123"
    raw = (tmp_path / "vault.enc").read_bytes()
    assert b"sk-test-123" not in raw          # never stored in the clear
    assert app.vault.delete("secret://provider/deepseek/key")
    assert app.vault.resolve("secret://provider/deepseek/key") is None


def test_default_deny(app):
    ctx = app.ctx(person_id="stranger", persona=Persona.GUEST)
    decision = app.trust.check(ctx, "computer:app:open", {"target": "chrome"})
    assert decision.allow is False


def test_owner_bootstrap_grant_allows_app_open(app):
    ctx = app.ctx(person_id="owner", persona=Persona.OWNER)
    decision = app.trust.check(ctx, "computer:app:open", {"target": "chrome"})
    assert decision.allow is True


def test_destructive_action_requires_confirmation(app):
    ctx = app.ctx(person_id="owner", persona=Persona.OWNER)
    app.trust.grant("owner", "computer:files:delete", GrantType.STANDING, "owner")
    decision = app.trust.check(ctx, "computer:files:delete", {"path": "C:/x"})
    assert decision.allow is True
    assert decision.needs_confirm is True


def test_destructive_denied_for_non_owner(app):
    ctx = app.ctx(person_id="guest_user", persona=Persona.GUEST)
    app.trust.grant("guest_user", "computer:files:delete", GrantType.STANDING, "owner")
    assert app.trust.check(ctx, "computer:files:delete").allow is False


def test_one_time_grant_is_consumed(app):
    ctx = app.ctx(person_id="owner", persona=Persona.OWNER)
    app.trust.grant("owner", "device:phone_main:media.next", GrantType.ONE_TIME, "owner")
    assert app.trust.check(ctx, "device:phone_main:media.next").allow is True
    assert app.trust.check(ctx, "device:phone_main:media.next").allow is False


def test_audit_chain_verifies_and_detects_tamper(app):
    app.audit.record(who="owner", action="a", result="ok")
    app.audit.record(who="owner", action="b", result="ok")
    assert app.audit.verify() is True
    app.db.execute("UPDATE audit_log SET result='HACKED' WHERE action='a'")
    assert app.audit.verify() is False


def test_policy_blocks_restricted_and_secret(app):
    from core.contracts import DataClass
    from security.policy import PolicyRegistry
    pol = PolicyRegistry()
    assert pol.check("deepseek", DataClass.SECRET) is False
    assert pol.check("deepseek", DataClass.RESTRICTED) is False
    assert pol.check("deepseek", DataClass.INTERNAL) is True
