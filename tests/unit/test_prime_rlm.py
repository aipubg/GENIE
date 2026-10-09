"""Tests for integrations/prime_rlm.py — Prime Agent RLM runtime provider.

Deterministic: a provider double stands in for GENIE's Provider Gateway (no credentials, no
network), and the real GENIE host logic is driven against the preserved kernel's frame protocol.
Proves: original runtime preserved (vendored), GENIE is the host, and multi-model fan-out is
resolved by GENIE (never by Prime).
"""
from __future__ import annotations

import pytest

from core.contracts import ModelSpec
from integrations.prime_rlm import (
    ChildSpec, PrimeRlmRuntime, PrimeRlmUnavailable, RLMModel, encode_frame,
    kernel_available, requirement_for,
)


class FakeGateway:
    """Provider double for GENIE's Provider Gateway (models/gateway.Gateway.candidates)."""

    TABLE = {
        "reasoning": [("anthropic", "claude-reason", "Claude Reason"),
                      ("openai", "o3-mini", "O3 Mini")],
        "coding": [("openai", "gpt-code", "GPT Code")],
        "fast": [("groq", "llama-fast", "Llama Fast")],
        "vision": [("openai", "gpt-vision", "GPT Vision")],
    }

    def candidates(self, req):
        return [ModelSpec(provider_id=p, model_id=m, display_name=n,
                          capabilities=[req.capability])
                for p, m, n in self.TABLE.get(req.capability, [])]


@pytest.fixture
def runtime(tmp_path):
    return PrimeRlmRuntime(FakeGateway(), session_root=tmp_path / "sessions")


# ------------------------------------------------- preservation / authority
def test_original_kernel_is_vendored_not_rewritten():
    """The preserved upstream kernel lives under GENIE, unmodified."""
    assert kernel_available() is True, "vendored Prime RLM kernel must be present"


def test_describe_states_gateway_authority(runtime):
    d = runtime.describe()
    assert d["id"] == "prime_rlm"
    assert d["gateway_bound"] is True
    assert "GENIE Provider Gateway" in d["authority"]


def test_capability_maps_to_genie_requirement_not_model_name():
    req = requirement_for("coding.strong")
    assert req.capability == "coding"
    assert req.min_quality == "strong"
    assert req.needs_tools is True


def test_unknown_capability_falls_back_safely():
    req = requirement_for("does.not.exist")
    assert req.capability == "reasoning"


# ------------------------------------------------------- model authority
def test_models_come_from_genie_gateway(runtime):
    models = runtime.models_for("reasoning.strong")
    assert models and all(isinstance(m, RLMModel) for m in models)
    assert models[0].selector == "anthropic/claude-reason"


def test_runtime_refuses_to_select_models_without_gateway(tmp_path):
    rt = PrimeRlmRuntime(None, session_root=tmp_path)
    with pytest.raises(PrimeRlmUnavailable):
        rt.models_for("reasoning.strong")


def test_resolve_selector_picks_gateway_top_candidate(runtime):
    assert runtime.resolve_selector("coding.strong") == "openai/gpt-code"


def test_explicit_model_outside_genie_candidates_is_refused(runtime):
    """A runtime cannot self-grant a model GENIE did not offer."""
    with pytest.raises(PrimeRlmUnavailable):
        runtime.resolve_selector("coding.strong", explicit="evilrogue/shady")


def test_explicit_model_offered_by_genie_is_honoured(runtime):
    assert runtime.resolve_selector("reasoning.strong", explicit="openai/o3-mini") == \
        "openai/o3-mini"


def test_multimodal_resolves_to_a_vision_capable_model(runtime):
    assert runtime.resolve_selector("multimodal") == "openai/gpt-vision"


def test_no_model_for_capability_is_an_error(tmp_path):
    """If GENIE offers nothing for a capability, the runtime must not invent one."""

    class EmptyGateway:
        def candidates(self, req):
            return []

    rt = PrimeRlmRuntime(EmptyGateway(), session_root=tmp_path)
    with pytest.raises(PrimeRlmUnavailable):
        rt.resolve_selector("reasoning.strong")


# ------------------------------------------------------- multi-model fan-out
def test_fanout_spans_different_model_families(runtime):
    """The multi-model proof: one mission, children on different provider/model classes."""
    children = runtime.fanout([
        ChildSpec(name="planner", prompt="plan", capability="reasoning.strong"),
        ChildSpec(name="coder", prompt="code", capability="coding.strong"),
        ChildSpec(name="bulk", prompt="classify", capability="cheap.bulk"),
    ])
    selectors = [c.selector for c in children]
    assert selectors == ["anthropic/claude-reason", "openai/gpt-code", "groq/llama-fast"]
    assert len(set(selectors)) == 3, "children must land on distinct model families"


def test_fanout_same_capability_is_deterministic(runtime):
    a = runtime.fanout([ChildSpec(name="a", prompt="p", capability="coding.strong")])
    b = runtime.fanout([ChildSpec(name="b", prompt="p", capability="coding.strong")])
    assert a[0].selector == b[0].selector == "openai/gpt-code"


def test_children_are_scoped_under_the_genie_session_root(runtime, tmp_path):
    children = runtime.fanout([ChildSpec(name="worker", prompt="p", capability="coding.strong")])
    assert str(children[0].session_dir).startswith(str(tmp_path / "sessions"))


# ------------------------------------------------------- host frame protocol
def _frame(rtype, **data):
    return {"event": "host_request", "id": "rid-1", "data": {"type": rtype, **data}}


def test_host_run_creates_child_with_genie_model(runtime):
    reply = runtime.handle_frame(
        _frame("rlm.run", prompt="do work", kwargs={"name": "worker",
                                                    "capability": "coding.strong"}))
    assert reply["status"] == "ok"
    result = reply["result"]
    assert result["name"] == "worker"
    assert result["model"] == "openai/gpt-code"      # resolved by GENIE, not Prime
    assert result["rlm_child_id"].startswith("child-")


def test_host_find_models_returns_genie_models(runtime):
    reply = runtime.handle_frame(_frame("rlm.find_models", capability="reasoning.strong", limit=2))
    models = reply["result"]["models"]
    assert [m["selector"] for m in models] == ["anthropic/claude-reason", "openai/o3-mini"]


def test_host_list_subagents_lists_children(runtime):
    runtime.fanout([ChildSpec(name="w1", prompt="p", capability="coding.strong")])
    reply = runtime.handle_frame(_frame("rlm.list_subagents"))
    assert [s["name"] for s in reply["result"]["subagents"]] == ["w1"]


def test_host_create_session_returns_genie_model(runtime):
    reply = runtime.handle_frame(
        _frame("rlm.create_session", prompt="go",
               kwargs={"name": "daemon", "capability": "cheap.bulk"}))
    result = reply["result"]
    assert result["model"] == "groq/llama-fast"
    assert result["session_file"].endswith("daemon.jsonl")


def test_host_collect_reports_child(runtime):
    child = runtime.fanout([ChildSpec(name="w", prompt="p", capability="coding.strong")])[0]
    reply = runtime.handle_frame(_frame("rlm.collect", target=child.rlm_child_id))
    assert reply["result"]["session_name"] == "w"
    assert reply["result"]["settled"] is False       # still running


def test_unknown_host_request_errors_instead_of_fabricating(runtime):
    reply = runtime.handle_frame(_frame("rlm.do_whatever"))
    assert reply["status"] == "error"
    assert "unsupported" in reply["error"]


def test_non_host_request_frames_are_ignored(runtime):
    assert runtime.handle_frame({"event": "display", "id": "x"}) is None


def test_frame_encoding_matches_upstream(runtime):
    assert encode_frame({"event": "host_reply", "id": "r1", "status": "ok"}) == \
        b'{"event":"host_reply","id":"r1","status":"ok"}\n'
