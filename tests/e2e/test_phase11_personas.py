"""Phase 11B — role templates: agency-agents corpus → Agent Factory.

The corpus is **data**, not 264 permanent agents. These tests prove the factory resolves a
need to one persona and builds exactly one task-specific agent, and that a missing corpus
degrades gracefully instead of failing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents import personas as pmod
from agents.factory import AgentFactory
from agents.personas import PersonaLibrary, RoleTemplate, get_personas


@pytest.fixture()
def lib() -> PersonaLibrary:
    saved = pmod._LIBRARY
    pmod._LIBRARY = None
    library = get_personas()
    yield library
    pmod._LIBRARY = saved


def test_corpus_is_vendored_and_loads(lib):
    stats = lib.statistics()
    assert stats["templates"] > 100, "persona corpus should be vendored into data/personas"
    assert stats["divisions"] >= 10
    assert Path(stats["root"]).is_dir()


def test_personas_are_templates_not_live_agents(lib):
    """Loading the corpus must not instantiate anything."""
    assert all(isinstance(t, RoleTemplate) for t in lib.all())
    # a template describes a role; it has no lifecycle/agent id
    tpl = lib.all()[0]
    data = tpl.to_dict()
    assert "template_id" in data and "division" in data
    assert not hasattr(tpl, "agent_id")


def test_search_is_name_weighted_not_description_biased(lib):
    """A verbose persona must not win on an unrelated request (regression guard)."""
    api = lib.best("build a REST API for orders")
    assert api is not None
    assert api.division == "engineering"
    assert "api" in api.name.lower() or "api" in api.template_id.lower()
    pentest = lib.best("penetration testing")
    assert pentest is not None and pentest.division == "security"


def test_template_becomes_an_agent_spec(lib):
    tpl = lib.best("frontend react UI")
    spec = tpl.to_agent_spec("build the settings page")
    assert spec["role"] == tpl.template_id
    assert spec["purpose"] == "build the settings page"
    assert spec["capability"]  # a real GENIE capability, used for model routing
    assert spec["division"] == tpl.division


def test_division_lookup_and_get(lib):
    assert lib.by_division("security"), "security division should have personas"
    tpl = lib.get("engineering-backend-architect")
    if tpl is not None:  # corpus contents may evolve
        assert tpl.name and tpl.division == "engineering"


def test_factory_creates_one_task_specific_agent_from_a_persona(lib):
    factory = AgentFactory()
    result = factory.create_from_persona("build a REST API for orders",
                                         purpose="build the orders API")
    assert result.definition is not None
    # exactly one agent, created for this need — not 264 permanent agents
    created = [a for a in factory._registry.values()
               if (getattr(a, "purpose", "") or "") == "build the orders API"]
    assert len(created) == 1
    persona = getattr(result.definition, "persona", None)
    assert persona and persona["division"] == "engineering"


def test_factory_falls_back_gracefully_when_corpus_is_absent():
    """A missing corpus must degrade to the built-in worker, not crash."""
    saved = pmod._LIBRARY
    pmod._LIBRARY = PersonaLibrary(Path("/definitely/not/a/corpus"))
    try:
        factory = AgentFactory()
        result = factory.create_from_persona("anything at all")
        assert result.definition is not None
        assert result.definition.role == "worker"
    finally:
        pmod._LIBRARY = saved


def test_empty_library_is_handled():
    empty = PersonaLibrary(Path("/definitely/not/a/corpus"))
    assert empty.load() == []
    assert empty.search("anything") == []
    assert empty.statistics()["templates"] == 0
