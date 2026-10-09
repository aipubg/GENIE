"""Phase 13 — User Control Center.

The roadmap exit gate: *owner can see "what do you remember / which agents run / which provider
sees my data" in 3 clicks.* These tests prove the three answers are real, complete, and honestly
reported — including the important negative case: when a service is missing the section says so
instead of showing an empty list that would read as "GENIE remembers nothing".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.service import AgentService
from core.contracts import CallContext
from core.controlcenter import ControlCenter
from core.db import reset_db_for_tests
from memory.service import MemoryService
from models.registry import ModelRegistry
from security.audit import AuditLog
from security.trust import TrustService


@pytest.fixture()
def cc(app, tmp_path):
    trust = TrustService(app.db, audit=app.audit, default_deny=True)
    memory = MemoryService(app.db, audit=app.audit, trust=trust)
    registry = ModelRegistry(user_file=tmp_path / "providers.user.json")
    agents = AgentService(db=app.db, audit=app.audit, artifact_root=str(tmp_path / "art"))
    return ControlCenter(memory=memory, registry=registry, agents=agents, audit=app.audit)


# ------------------------------------------------------- 1 what do you remember
def test_memory_view_lists_what_genie_remembers(cc):
    ctx = CallContext()
    cc.memory.write(ctx, type="semantic", entity="owner",
                    value="Owner prefers dark mode", confidence=0.9)
    cc.memory.write(ctx, type="episodic", entity="owner",
                    value="Asked GENIE to open Chrome", confidence=0.7)

    view = cc.memory_view(limit=25)
    assert view["available"] is True
    assert view["total"] >= 2
    assert "semantic" in view["by_type"] and "episodic" in view["by_type"]
    # the owner can actually read what is remembered
    values = " ".join(str(r.get("value", "")) for r in view["records"])
    assert "dark mode" in values


def test_memory_view_without_a_service_says_so_rather_than_looking_empty():
    view = ControlCenter(memory=None).memory_view()
    assert view["available"] is False
    assert view["reason"]
    assert view["records"] == []


def test_owner_can_forget_a_memory(cc):
    ctx = CallContext()
    cc.memory.write(ctx, type="semantic", entity="owner",
                    value="Owner's temporary preference", confidence=0.6)
    before = cc.memory_view(limit=25)
    record_id = before["records"][0]["record_id"]

    out = cc.forget(record_id)
    assert out["ok"] is True
    after = cc.memory_view(limit=25)
    assert after["total"] < before["total"]


def test_forget_is_audited(cc, app):
    ctx = CallContext()
    cc.memory.write(ctx, type="semantic", entity="owner", value="Audited memory", confidence=0.8)
    record_id = cc.memory_view(limit=25)["records"][0]["record_id"]
    cc.forget(record_id)
    actions = [a["action"] for a in app.audit.tail(limit=20)]
    assert "control_center.forget" in actions


# ------------------------------------------------------------ 2 which agents run
def test_agents_view_reports_teams_and_costs(cc):
    view = cc.agents_view()
    assert view["available"] is True
    assert "teams" in view and "live_agents" in view
    assert view["running_tasks"] >= 0
    assert "experience" in view


def test_agents_view_without_a_service_says_so():
    view = ControlCenter(agents=None).agents_view()
    assert view["available"] is False and view["reason"]


# -------------------------------------------------- 3 which provider sees my data
def test_providers_view_separates_local_from_remote(cc):
    view = cc.providers_view()
    assert view["available"] is True
    assert view["providers"], "the owner must be able to see the configured providers"
    for p in view["providers"]:
        assert p["locality"] in ("local", "remote")
        # the privacy rule: only enabled AND non-local providers receive data
        assert p["sees_my_data"] == (p["locality"] == "remote" and p["enabled"])
    assert view["remote"] + view["local"] == len(view["providers"])


def test_providers_view_normalises_modelspec_objects(cc):
    """`registry.models()` returns dataclasses, not dicts — count must still work."""
    view = cc.providers_view()
    assert view["available"] is True
    for p in view["providers"]:
        assert isinstance(p["model_count"], int)
        for m in p["models"]:
            assert "model_id" in m and "enabled" in m


def test_local_providers_are_recognised_as_local():
    assert ControlCenter._is_local({"id": "ollama", "name": "Ollama"}) is True
    assert ControlCenter._is_local({"id": "local-vosk", "name": "Vosk"}) is True
    assert ControlCenter._is_local({"id": "openai", "base_url": "https://api.openai.com"}) is False


def test_providers_view_without_a_registry_says_so():
    view = ControlCenter(registry=None).providers_view()
    assert view["available"] is False and view["reason"]


# --------------------------------------------------------------------- overview
def test_overview_answers_all_three_questions_at_once(cc):
    ctx = CallContext()
    cc.memory.write(ctx, type="semantic", entity="owner", value="Owner is building GENIE",
                    confidence=0.9)
    overview = cc.overview()
    assert {"memory", "agents", "providers"} <= set(overview)
    assert overview["memory"]["available"] is True
    assert overview["agents"]["available"] is True
    assert overview["providers"]["available"] is True
