import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.db import Database
from core.contracts import ActionResult, CallContext, Completion
from core.tool_protocol import normalize_completion
from core.session_store import SessionStore
from missions.service import MissionService, MissionError
from computer.tool_bridge import execute, OPENAI_TOOLS


def provider_completion(text):
    return normalize_completion(Completion(text=text, model="fixture", provider_id="fixture"), OPENAI_TOOLS)


def test_archive_survives_context_compaction_and_restart(tmp_path):
    path = tmp_path / "history.db"
    db = Database(path)
    store = SessionStore(db, bound=4)
    for i in range(70):
        store.append(f"user {i}", f"reply {i}")
    newest = store.history()
    assert len(newest) == 100
    older = store.history(before=newest[0]["id"])
    assert len(older) == 40
    assert older[0]["text"] == "user 0"
    db.close()
    restarted = SessionStore(Database(path))
    assert restarted.history() == newest
    restarted.clear()
    assert restarted.history() == []


def test_archive_isolates_sessions(tmp_path):
    db = Database(tmp_path / "history.db")
    SessionStore(db, "first").append("private", "reply")
    assert SessionStore(db, "second").history() == []


def test_mission_delete_requires_settled_state(tmp_path):
    service = MissionService(Database(tmp_path / "missions.db"))
    ctx = CallContext()
    mission = service.create(ctx, "test")
    with pytest.raises(MissionError):
        service.delete(ctx, mission.mission_id)
    service.cancel(ctx, mission.mission_id)
    assert service.delete(ctx, mission.mission_id)
    assert service.get(mission.mission_id) is None
    assert not service.delete(ctx, mission.mission_id)


def fake_computer(ok=True):
    result = SimpleNamespace(to_dict=lambda: {"ok": ok, "data": {"text": "source"}})
    return SimpleNamespace(execute=MagicMock(return_value=result))


def test_live_volume_strips_untrusted_authority_fields():
    computer = fake_computer()
    execute(computer, CallContext(), "set_volume", {"level": 25, "confirmed": True}, threading.Event())
    assert computer.execute.call_args.args[1:] == ("system.volume.set", {"level": 25})


@pytest.mark.parametrize("value", [True, -1, 101, "25"])
def test_live_volume_validates(value):
    computer = fake_computer()
    assert not execute(computer, CallContext(), "set_volume", {"level": value}, threading.Event())["ok"]
    computer.execute.assert_not_called()


def test_search_uses_existing_executor_without_mission():
    computer = fake_computer()
    result = execute(computer, CallContext(), "search_web", {"query": "workbuddy AI & tools"}, threading.Event())
    assert result["ok"]
    assert [c.args[1] for c in computer.execute.call_args_list] == ["browser.navigate", "browser.detect_gate", "browser.extract"]
    assert "workbuddy+AI+%26+tools" in result["source_url"]


def test_search_failure_does_not_read_stale_page():
    computer = fake_computer(False)
    assert not execute(computer, CallContext(), "search_web", {"query": "test"}, threading.Event())["ok"]
    assert computer.execute.call_count == 1


def test_cancelled_tool_does_not_execute():
    computer = fake_computer()
    cancel = threading.Event()
    cancel.set()
    assert not execute(computer, CallContext(), "open_application", {"target": "notepad"}, cancel)["ok"]
    computer.execute.assert_not_called()


@pytest.mark.parametrize("url", ["file:///C:/private", "javascript:alert(1)", "https://user:password@example.com"])
def test_read_rejects_non_web_or_credentials(url):
    computer = fake_computer()
    assert not execute(computer, CallContext(), "read_web_page", {"url": url}, threading.Event())["ok"]
    computer.execute.assert_not_called()


def test_default_browser_handoff_is_not_page_verification(monkeypatch):
    from browser import default_browser
    start = MagicMock()
    monkeypatch.setattr(default_browser.os, "startfile", start, raising=False)
    monkeypatch.setattr(default_browser.os, "name", "nt")
    result = default_browser.open_url({"url": "https://example.com"})
    start.assert_called_once_with("https://example.com")
    assert result["ok"] and not result["page_verified"]
    assert result["verify"]["scope"] == "os-url-handoff"


def test_default_browser_plan_preserves_explicit_preference():
    from director.heuristics import plan_web_action
    plan = plan_web_action("default browser pe YouTube open karo")
    assert plan[0]["capability"] == "browser.open_default"


def test_browser_defaults_preserve_different_protocol_associations(monkeypatch):
    from browser import default_browser
    monkeypatch.setattr(default_browser.os, "name", "nt")
    monkeypatch.setattr(default_browser, "_association", lambda protocol, kind: {
        ("http", 2): "A.exe", ("http", 4): "Browser A", ("https", 2): "B.exe", ("https", 4): "Browser B",
    }[protocol, kind])
    result = default_browser.defaults()
    assert result["associations"]["http"]["name"] == "Browser A"
    assert result["associations"]["https"]["name"] == "Browser B"


def test_laya_shadow_is_disabled_without_configuration():
    from director.laya_shadow import LayaShadow
    shadow = LayaShadow(None)
    shadow.observe("open YouTube", "simple_action")
    assert shadow.last == {"state": "disabled"}


def test_packaged_apps_and_product_suffix_match_without_alias(monkeypatch):
    from computer import apps
    entries = [apps.AppEntry("WorkBuddy", "work.lnk", "lnk", "start_menu"),
               apps.AppEntry("WhatsApp", "shell:AppsFolder\\pkg!App", "appsfolder", "start_registration")]
    monkeypatch.setattr(apps, "discover", lambda **kw: entries)
    monkeypatch.setattr(apps, "_which_exe", lambda _: "")
    assert apps.resolve("WorkBuddy AI") is entries[0]
    assert apps.resolve("Whatsapp") is entries[1]


def test_ambiguous_apps_do_not_launch_arbitrary_candidate(monkeypatch):
    from computer import apps
    monkeypatch.setattr(apps, "discover", lambda **kw: [
        apps.AppEntry("Editor Alpha", "a.exe", "exe", "registry"),
        apps.AppEntry("Editor Beta", "b.exe", "exe", "registry")])
    monkeypatch.setattr(apps, "_which_exe", lambda _: "")
    assert apps.resolve("Editor") is None
    assert len(apps.resolve_candidates("Editor")) == 2


def test_stale_uia_handle_is_rejected_without_new_registration(monkeypatch):
    from computer import uia
    registry = uia._Registry()
    monkeypatch.setattr(uia.time, "time", lambda: 10)
    element_id = registry.put(object())
    monkeypatch.setattr(uia.time, "time", lambda: 10 + uia.ELEMENT_TTL_S + 1)
    assert registry.get(element_id) is None


def test_uia_retries_transient_com_initialization_failure(monkeypatch):
    import os
    if os.name != "nt":
        pytest.skip("Windows UIA is platform-specific")
    from computer import uia
    monkeypatch.setattr(uia, "_BACKEND", "unavailable")
    monkeypatch.setattr(uia, "_IMPORT_ERROR", "[WinError -2147221008] CoInitialize has not been called")
    assert uia.available()
    assert uia.status()["backend"] == "pywinauto"


def test_window_monitor_lookup_falls_back_when_windows_returns_null_monitor():
    import types
    from computer import windows_api
    monitor = windows_api.MonitorInfo(
        index=0, hmon=1, rect=(0, 0, 1920, 1080),
        work_rect=(0, 0, 1920, 1040), primary=True)
    isolated_globals = vars(windows_api).copy()
    isolated_globals.update({
        "IS_WINDOWS": True,
        "user32": SimpleNamespace(MonitorFromWindow=lambda *_args: None),
        "list_monitors": lambda: [monitor],
        "_rect": lambda _hwnd: (20, 20, 100, 100),
    })
    isolated_lookup = types.FunctionType(
        windows_api.monitor_index_for_window.__code__, isolated_globals)
    assert isolated_lookup(1) == 0


def test_named_browser_never_silently_selects_owned_profile():
    from browser.mode import decide_browser_mode
    assert decide_browser_mode("Brave me YouTube kholo", "brave", "youtube") == "owner_existing"
    assert decide_browser_mode("use your own browser Brave", "brave", "youtube") == "genie_owned"


def test_google_captcha_is_not_search_success():
    computer = fake_computer()
    computer.execute.side_effect = [SimpleNamespace(to_dict=lambda: {"ok": True}),
                                   SimpleNamespace(to_dict=lambda: {"ok": True, "data": {"gate": "captcha"}})]
    result = execute(computer, CallContext(), "search_web", {"query": "test"}, threading.Event())
    assert not result["ok"] and result["error_code"] == "website_gate"
    assert computer.execute.call_count == 2


def test_typed_tool_loop_uses_same_dispatcher(monkeypatch):
    from core import tool_dialogue
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[
        provider_completion('{"tool":"set_volume","args":{"level":25}}'),
        provider_completion('{"reply":"Volume changed."}')]))
    dispatch = MagicMock(return_value={"ok": True, "verified": True})
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    result = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event())
    assert result == "Volume changed."
    assert dispatch.call_args.args[2:4] == ("set_volume", {"level": 25})


def test_browser_fill_is_exposed_and_never_submits():
    from computer.tool_bridge import DECLARATIONS, execute
    declaration = next(item for item in DECLARATIONS if item["name"] == "browser_fill")
    assert "label" in declaration["parameters"]["required"]
    computer = SimpleNamespace(execute=MagicMock(return_value=SimpleNamespace(
        to_dict=lambda: {"ok": True, "verified": True})))
    result = execute(computer, CallContext(), "browser_fill",
                     {"text": "public query", "label": "Search"}, threading.Event())
    assert result["ok"]
    assert computer.execute.call_args.args[1:] == ("browser.fill", {
        "text": "public query", "label": "Search", "selector": ""})


def test_browser_click_without_effect_is_not_reported_as_success():
    from computer.tool_bridge import execute
    computer = SimpleNamespace(execute=MagicMock(return_value=ActionResult(
        True, "browser.act", "no observable change", verified=False)))
    result = execute(computer, CallContext(), "browser_click", {"text": "More information"},
                     threading.Event())
    assert result["ok"] is False
    assert result["error_code"] == "browser_action_unverified"


def test_verified_computer_receipt_survives_browser_bridge():
    computer = SimpleNamespace(execute=MagicMock(return_value=ActionResult(
        True, "browser.act", "navigation changed", verified=True)))
    result = execute(computer, CallContext(), "browser_click", {"text": "More information"}, threading.Event())
    assert result["ok"] and result["verified"]
    assert "error_code" not in result


@pytest.mark.parametrize("code", ["access_denied", "permission_denied", "owner_confirmation_required", "browser_action_unverified"])
def test_executor_diagnostics_reach_chat_and_voice(code):
    computer = SimpleNamespace(execute=MagicMock(return_value=ActionResult(
        False, "browser.act", "precise failure", data={"error_code": code, "error": "precise failure"})))
    result = execute(computer, CallContext(), "browser_click", {"text": "More information"}, threading.Event())
    assert result["error_code"] == code
    assert result["error"] == "precise failure"
    assert not result["ok"]


def test_browser_click_verifies_links_that_open_in_new_tabs(monkeypatch):
    from browser.service import BrowserService
    import browser.service as browser_module
    svc = BrowserService()
    client = SimpleNamespace(evaluate=MagicMock(side_effect=[
        "https://example.com/",
        {"clicked": True, "tag": "a", "text": "More information", 
         "href": "https://www.iana.org/help/example-domains", "target": "_blank",
         "x": 50, "y": 30},
        "unchanged page",
        "https://example.com/",
        "unchanged page",
    ]), send=MagicMock())
    monkeypatch.setattr(svc, "_connect_page", lambda: client)
    monkeypatch.setattr(browser_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(browser_module.cdp, "page_targets", MagicMock(side_effect=[
        [{"id": "original", "url": "https://example.com/"}],
        [{"id": "original", "url": "https://example.com/"},
         {"id": "new", "url": "https://www.iana.org/help/example-domains"}],
    ]))
    result = svc.act({"text": "More information"})
    assert result["ok"] and result["opened_tab"]
    assert result["verify"]["verified"]
    input_calls = [call for call in client.send.call_args_list
                   if len(call.args) > 1 and call.args[0] == "Input.dispatchMouseEvent"]
    assert [call.args[1]["type"] for call in input_calls] == [
        "mouseMoved", "mousePressed", "mouseReleased"]


@pytest.mark.parametrize("after,verified", [("menu is open", True), ("menu is closed", False)])
def test_browser_same_page_requires_observed_change(monkeypatch, after, verified):
    from browser.service import BrowserService
    import browser.service as browser_module
    svc = BrowserService()
    client = SimpleNamespace(evaluate=MagicMock(side_effect=[
        "https://example.com/", {"clicked": True, "tag": "button", "text": "Menu", "x": 20, "y": 30},
        "menu is closed", "https://example.com/", after,
    ]), send=MagicMock())
    monkeypatch.setattr(svc, "_connect_page", lambda: client)
    monkeypatch.setattr(browser_module.time, "sleep", lambda *_: None)
    result = svc.act({"text": "Menu", "expect_same_page": True})
    assert result["ok"] is verified
    if not verified:
        assert result["error_code"] == "browser_action_unverified"


@pytest.mark.parametrize("text", ["WhatsApp par ek message draft chahiye", "Could you interact with my browser?", "Meri screen par kya hai?"])
def test_reasoning_has_tools_without_an_action_keyword(text):
    from core.orchestrator import _use_conversational_tools
    from director.base import DirectorDecision
    assert _use_conversational_tools(text, DirectorDecision())


@pytest.mark.parametrize("text", ["hi", "thanks", "what did I just ask?"])
def test_greetings_and_history_do_not_enable_actions(text):
    from core.orchestrator import _use_conversational_tools
    from director.base import DirectorDecision
    assert not _use_conversational_tools(text, DirectorDecision())


def test_desktop_inspection_has_one_shared_declaration():
    from computer.tool_bridge import DECLARATIONS
    from voice.live import TOOLS
    assert sum(d["name"] == "inspect_desktop" for d in DECLARATIONS) == 1
    assert sum(d["name"] == "inspect_desktop" for d in TOOLS[0]["function_declarations"]) == 1
    computer = fake_computer()
    execute(computer, CallContext(), "inspect_desktop", {}, threading.Event())
    assert computer.execute.call_args.args[1:] == ("desktop.observe", {})


@pytest.mark.parametrize("utterance", [
    "Find installed WorkBuddy AI and WhatsApp. Do not launch or change any app.",
    "WhatsApp par message bhejo",
    "Open WhatsApp then draft a message",
    "Can you interact with Notepad?",
])
def test_app_name_does_not_reduce_interaction_to_launch(utterance):
    from director.base import DirectorDecision, DirectorTask
    from director.semantic_guard import guard_decision
    from core.contracts import TaskType
    decision = DirectorDecision(tasks=[DirectorTask(TaskType.APPLICATION_ACTION, "pc_main", "application.open", target="WhatsApp")])
    verdict = guard_decision(utterance, decision)
    assert not verdict.allowed
    assert decision.tasks == [] and decision.reasoning_required


def test_simple_launch_remains_a_direct_action():
    from director.base import DirectorDecision, DirectorTask
    from director.semantic_guard import guard_decision
    from core.contracts import TaskType
    decision = DirectorDecision(tasks=[DirectorTask(TaskType.APPLICATION_ACTION, "pc_main", "application.open", target="WhatsApp")])
    assert guard_decision("Open WhatsApp", decision).allowed
    assert len(decision.tasks) == 1


def test_unverified_browser_click_cannot_be_smoothed_into_success(monkeypatch):
    from core import tool_dialogue
    gateway = SimpleNamespace(complete=MagicMock(return_value=provider_completion(
        '{"tool":"browser_click","args":{"text":"More information"}}')))
    monkeypatch.setattr(tool_dialogue, "execute", lambda *args: {
        "ok": False, "error_code": "browser_action_unverified",
        "error": "The click was issued, but its effect could not be verified."})
    reply = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event())
    assert "not verified" in reply and "No retry" in reply
    gateway.complete.assert_called_once()


def test_provider_wrapped_tool_json_reaches_the_shared_dispatcher(monkeypatch):
    from core import tool_dialogue
    wrapped = '''I will act now.
<minimax:tool_call>
<tool_call>
{"tool":"browser_click","args":{"text":"More information"}}
</tool_call>
</minimax:tool_call>'''
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[
        provider_completion(wrapped), provider_completion('{"reply":"Done."}')]))
    dispatch = MagicMock(return_value={"ok": True, "verify": {"verified": True}})
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    result = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event())
    assert result == "Done."
    assert dispatch.call_args.args[2:4] == ("browser_click", {"text": "More information"})


def test_unverified_click_reobserves_once_without_repeating(monkeypatch):
    from core import tool_dialogue
    gateway = SimpleNamespace(complete=MagicMock(side_effect=[
        provider_completion('{"tool":"browser_click","args":{"text":"More information"}}'),
        provider_completion('{"reply":"The IANA destination is open."}')]))
    calls = []
    def dispatch(_computer, _ctx, name, _args, _cancel):
        calls.append(name)
        if name == "browser_click":
            return {"ok": False, "error_code": "browser_action_unverified", "url_after": ""}
        return {"ok": True, "url": "https://www.iana.org/help/example-domains"}
    monkeypatch.setattr(tool_dialogue, "execute", dispatch)
    reply = tool_dialogue.run(gateway, object(), CallContext(), object(), [], threading.Event())
    assert reply == "The IANA destination is open."
    assert calls == ["browser_click", "browser_observe"]


@pytest.mark.parametrize("utterance", [
    "List the currently open desktop window titles",
    "Which apps are currently open?",
    "Screen pe kya khula hai?",
])
def test_open_window_inventory_routes_to_existing_window_executor(utterance):
    from core.orchestrator import _fast_deterministic_decision
    decision = _fast_deterministic_decision(utterance, CallContext())
    assert decision is not None
    assert len(decision.tasks) == 1
    assert decision.tasks[0].capability == "window.list"
    assert decision.source == "heuristic-fast"


def test_multistep_browser_interaction_falls_back_to_chat_tool_loop():
    from core.orchestrator import _apply_web_action_plan
    from director.base import DirectorDecision, DirectorTask
    from core.contracts import TaskType
    decision = DirectorDecision(tasks=[DirectorTask(
        type=TaskType.BROWSER_ACTION, capability="browser.navigate",
        params={"url": "https://example.com"})])
    routed = _apply_web_action_plan(
        "In GENIE browser, open https://example.com, observe its visible links, "
        "click the observed More information link, and report whether it loaded.", decision)
    assert routed.tasks == []
    assert routed.reasoning_required is True
    assert routed.mission_required is False
    assert routed.provider_category == "reasoning"
    assert routed.raw["reason"] == "web-interaction-tool-loop"


def test_settings_uri_rejects_command_injection():
    from computer.system_tools import open_settings
    assert not open_settings('sound & powershell.exe')["ok"]


def test_explorer_shell_is_not_file_explorer_window():
    from computer.windows_api import WindowInfo
    shell = WindowInfo(1, "Program Manager", "Progman", 99, process="explorer.exe")
    assert shell.to_dict()["presentation"] == "shell_surface"


def test_enum_windows_callback_contains_per_window_metadata_failures(monkeypatch):
    from computer import windows_api as win
    fake_user32 = SimpleNamespace(
        IsWindowVisible=lambda _hwnd: True,
        EnumWindows=lambda callback, state: callback(123, state),
        IsIconic=lambda _hwnd: False,
        IsZoomed=lambda _hwnd: False,
    )
    monkeypatch.setattr(win, "IS_WINDOWS", True)
    monkeypatch.setattr(win, "user32", fake_user32)
    monkeypatch.setattr(win, "_window_title", lambda _hwnd: "Test")
    monkeypatch.setattr(win, "_pid_of", lambda _hwnd: 1234)
    monkeypatch.setattr(win, "_class_name", lambda _hwnd: "TestWindow")
    monkeypatch.setattr(win, "_process_name", lambda _pid: "test.exe")
    monkeypatch.setattr(win, "_rect", lambda _hwnd: (0, 0, 100, 100))
    monkeypatch.setattr(win, "monitor_index_for_window", lambda _hwnd: (_ for _ in ()).throw(RuntimeError("display changed")))
    assert win.list_windows() == []


def test_create_folder_uses_redirected_location_and_real_verifier(tmp_path, monkeypatch):
    from computer import files, state
    from computer.service import ComputerService
    from security.trust import TrustService
    desktop = tmp_path / "OneDrive" / "Desktop"
    monkeypatch.setattr(files, "user_locations", lambda: {"desktop": str(desktop)})
    monkeypatch.setattr(state, "snapshot", lambda **kw: SimpleNamespace(to_dict=lambda: {}))
    db = Database(tmp_path / "trust.db")
    computer = ComputerService(trust=TrustService(db), workspace_root=str(tmp_path / "workspace"))
    result = execute(computer, CallContext(), "create_folder",
                     {"location": "desktop", "name": "Project notes"}, threading.Event())
    assert result["ok"] and result["verified"]
    assert (desktop / "Project notes").is_dir()
    assert not (tmp_path / "workspace" / "Project notes").exists()


@pytest.mark.parametrize("name", ["../outside", "C:\\escape", "..", "CON", "LPT1.txt", "notes.", "bad:name", ""])
def test_create_folder_rejects_invalid_or_escaping_name(tmp_path, name):
    computer = fake_computer()
    result = execute(computer, CallContext(), "create_folder",
                     {"location": str(tmp_path), "name": name}, threading.Event())
    assert not result["ok"]
    computer.execute.assert_not_called()


def test_set_toggle_is_idempotent_and_verified(monkeypatch):
    from computer import uia, verifier
    toggle = SimpleNamespace(CurrentToggleState=0)
    def flip():
        toggle.CurrentToggleState = 1 - toggle.CurrentToggleState
    toggle.Toggle = MagicMock(side_effect=flip)
    wrapper = SimpleNamespace(iface_toggle=toggle)
    monkeypatch.setattr(uia._REGISTRY, "get", lambda _: wrapper)
    monkeypatch.setattr(uia, "describe_registered", lambda _: {"name": "Notifications", "window": "Settings"})
    first = uia.set_toggle("observed", True)
    second = uia.set_toggle("observed", True)
    assert first["ok"] and second["ok"]
    toggle.Toggle.assert_called_once()
    assert verifier.verify("uia.set_toggle", {"enabled": True}, first, {}, {}).verified
    toggle.CurrentToggleState = 0
    assert not verifier.verify("uia.set_toggle", {"enabled": True}, first, {}, {}).verified


def test_sensitive_control_waits_for_owner_and_rejects_changed_target(monkeypatch):
    from computer import uia
    computer = fake_computer()
    computer.approvals = SimpleNamespace(request=MagicMock(return_value={"ok": True}))
    monkeypatch.setattr(uia, "describe_registered", MagicMock(side_effect=[
            {"name": "Delete draft", "window": "Alice - WhatsApp"},
            {"name": "Delete draft", "window": "Bob - WhatsApp"}]))
    result = execute(computer, CallContext(), "desktop_control",
                     {"element_id": "observed", "operation": "invoke", "confirmed": True}, threading.Event())
    assert result["error_code"] == "stale_element"
    computer.approvals.request.assert_called_once()
    computer.execute.assert_not_called()


def test_approved_control_runs_through_existing_executor(monkeypatch):
    from computer import uia
    computer = fake_computer()
    computer.approvals = SimpleNamespace(request=MagicMock(return_value={"ok": True}))
    monkeypatch.setattr(uia, "describe_registered", lambda _: {"name": "Delete draft", "window": "Alice - WhatsApp"})
    assert execute(computer, CallContext(), "desktop_control",
                   {"element_id": "observed", "operation": "invoke", "confirmed": True}, threading.Event())["ok"]
    assert computer.execute.call_args.args[1:] == ("uia.invoke", {"element_id": "observed"})


def test_approval_is_single_use_and_never_leaks_id_to_model():
    import time
    from concurrent.futures import ThreadPoolExecutor
    from computer.approvals import ActionApprovals
    approvals = ActionApprovals()
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(approvals.request, CallContext(), "Send to Alice", threading.Event(), 2)
        deadline = time.monotonic() + 1
        pending = approvals.pending()
        while not pending and time.monotonic() < deadline:
            time.sleep(0.005)
            pending = approvals.pending()
        assert len(pending) == 1
        key = pending[0]["id"]
        assert not approvals.resolve(key, "true")
        assert approvals.resolve(key, True)
        assert not approvals.resolve(key, True)
        result = future.result(timeout=1)
    assert result["ok"] and "id" not in result
    assert approvals.pending() == []


def test_approval_expiry_and_agent_restriction():
    from computer.approvals import ActionApprovals
    approvals = ActionApprovals()
    assert not approvals.request(CallContext(), "test", threading.Event(), timeout_s=0)["ok"]
    assert approvals.pending() == []
    assert not approvals.request(CallContext(agent_id="agent"), "test", threading.Event())["ok"]


def test_model_cannot_answer_its_own_confirmation(monkeypatch):
    from computer import uia
    monkeypatch.setattr(uia, "describe_registered", lambda _: {"name": "Yes", "window": "GENIE - Confirm action"})
    computer = fake_computer()
    result = execute(computer, CallContext(), "desktop_control",
                     {"element_id": "observed", "operation": "invoke"}, threading.Event())
    assert result["error_code"] == "owner_required"
    computer.execute.assert_not_called()


def test_uia_access_denied_is_not_reported_as_no_matching_control(monkeypatch):
    from computer import uia
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "_desktop", lambda: (_ for _ in ()).throw(PermissionError("Access is denied")))
    assert uia.find_elements(window_title="Admin application") == []
    assert uia.last_error() == {"error_code": "access_denied", "error": "Access is denied"}


def test_uia_com_survives_request_threads_and_nested_calls(monkeypatch):
    import sys
    from types import ModuleType
    from computer import uia
    calls = []
    pythoncom = ModuleType("pythoncom")
    pythoncom.CoInitializeEx = lambda mode: calls.append(("init", mode))
    pythoncom.CoUninitialize = lambda: calls.append("uninit")
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    worker = uia._UIAWorker()
    monkeypatch.setattr(uia, "_WORKER", worker)

    @uia._com_sta
    def nested():
        return threading.get_ident()

    @uia._com_sta
    def outer():
        return nested()

    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=1) as first:
            first_id = first.submit(outer).result()
        with ThreadPoolExecutor(max_workers=1) as second:
            assert second.submit(outer).result() == first_id
        assert calls == [("init", 0)]
        assert first_id != threading.get_ident()
    finally:
        worker.close()
    assert calls == [("init", 0), "uninit"]


def test_uninstall_request_never_routes_to_application_open():
    from director.heuristics import HeuristicDirector
    decision = HeuristicDirector().classify("uninstall WorkBuddy app", CallContext())
    assert all(task.capability != "application.open" for task in decision.tasks)
    assert decision.reasoning_required


@pytest.mark.parametrize("phrase", [
    "Turn Wi-Fi off", "Bluetooth band karo", "create folder notes", "open installed app",
    "WhatsApp par message bhejo", "open settings and uninstall app", "माइक्रोफोन बंद करो",
])
def test_action_detection_covers_owner_reported_capability_requests(phrase):
    from core.tool_dialogue import action_requested
    assert action_requested(phrase)


def test_access_denied_receipt_is_reported_without_model_smoothing():
    from core import tool_dialogue
    computer = SimpleNamespace()
    completion = provider_completion('{"tool":"open_application","args":{"target":"app"}}')
    gateway = SimpleNamespace(complete=MagicMock(return_value=completion))
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(tool_dialogue, "execute", lambda *args: {
            "ok": False, "capability": "application.open", "detail": "denied: no grant for computer:app:open"})
        reply = tool_dialogue.run(gateway, computer, CallContext(), object(), [], threading.Event())
    finally:
        monkeypatch.undo()
    assert "denied: no grant" in reply and "No action was completed" in reply
    gateway.complete.assert_called_once()
