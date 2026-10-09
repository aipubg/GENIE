import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from computer.tool_bridge import OPENAI_TOOLS, execute
from core.contracts import CallContext, Completion, ModelSpec
from core.tool_protocol import normalize_completion
from models.providers.openai_compat import OpenAICompatAdapter, OpenAIResponsesAdapter


def normalized(text="", calls=None):
    return normalize_completion(Completion(text=text, model="test", provider_id="test"), OPENAI_TOOLS, calls)


def native(name, args, identity="call1"):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_exact_owner_failure_becomes_two_calls_not_visible_text():
    result = normalized('Checking the requested action...{"tool":"inspect_desktop","args":{}}\n'
                        '{"tool":"desktop_windows","args":{}}')
    assert not result.text and not result.tool_error
    assert [call["name"] for call in result.tool_calls] == ["inspect_desktop", "desktop_windows"]


@pytest.mark.parametrize("text", [
    'Mera plan: open the app\n{"tool":"find_application","args":{"target":"WhatsApp"}}',
    '{"tool":"missing","args":{}}',
    '{"tool":"set_volume","args":{"level":true}}',
    '{"tool":"set_volume","args":{"level":2,"confirmed":true}}',
    '{"tool":"set_volume","args":{}}',
    '{"tool":"set_volume","tool":"set_mute","args":{}}',
    '{"tool":"set_volume","args":{"level":NaN}}',
    '{"tool":"desktop_windows","args":{}} broken',
    '{"reply":"ok","tool":"desktop_windows","args":{}}',
    '{"reply":"{\\"tool\\":\\"desktop_windows\\",\\"args\\":{}}"}',
    '\n'.join('{"tool":"desktop_windows","args":{}}' for _ in range(9)),
])
def test_bad_envelope_never_becomes_executable_or_visible(text):
    result = normalized(text)
    assert result.tool_error and not result.tool_calls and not result.text


def test_native_calls_preserve_identity_and_hide_intermediate_content():
    result = normalized("About to act", [native("desktop_windows", {})])
    assert result.tool_protocol == "native" and result.tool_calls[0]["id"] == "call1"
    assert not result.text


def test_native_calls_preserve_google_thought_signature():
    call = native("desktop_windows", {})
    call["extra_content"] = {"google": {"thought_signature": "opaque-signature-fixture"}}
    result = normalized(calls=[call])
    assert result.tool_calls[0]["extra_content"] == call["extra_content"]


def test_duplicate_native_ids_reject_entire_batch():
    result = normalized(calls=[native("desktop_windows", {}), native("inspect_desktop", {})])
    assert result.tool_error and not result.tool_calls


def test_native_adapter_receives_catalog_and_returns_structured_calls(monkeypatch):
    adapter = OpenAICompatAdapter(base_url="https://fixture.invalid/v1")
    post = MagicMock(return_value={"choices": [{"message": {"content": None,
                                      "tool_calls": [native("desktop_windows", {})]}}]})
    monkeypatch.setattr(adapter, "_post", post)
    spec = ModelSpec("test", "test", "Test", capabilities=["tools"])
    result = adapter.complete(spec, [{"role": "user", "content": "Observe"}], tools=OPENAI_TOOLS)
    assert post.call_args.args[1]["tools"] == OPENAI_TOOLS
    assert result.tool_calls[0]["name"] == "desktop_windows"


def test_compatibility_is_adapter_only_and_continues_with_receipts(monkeypatch):
    adapter = OpenAICompatAdapter(base_url="https://fixture.invalid/v1")
    post = MagicMock(return_value={"choices": [{"message": {"content": '{"reply":"Done"}'}}]})
    monkeypatch.setattr(adapter, "_post", post)
    spec = ModelSpec("test", "test", "Test")
    messages = [{"role": "assistant", "content": None, "tool_calls": [native("desktop_windows", {})]},
                {"role": "tool", "tool_call_id": "call1", "content": '{"ok":true}'}]
    assert adapter.complete(spec, messages, tools=OPENAI_TOOLS).text == "Done"
    payload = post.call_args.args[1]
    assert "tools" not in payload and payload["messages"][-1]["role"] == "user"
    assert "UNTRUSTED TOOL RECEIPT" in payload["messages"][-1]["content"]


def test_responses_sends_tools_and_matching_function_outputs(monkeypatch):
    adapter = OpenAIResponsesAdapter(base_url="https://fixture.invalid/v1")
    post = MagicMock(return_value={"output": [{"type": "function_call", "call_id": "r2",
                          "name": "inspect_desktop", "arguments": "{}"}]})
    monkeypatch.setattr(adapter, "_post", post)
    messages = [{"role": "assistant", "content": None, "tool_calls": [native("desktop_windows", {})],
                 "response_items": [{"type": "reasoning", "id": "reason1", "summary": []}]},
                {"role": "tool", "tool_call_id": "call1", "content": '{"ok":true}'}]
    spec = ModelSpec("test", "test", "Test", capabilities=["tools"])
    result = adapter.complete(spec, messages, tools=OPENAI_TOOLS)
    payload = post.call_args.args[1]
    assert payload["input"][0]["type"] == "reasoning"
    assert payload["input"][-1]["type"] == "function_call_output"
    assert payload["input"][-1]["call_id"] == "call1"
    assert payload["tools"][0]["name"] == "inspect_desktop"
    assert result.tool_calls[0]["id"] == "r2"


def test_loop_dispatches_both_calls_and_returns_results_to_same_model(monkeypatch):
    from core import tool_dialogue
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[
        normalized(calls=[native("inspect_desktop", {}, "one"), native("desktop_windows", {}, "two")]),
        normalized("Observed actual windows.")]))
    dispatch = MagicMock(return_value={"ok": True, "verified": True})
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    reply = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event())
    assert reply == "Observed actual windows."
    assert [call.args[2] for call in dispatch.call_args_list] == ["inspect_desktop", "desktop_windows"]
    continuation = gateway.complete.call_args.args[2]
    assert [item["tool_call_id"] for item in continuation if item["role"] == "tool"] == ["one", "two"]
    assert gateway.complete.call_args.kwargs["preferred_model"] == ("test", "test")


def test_tool_loop_returns_google_thought_signature_in_assistant_history(monkeypatch):
    from core import tool_dialogue
    call = native("desktop_windows", {}, "sig-call")
    call["extra_content"] = {"google": {"thought_signature": "opaque-signature-fixture"}}
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[
        normalized(calls=[call]), normalized("Read-only result received.")]))
    monkeypatch.setattr(tool_dialogue, "execute",
                        MagicMock(return_value={"ok": True, "verified": True}))
    result = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event(),
                               user_text="Find the current window list.")
    assert result == "Read-only result received."
    history = gateway.complete.call_args.args[2]
    assistant = next(item for item in history if item.get("role") == "assistant")
    assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == \
        "opaque-signature-fixture"


def test_replayed_native_identity_does_not_repeat_action(monkeypatch):
    from core import tool_dialogue
    same = normalized(calls=[native("desktop_windows", {})])
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[same, same, normalized("Done")]))
    dispatch = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    assert tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event()) == "Done"
    dispatch.assert_called_once()


def test_model_return_after_cancel_cannot_dispatch(monkeypatch):
    from core import tool_dialogue
    cancel = threading.Event()
    def complete(*_args, **_kwargs):
        cancel.set()
        return normalized(calls=[native("desktop_windows", {})])
    dispatch = MagicMock()
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    assert tool_dialogue.run(SimpleNamespace(complete=complete), object(), CallContext(), object(), [], cancel) == "Stopped."
    dispatch.assert_not_called()


def test_shared_dispatcher_rejects_schema_before_computer_execution():
    computer = SimpleNamespace(execute=MagicMock())
    result = execute(computer, CallContext(), "desktop_windows", {"unexpected": 1}, threading.Event())
    assert result["error_code"] == "invalid_arguments"
    computer.execute.assert_not_called()


def test_stream_history_excludes_progress(monkeypatch):
    from core import lifecycle
    from core.lifecycle import Daemon
    remembered = MagicMock()
    monkeypatch.setattr(lifecycle, "_remember_turn", remembered)
    daemon = SimpleNamespace(ready=True, orchestrator=SimpleNamespace(stream_text=lambda *_: iter([
        ("Checking the requested action...", "progress"), ("Actual final answer", "final")])))
    assert list(Daemon.stream_chat(daemon, "Test request"))[-1] == ("Actual final answer", "final")
    assert remembered.call_args.args[-1] == "Actual final answer"
