"""Memory service: dedupe, supersede, forget, privacy, offline FTS."""
from __future__ import annotations

from core.contracts import CallContext, Persona


def test_write_and_retrieve(app):
    ctx = app.ctx()
    rid = app.memory.write(ctx, type="preference", entity="UI theme", value="prefers dark themes")
    hits = app.memory.query(ctx, "dark themes")
    assert any(h.record.record_id == rid for h in hits)


def test_duplicate_write_is_deduped_not_duplicated(app):
    ctx = app.ctx()
    a = app.memory.write(ctx, type="preference", entity="theme", value="dark")
    b = app.memory.write(ctx, type="preference", entity="theme", value="dark")
    assert a == b
    assert app.memory.stats()["live"] == 1


def test_correction_supersedes_instead_of_overwriting(app):
    ctx = app.ctx()
    old = app.memory.write(ctx, type="preference", entity="theme", value="bright themes")
    new = app.memory.write(ctx, type="preference", entity="theme", value="dark themes")
    assert old != new
    old_row = app.memory.get(old)
    assert old_row["superseded_by"] == new
    # superseded records are hidden from queries
    assert all(h.record.record_id != old for h in app.memory.query(ctx, "themes"))
    assert app.memory.stats()["superseded"] == 1


def test_forget_purges_everywhere(app):
    ctx = app.ctx()
    app.memory.write(ctx, type="fact", entity="secret-project", value="alpha")
    n = app.memory.forget(ctx, entity="secret-project")
    assert n == 1
    assert app.memory.query(ctx, "alpha") == []


def test_private_memory_not_readable_by_other_person(app):
    owner = app.ctx(person_id="owner", persona=Persona.OWNER)
    app.memory.write(owner, type="fact", entity="pin", value="1234",
                     privacy_scope="private:owner")
    other = app.ctx(person_id="member1", persona=Persona.MEMBER)
    assert all(h.record.value != "1234" for h in app.memory.query(other, "pin"))


def test_offline_fts_fallback_returns_results(app):
    ctx = app.ctx()
    app.memory.write(ctx, type="skill", entity="blender export", value="export fbx using script")
    hits = app.memory.query(ctx, "blender export")
    assert hits and "blender" in hits[0].record.entity


def test_pinned_record_is_not_superseded(app):
    ctx = app.ctx()
    rid = app.memory.write(ctx, type="preference", entity="editor", value="vscode")
    app.memory.pin(rid)
    new = app.memory.write(ctx, type="preference", entity="editor", value="neovim")
    assert app.memory.get(rid)["superseded_by"] is None
    assert app.memory.get(new)["value"] == "neovim"
