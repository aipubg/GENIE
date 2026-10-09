import copy
import json
from types import SimpleNamespace
import threading
import time
from unittest.mock import MagicMock

import pytest

from computer import uia
from computer.messages import MessageTransactions
from computer.tool_bridge import execute
from computer.visual import VisualFallback, parse_grounding
from core.config import Config
from core.contracts import CallContext
from director.laya_runtime import LayaRuntime


@pytest.mark.parametrize("utterance", [
    "bluestack software open karo aur Play Store per bgmi download karo",
    "Open my emulator and install a game from the Play Store",
    "Open the app store and update my music app",
])
def test_software_store_workflow_is_not_media_playback(utterance):
    from director.heuristics import find_media_query
    from director.semantic_guard import guard_decision
    from director.base import DirectorDecision, DirectorTask
    from core.contracts import TaskType
    assert find_media_query(utterance, "") == ""
    decision = DirectorDecision(tasks=[DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "browser.media.play")])
    assert not guard_decision(utterance, decision).allowed
    assert decision.tasks == [] and decision.reasoning_required


def test_media_keyword_substrings_do_not_choose_next_track():
    from director.heuristics import _NEXT_RE
    assert not _NEXT_RE.search("pause the song on this page")
    assert _NEXT_RE.search("next song")


def test_visual_upload_requires_destination_approval_and_pins_gateway():
    gateway = MagicMock()
    gateway.candidates.return_value = [SimpleNamespace(provider_id="fixture-provider", protocol="openai_chat")]
    gateway.registry.base_url.return_value = "https://vision.example.invalid/v1"
    gateway.complete.return_value = SimpleNamespace(text="{}")
    approvals = SimpleNamespace(request=MagicMock(return_value={"ok": False}))
    visual = VisualFallback(None, approvals, gateway)
    frame = {"identity": (1, 2, "app", "Fixture"), "png": b"fixture-only"}
    with pytest.raises(ValueError, match="not approved"):
        visual._ask(CallContext(), "describe", [frame], threading.Event())
    gateway.complete.assert_not_called()
    approvals.request.return_value = {"ok": True}
    assert visual._ask(CallContext(), "describe", [frame], threading.Event()) == "{}"
    assert gateway.complete.call_args.args[1].allowed_provider_ids == ["fixture-provider"]
    assert "vision.example.invalid" in approvals.request.call_args.args[1]


def test_constrained_provider_never_falls_back_to_mock(app):
    from core.contracts import ModelRequirement, ProviderError
    gateway = app.orchestrator.gateway
    req = ModelRequirement(allowed_provider_ids=["not-configured"])
    assert gateway.candidates(req) == []
    with pytest.raises(ProviderError, match="authorized provider"):
        gateway.complete(app.ctx(), req, [])
    with pytest.raises(ProviderError, match="authorized provider"):
        list(gateway.stream(app.ctx(), req, []))


@pytest.fixture
def message_env(monkeypatch):
    values = {"recipient": "Fixture recipient", "composer": "", "send": "Send"}
    windows = {key: 42 for key in values}
    monkeypatch.setattr(uia, "describe_registered", lambda key: {
        "name": key, "window": "Disposable chat", "window_id": windows[key], "process_id": 7})
    monkeypatch.setattr(uia, "get_value", lambda key: {"ok": True, "value": values[key]})
    def write(key, text):
        values[key] = text
        return {"ok": True, "verified": True}
    monkeypatch.setattr(uia, "set_value", write)
    invoke = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(uia, "invoke_once", invoke)
    approvals = SimpleNamespace(request=MagicMock(return_value={"ok": True}))
    service = MessageTransactions(approvals)
    monkeypatch.setattr(service, "_matching_messages", lambda _: 0)
    params = {"recipient_element_id": "recipient", "composer_element_id": "composer",
              "send_element_id": "send", "recipient": "Fixture recipient", "message": "Exact text!"}
    return service, params, values, windows, invoke, approvals


def test_prepare_reads_exact_recipient_and_draft(message_env):
    service, params, values, _, invoke, approvals = message_env
    result = service.prepare(CallContext(), params)
    assert result["status"] == "drafted" and result["verified"]
    assert values["composer"] == params["message"]
    invoke.assert_not_called()
    approvals.request.assert_not_called()


@pytest.mark.parametrize("change,code", [("recipient", "recipient_mismatch"), ("draft", "existing_draft"),
                                          ("window", "message_target_unavailable")])
def test_prepare_rejects_wrong_conversation_or_existing_draft(message_env, change, code):
    service, params, values, windows, invoke, _ = message_env
    if change == "window":
        windows["send"] = 99
    else:
        values["recipient" if change == "recipient" else "composer"] = "Different"
    assert service.prepare(CallContext(), params)["error_code"] == code
    invoke.assert_not_called()


def test_confirmation_binds_recipient_and_exact_message(message_env):
    service, params, values, _, invoke, approvals = message_env
    ctx = CallContext()
    tx = service.prepare(ctx, params)
    def approve(*args, **kwargs):
        assert "Recipient: Fixture recipient" in args[1]
        assert "Exact text!" in args[1]
        assert kwargs["scope"]["recipient"] == "Fixture recipient"
        values["composer"] = "Changed by owner"
        return {"ok": True}
    approvals.request.side_effect = approve
    result = service.send(ctx, tx, threading.Event())
    assert result["error_code"] == "message_changed"
    invoke.assert_not_called()
    assert service.send(ctx, tx, threading.Event())["error_code"] == "stale_transaction"


def test_message_submission_is_not_delivery(message_env, monkeypatch):
    service, params, values, _, invoke, approvals = message_env
    ctx = CallContext()
    tx = service.prepare(ctx, params)
    def sent(_):
        values["composer"] = ""
        return {"ok": True}
    invoke.side_effect = sent
    monkeypatch.setattr(service, "_matching_messages", MagicMock(side_effect=[0, 1]))
    result = service.send(ctx, tx, threading.Event())
    assert result["ok"] and result["status"] == "submitted"
    assert result["sent"] is result["delivered"] is result["read"] is None
    invoke.assert_called_once()


def test_send_requires_same_owner_and_not_cancelled(message_env):
    service, params, _, _, invoke, _ = message_env
    tx = service.prepare(CallContext(person_id="owner"), params)
    assert service.send(CallContext(person_id="other"), tx, threading.Event())["error_code"] == "stale_transaction"
    cancel = threading.Event()
    cancel.set()
    assert service.send(CallContext(person_id="owner"), tx, cancel)["error_code"] == "cancelled"
    invoke.assert_not_called()


def test_desktop_lease_starts_after_confirmation(message_env, monkeypatch):
    service, params, values, _, invoke, approvals = message_env
    lease = SimpleNamespace(acquire=MagicMock(return_value={"ok": True}),
                            release=MagicMock(), check_takeover=MagicMock(return_value=None))
    service.desktop_lock = lease
    def approve(*args, **kwargs):
        lease.acquire.assert_not_called()
        return {"ok": True}
    approvals.request.side_effect = approve
    def sent(_):
        lease.acquire.assert_called_once()
        values["composer"] = ""
        return {"ok": True}
    invoke.side_effect = sent
    monkeypatch.setattr(service, "_matching_messages", MagicMock(side_effect=[0, 1]))
    ctx = CallContext()
    tx = service.prepare(ctx, params)
    assert service.send(ctx, tx, threading.Event())["ok"]
    lease.release.assert_called_once()


def test_pending_message_cannot_cross_session(message_env):
    service, params, _, _, invoke, _ = message_env
    tx = service.prepare(CallContext(session_id="owner"), params)
    assert service.send(CallContext(session_id="different"), tx, threading.Event())["error_code"] == "stale_transaction"
    invoke.assert_not_called()


def test_generic_invoke_cannot_bypass_recipient_confirmation(monkeypatch):
    monkeypatch.setattr(uia, "describe_registered", lambda _: {"name": "Send", "window": "Fixture chat"})
    computer = SimpleNamespace(execute=MagicMock())
    result = execute(computer, CallContext(), "desktop_control",
                     {"element_id": "send", "operation": "invoke"}, threading.Event())
    assert result["error_code"] == "message_transaction_required"
    computer.execute.assert_not_called()


@pytest.mark.parametrize("box", [[0, 0, 0, 1], [-1, 0, 20, 20], [True, 0, 20, 20],
                                 [0, 0, float("nan"), 20], [0, 0, 2000, 20]])
def test_visual_rejects_invalid_or_guessed_coordinates(box):
    with pytest.raises(ValueError):
        parse_grounding(json.dumps({"controls": [{"label": "Apply", "box": box}]}), 400, 200)


def test_normalized_box_maps_to_physical_pixels():
    _, controls = parse_grounding('{"controls":[{"label":"Apply","box":[250,250,750,750]}]}', 400, 200)
    assert controls[0]["point"] == [200, 100]


@pytest.fixture
def visual_env(monkeypatch):
    approvals = SimpleNamespace(request=MagicMock(return_value={"ok": False, "error_code": "owner_denied"}))
    visual = VisualFallback(None, approvals)
    frame = {"identity": (42, 7, "fixture.exe", "Fixture", (-900, 20, -500, 220), (-1000, 0, 0, 900), 1.5),
             "digest": "before", "width": 400, "height": 200, "png": b"test", "created": time.monotonic()}
    monkeypatch.setattr(visual, "_frame", lambda _: copy.deepcopy(frame))
    monkeypatch.setattr(visual, "_ask", lambda *args: '{"description":"Fixture","controls":[{"label":"Apply","box":[250,250,750,750]}]}')
    return visual, frame, approvals


def test_visual_targets_are_opaque_owner_bound_and_single_use(visual_env):
    visual, _, approvals = visual_env
    ctx = CallContext(person_id="owner")
    result = visual.observe(ctx, {"window_id": 42}, threading.Event())
    control = result["controls"][0]
    assert set(control) == {"target_id", "label"}
    params = {"target_id": control["target_id"], "expected_result": "Applied"}
    assert visual.click(CallContext(person_id="other"), params, threading.Event())["error_code"] == "stale_visual_target"
    approvals.request.assert_not_called()


def test_visual_changed_pixels_fail_before_owner_confirmation(visual_env):
    visual, frame, approvals = visual_env
    ctx = CallContext()
    result = visual.observe(ctx, {"window_id": 42}, threading.Event())
    frame["digest"] = "changed"
    clicked = visual.click(ctx, {"target_id": result["controls"][0]["target_id"], "expected_result": "Applied"}, threading.Event())
    assert clicked["error_code"] == "stale_visual_target" and not clicked["action_may_have_run"]
    approvals.request.assert_not_called()


def test_visual_denial_does_not_act(visual_env):
    visual, _, approvals = visual_env
    ctx = CallContext()
    result = visual.observe(ctx, {"window_id": 42}, threading.Event())
    assert visual.click(ctx, {"target_id": result["controls"][0]["target_id"], "expected_result": "Applied"},
                        threading.Event())["error_code"] == "owner_denied"
    approvals.request.assert_called_once()


def test_visual_after_click_failure_is_not_retried(visual_env, monkeypatch):
    from contextlib import nullcontext
    from computer import visual as module
    visual, _, approvals = visual_env
    approvals.request.return_value = {"ok": True}
    monkeypatch.setattr(module, "physical_pixels", nullcontext)
    monkeypatch.setattr(module.win, "focus_window", lambda _: True)
    click = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(module.input_mod, "click", click)
    ctx = CallContext()
    observed = visual.observe(ctx, {"window_id": 42}, threading.Event())
    result = visual.click(ctx, {"target_id": observed["controls"][0]["target_id"], "expected_result": "Applied"}, threading.Event())
    assert result["error_code"] == "action_outcome_unknown"
    assert result["action_may_have_run"]
    # Negative monitor origin and 150% DPI are already physical; no double scaling.
    click.assert_called_once_with(-700, 120)


def test_visual_requires_explicit_remote_consent_before_capture(monkeypatch):
    from computer import visual as module
    awareness = SimpleNamespace(settings=SimpleNamespace(remote_visual_consent=False))
    visual = VisualFallback(awareness, None)
    capture = MagicMock()
    monkeypatch.setattr(module.win, "list_windows", capture)
    result = visual.observe(CallContext(), {"window_id": 42}, threading.Event())
    assert result["error_code"] == "visual_unavailable"
    assert "not enabled" in result["error"]
    capture.assert_not_called()


def test_laya_is_optional_and_no_mission_authority(monkeypatch):
    runtime = LayaRuntime()
    assert runtime.route("hello") is None
    runtime.mode = "assist"
    monkeypatch.setattr(runtime, "predict", lambda _: {"choice": "mission", "confidence": 1., "roundtrip_ms": 1})
    assert runtime.route("do something") is None
    monkeypatch.setattr(runtime, "predict", lambda _: {"choice": "simple_action", "confidence": .95, "roundtrip_ms": 1})
    decision = runtime.route("do something")
    assert decision.source == "laya" and decision.reasoning_required
    assert not decision.tasks and not decision.mission_required and not decision.memory_writes


def test_laya_low_confidence_falls_back(monkeypatch):
    runtime = LayaRuntime(Config({"director": {"laya": {"mode": "assist"}}}))
    monkeypatch.setattr(runtime, "predict", lambda _: {"choice": "research", "confidence": .4, "roundtrip_ms": 1})
    assert runtime.route("look up") is None


def test_laya_missing_checkpoint_is_honest_and_does_not_download(tmp_path):
    # Give this test explicitly missing local assets so a developer's real pinned
    # Laya installation cannot turn the expected unavailable state into warmup.
    runtime = LayaRuntime(Config({"director": {"laya": {
        "mode": "assist", "python": str(tmp_path / "python.exe"),
        "source": str(tmp_path / "source"), "model": str(tmp_path / "missing-model")}}}))
    assert runtime.predict("test") is None
    assert runtime.last["state"] == "unavailable"


def test_laya_shadow_does_not_load_model_or_sample_by_default(monkeypatch):
    from director import laya_runtime
    spawn = MagicMock(side_effect=AssertionError("shadow model must remain unloaded"))
    monkeypatch.setattr(laya_runtime.subprocess, "Popen", spawn)
    runtime = LayaRuntime(Config({"director": {"laya": {
        "mode": "shadow", "python": "configured", "source": "configured",
        "model": "configured"}}}))
    runtime.start()
    runtime.observe("open notepad", "simple_action")
    assert runtime._process is None
    assert runtime.last == {"state": "shadow_only", "sampling": "disabled_by_default"}
    spawn.assert_not_called()


@pytest.mark.parametrize("text", ["WhatsApp open karke wapas physics wala Ko hi bhej do",
                                  "Mail open kar ke report bhej dena",
                                  "Editor kholo aur note likh do",
                                  "Calendar kholke appointment dikhana"])
def test_compound_hinglish_is_not_an_application_name(text):
    from director.base import DirectorDecision, DirectorTask
    from director.semantic_guard import guard_decision
    decision = DirectorDecision(tasks=[DirectorTask(capability="application.open", target="incorrect suffix")])
    verdict = guard_decision(text, decision)
    assert not verdict.allowed and not decision.tasks and decision.reasoning_required
