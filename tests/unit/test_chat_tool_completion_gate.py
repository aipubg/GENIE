import threading
from types import SimpleNamespace

from core.contracts import CallContext
from core.tool_dialogue import run
from director.heuristics import HeuristicDirector


def test_open_and_write_app_request_does_not_collapse_to_app_launch():
    decision = HeuristicDirector().classify(
        "Open Notepad and write a leave application", CallContext())
    assert decision.tasks == []
    assert decision.reasoning_required
    assert decision.intent == "application_interaction"


def test_model_cannot_claim_document_written_after_only_open_receipt(monkeypatch):
    import core.tool_dialogue as dialogue

    opened = SimpleNamespace(text="", provider_id="", model="", tool_error="",
        tool_protocol="native", tool_calls=[{
            "id": "call-open", "name": "open_application",
            "args": {"target": "notepad"},
        }], raw={})
    claimed = SimpleNamespace(text="I opened Notepad and wrote the leave application.",
        provider_id="", model="", tool_error="", tool_protocol="native",
        tool_calls=[], raw={})

    class Gateway:
        def __init__(self):
            self.turn = 0

        def complete(self, *args, **kwargs):
            result = (opened, claimed)[self.turn]
            self.turn += 1
            return result

    monkeypatch.setattr(dialogue, "execute", lambda *args, **kwargs: {
        "ok": True, "verified": True, "capability": "application.open",
        "detail": "Notepad window opened", "window_id": 123,
    })
    request = "Open Notepad and write a leave application"
    reply = run(Gateway(), object(), CallContext(), None,
                [{"role": "user", "content": request}], threading.Event(),
                user_text=request)
    assert "could not verify completion" in reply.casefold()
    assert "document text" in reply.casefold()


def test_document_write_requirement_accepts_only_a_verified_text_action():
    from core.tool_dialogue import _required_tool_groups, _tool_satisfies_group

    groups = _required_tool_groups("Open Notepad and write a leave application")
    assert len(groups) == 2
    label, alternatives = groups[1]
    assert "document text" in label
    assert not _tool_satisfies_group("input_type", {},
        {"ok": True, "verified": False}, alternatives)
    assert _tool_satisfies_group("desktop_control", {"operation": "set_value"},
        {"ok": True, "verified": True}, alternatives)


def test_explicit_network_state_query_exposes_only_network_status_tool():
    from core.tool_dialogue import _tools_for_instruction

    tools = _tools_for_instruction(
        "Check which network interfaces are currently up and report their names and state.")
    assert [tool["function"]["name"] for tool in tools] == ["network_status"]


def test_network_tool_scope_is_preserved_for_followup_model_turns(monkeypatch):
    import core.tool_dialogue as dialogue
    request = "Check which network interfaces are currently up and report their names and state."
    tool_call = SimpleNamespace(text="", provider_id="gemini", model="gemini-3.8-flash",
        tool_error="", tool_protocol="native", tool_calls=[{
            "id": "network-call", "name": "network_status", "args": {},
        }], raw={})
    final = SimpleNamespace(text="The requested network state check completed.",
        provider_id="gemini", model="gemini-3.8-flash", tool_error="",
        tool_protocol="native", tool_calls=[], raw={})

    class Gateway:
        def __init__(self):
            self.turn = 0
            self.tool_names = []

        def complete(self, *args, **kwargs):
            self.tool_names.append([tool["function"]["name"] for tool in kwargs["tools"]])
            result = (tool_call, final)[self.turn]
            self.turn += 1
            return result

    gateway = Gateway()
    monkeypatch.setattr(dialogue, "execute", lambda *args, **kwargs: {
        "ok": True, "verified": True, "capability": "system.network.state",
        "detail": "Network state observed",
        "data": {"interfaces": [{"Name": "Wi-Fi", "Status": "Up"}]},
    })
    reply = run(gateway, object(), CallContext(), None,
                [{"role": "user", "content": request}], threading.Event(),
                user_text=request)
    assert reply == "Current network-interface states: Wi-Fi: Up."
    assert gateway.tool_names == [["network_status"]]


def test_network_state_reply_is_rendered_from_verified_receipt_only():
    from core.tool_dialogue import _network_status_reply

    assert _network_status_reply({
        "ok": True, "verified": True,
        "data": {"interfaces": [
            {"Name": "Wi-Fi", "Status": "Up"},
            {"Name": "Ethernet", "Status": "Disconnected"},
        ]},
    }) == "Current network-interface states: Wi-Fi: Up; Ethernet: Disconnected."
    assert "could not verify" in _network_status_reply({"ok": False}).casefold()
