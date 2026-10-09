"""A2A specialist boundary (re-audit 14.5, donor: AgentScope).

The honesty rule: registering an agent card is NOT proof it works. Without a transport, a call must
report pending-live-acceptance rather than a fabricated result.
"""
from __future__ import annotations

from agents.a2a import LIVE, PENDING, A2AProvider, AgentCard, SpecialistAgentRegistry


def _card(**kw):
    defaults = dict(name="pubg-agent", description="plays matches",
                    capabilities=["play_match", "training_mode", "stop"])
    defaults.update(kw)
    return AgentCard(**defaults)


# ----------------------------------------------------------------- registry
def test_a_card_can_be_registered_and_found():
    reg = SpecialistAgentRegistry()
    reg.register(_card())
    assert reg.get("pubg-agent") is not None
    assert len(reg) == 1
    assert reg.get("nope") is None


def test_finding_agents_by_capability():
    reg = SpecialistAgentRegistry()
    reg.register(_card(name="a", capabilities=["play_match"]))
    reg.register(_card(name="b", capabilities=["analyse"]))
    assert [c.name for c in reg.find_by_capability("play_match")] == ["a"]
    assert reg.find_by_capability("nope") == []


def test_unregister_removes_the_card():
    reg = SpecialistAgentRegistry()
    reg.register(_card())
    assert reg.unregister("pubg-agent") is True
    assert len(reg) == 0


# ------------------------------------------------------------------- invoke
def test_calling_an_unknown_agent_is_reported():
    out = A2AProvider().invoke("ghost", "play_match")
    assert out["ok"] is False
    assert out["status"] == "unknown-agent"


def test_calling_an_undeclared_capability_is_refused():
    provider = A2AProvider()
    provider.registry.register(_card())
    out = provider.invoke("pubg-agent", "launch_nukes")
    assert out["ok"] is False
    assert out["status"] == "unsupported-method"
    assert "play_match" in out["error"], "the error should say what IS available"


def test_without_a_transport_the_call_is_pending_not_faked():
    """The critical honesty case."""
    provider = A2AProvider()
    provider.registry.register(_card())
    out = provider.invoke("pubg-agent", "play_match")
    assert out["ok"] is False
    assert out["status"] == PENDING
    assert "not live" in out["error"]


def test_with_a_transport_the_call_succeeds():
    def transport(card, method, params):
        return {"result": f"{method}:{params.get('mode', 'ranked')}"}

    provider = A2AProvider(transport=transport)
    provider.registry.register(_card())
    out = provider.invoke("pubg-agent", "play_match", {"mode": "casual"})
    assert out["ok"] is True
    assert out["status"] == LIVE
    assert out["result"] == "play_match:casual"


def test_transport_errors_are_captured_not_raised():
    def boom(card, method, params):
        raise RuntimeError("connection refused")

    provider = A2AProvider(transport=boom)
    provider.registry.register(_card())
    out = provider.invoke("pubg-agent", "play_match")
    assert out["ok"] is False
    assert out["status"] == "transport-error"
    assert "connection refused" in out["error"]


def test_calls_are_recorded_for_audit():
    provider = A2AProvider(transport=lambda c, m, p: {"ok": True})
    provider.registry.register(_card())
    provider.invoke("pubg-agent", "play_match")
    provider.invoke("pubg-agent", "stop")
    assert len(provider.calls) == 2
    assert {c["method"] for c in provider.calls} == {"play_match", "stop"}


def test_status_distinguishes_registered_from_live():
    provider = A2AProvider()          # no transport
    provider.registry.register(_card())
    status = provider.status()
    assert status["registered"] == 1
    assert status["live_capable"] is False
    assert status["transport"] == "none"
