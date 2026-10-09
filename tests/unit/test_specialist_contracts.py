"""P5: repo-specific specialist contracts (n8n, ComfyUI, HeyGem, OmniVoice,
Kronos, MiroFish, Decepticon).

The gap was that a generic SpecialistAdapter alone is not capability: it
forwarded anything to a fixed path. Each engine now declares its real
operations, endpoints and required parameters, so a request can be validated
before it reaches the wire and health can be checked against the engine's own
response shape.

Deterministic — no engine needs to be running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.specialists import (  # noqa: E402
    ADAPTERS, ComfyUIAdapter, DecepticonAdapter, HeyGemAdapter, KronosAdapter,
    MiroFishAdapter, N8nAdapter, OmniVoiceAdapter, OperationSpec,
    SpecialistAdapter, get_adapter,
)


# ------------------------------------------------------- contracts declared
@pytest.mark.parametrize("adapter,expected_ops", [
    (N8nAdapter, {"run_workflow", "list_workflows", "list_executions"}),
    (ComfyUIAdapter, {"queue_prompt", "history", "object_info"}),
    (HeyGemAdapter, {"submit_avatar_video"}),
    (KronosAdapter, {"predict"}),
    (MiroFishAdapter, {"submit_scenario", "job_status", "job_report"}),
    (OmniVoiceAdapter, {"synthesize"}),
    (DecepticonAdapter, {"assess"}),
])
def test_each_engine_declares_its_operations(adapter, expected_ops):
    inst = adapter()
    assert set(inst.supported_operations()) == expected_ops, \
        f"{adapter.__name__} must declare its real operations"


def test_every_registered_adapter_has_operations():
    for a in ADAPTERS:
        assert a.supported_operations(), \
            f"{a.name} declares no operations — a generic proxy is not capability"


def test_operation_specs_carry_paths_and_methods():
    for a in ADAPTERS:
        for name, spec in a.operations.items():
            assert isinstance(spec, OperationSpec)
            assert spec.path.startswith("/"), f"{a.name}.{name} has no endpoint path"
            assert spec.method in ("GET", "POST")


# ------------------------------------------------------------- validation
def test_missing_required_parameter_is_refused():
    a = N8nAdapter()
    out = a.validate_operation("run_workflow", {})
    assert out.ok is False
    assert out.missing == ["workflow_id"]


def test_required_parameter_present_passes():
    a = N8nAdapter()
    assert a.validate_operation("run_workflow", {"workflow_id": "42"}).ok is True


def test_unknown_operation_is_refused_not_forwarded():
    a = N8nAdapter()
    out = a.validate_operation("delete_everything", {})
    assert out.ok is False
    assert "does not support" in out.error


def test_unknown_parameter_is_reported_but_not_fatal():
    a = ComfyUIAdapter()
    out = a.validate_operation("queue_prompt", {"prompt": {}, "bogus": 1})
    assert out.ok is True
    assert "bogus" in out.error, "unexpected params should be surfaced"


def test_kronos_requires_symbol_and_horizon():
    a = KronosAdapter()
    assert a.validate_operation("predict", {"symbol": "AAPL"}).missing == ["horizon"]
    assert a.validate_operation("predict",
                                {"symbol": "AAPL", "horizon": 5}).ok is True


@pytest.mark.parametrize("falsy", [0, False])
def test_legitimately_falsy_values_are_not_reported_missing(falsy):
    """A horizon of 0 or a flag of False is a real value, not a missing one."""
    a = KronosAdapter()
    assert a.validate_operation("predict",
                                {"symbol": "AAPL", "horizon": falsy}).ok is True


def test_blank_string_still_counts_as_missing():
    a = N8nAdapter()
    assert a.validate_operation("run_workflow", {"workflow_id": "   "}).ok is False


def test_heygem_requires_audio_and_avatar():
    a = HeyGemAdapter()
    out = a.validate_operation("submit_avatar_video", {"audio_path": "/a.wav"})
    assert out.missing == ["avatar_id"]


# ------------------------------------------------------------------ gating
def test_decepticon_requires_authorisation_even_when_params_are_valid():
    a = DecepticonAdapter()
    out = a.validate_operation("assess", {"scope": "lab"})
    assert out.ok is False
    assert "authorisation" in out.error


def test_decepticon_passes_when_authorised():
    a = DecepticonAdapter()
    assert a.validate_operation("assess", {"authorised": True, "scope": "lab"}).ok is True


def test_decepticon_invoke_refuses_without_authorisation():
    a = DecepticonAdapter()
    out = a.invoke("pentest the lab", {})
    assert out["ok"] is False
    assert out.get("status") == "gated"


# ------------------------------------------------------------- health shape
def test_comfyui_shape_check_requires_system_block():
    a = ComfyUIAdapter()
    assert a.health_shape_ok({"system": {"comfyui_version": "1.0"}}) is True
    assert a.health_shape_ok({"unexpected": True}) is False


def test_n8n_shape_check_requires_ok_status():
    a = N8nAdapter()
    assert a.health_shape_ok({"status": "ok"}) is True
    assert a.health_shape_ok({"status": "degraded"}) is False


def test_unknown_shape_defaults_to_true_not_a_claim_of_mismatch():
    """An adapter without a known shape must not invent a mismatch."""
    class Generic(SpecialistAdapter):
        name = "generic"
        capability = "x"
    assert Generic().health_shape_ok({"anything": 1}) is True


# ------------------------------------------------------------- conformance
def test_conformance_reports_pending_when_absent():
    a = N8nAdapter(base_url="http://127.0.0.1:1")   # nothing listening
    rep = a.conformance()
    assert rep["available"] is False
    assert rep["state"] == "pending-live-acceptance"
    assert rep["operations"], "operations must be reported even when absent"


def test_conformance_reports_operations_for_all_adapters():
    for a in ADAPTERS:
        rep = a.conformance()
        assert "operations" in rep
        assert rep["source_repo"] == a.source_repo


def test_conformance_never_claims_success_when_unreachable():
    for a in ADAPTERS:
        rep = a.conformance()
        if not rep["available"]:
            assert rep["state"] != "ready", \
                f"{a.name} claimed ready while unreachable"


# ------------------------------------------------------------------ registry
def test_registry_lookup_by_capability():
    assert get_adapter("workflow.automation") is not None
    assert get_adapter("media.image_video") is not None
    assert get_adapter("does.not.exist") is None


def test_all_seven_specialists_are_registered():
    repos = {a.source_repo for a in ADAPTERS}
    assert repos == {"n8n", "ComfyUI", "HeyGem.ai", "Kronos", "MiroFish",
                     "OmniVoice", "Decepticon"}
