"""Computer contract (C7): PTE gate, dry-run, verification flag, audit trail."""
from __future__ import annotations

from core.contracts import Persona


def test_dry_run_open_is_allowed_and_audited(app):
    ctx = app.ctx(dry_run=True)
    res = app.computer.execute(ctx, "application.open", {"target": "chrome"})
    assert res.ok is True
    entries = app.audit.tail(10)
    assert any(e["action"] == "computer.application.open" for e in entries)


def test_unknown_capability_is_rejected(app):
    res = app.computer.execute(app.ctx(dry_run=True), "application.explode", {})
    assert res.ok is False and "unsupported" in res.detail


def test_denied_when_no_grant(app):
    ctx = app.ctx(person_id="member1", persona=Persona.MEMBER, dry_run=True)
    res = app.computer.execute(ctx, "application.open", {"target": "chrome"})
    assert res.ok is False and "denied" in res.detail


def test_shell_requires_scope(app):
    ctx = app.ctx(person_id="member1", persona=Persona.MEMBER, dry_run=True)
    res = app.computer.execute(ctx, "shell.run", {"command": "whoami"})
    assert res.ok is False


def test_capabilities_list_is_stable(app):
    caps = app.computer.capabilities()
    assert "application.open" in caps and "system.volume.set" in caps
