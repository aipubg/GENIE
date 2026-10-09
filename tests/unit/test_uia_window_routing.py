import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from computer import uia, windows_api
from computer.tool_bridge import execute
from core.contracts import CallContext


def test_named_window_uses_native_handle_not_global_uia_tree(monkeypatch):
    monkeypatch.setattr(windows_api, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_api, "list_windows", lambda: [SimpleNamespace(title="Fixture", hwnd=42)])
    desktop = MagicMock()
    assert uia._window_root(desktop, "Fixture") is desktop.window.return_value.wrapper_object.return_value
    desktop.windows.assert_not_called()
    desktop.window.assert_called_once_with(handle=42)


def test_duplicate_titles_require_explicit_observed_handle(monkeypatch):
    monkeypatch.setattr(windows_api, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_api, "list_windows", lambda: [SimpleNamespace(title="Fixture", hwnd=i) for i in (42, 43)])
    desktop = MagicMock()
    assert uia._window_root(desktop, "Fixture") is None
    assert uia.last_error()["error_code"] == "ambiguous_window"
    assert [row["window_id"] for row in uia.last_error()["candidates"]] == [42, 43]
    uia._window_root(desktop, "Fixture", 43)
    desktop.window.assert_called_once_with(handle=43)
    assert uia._window_root(desktop, "Different app", 43) is None


def test_duplicate_window_candidates_include_process_identity(monkeypatch):
    monkeypatch.setattr(windows_api, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_api, "list_windows", lambda: [
        SimpleNamespace(title="Fixture", hwnd=42, pid=100, process="host.exe", class_name="Host"),
        SimpleNamespace(title="Fixture", hwnd=43, pid=101, process="render.exe", class_name="Content")])
    desktop = MagicMock()
    assert uia._window_root(desktop, "Fixture") is None
    candidates = uia.last_error()["candidates"]
    assert candidates[1] == {"window": "Fixture", "window_id": 43, "pid": 101,
                              "process": "render.exe", "class_name": "Content"}
    desktop.window.assert_not_called()


def test_bounded_walk_does_not_materialize_entire_descendant_tree():
    leaf = SimpleNamespace(children=MagicMock(return_value=[]))
    root = SimpleNamespace(children=MagicMock(return_value=[leaf]), descendants=MagicMock())
    stream = uia._bounded_descendants(root, depth=32)
    assert next(stream) is leaf
    leaf.children.assert_not_called()
    stream.close()
    root.descendants.assert_not_called()


def test_walk_reaches_webview_beyond_old_depth_limit():
    root = SimpleNamespace(children=lambda: [])
    leaf = root
    for _ in range(17):
        parent = SimpleNamespace(children=lambda child=root: [child])
        root = parent
    assert leaf in list(uia._bounded_descendants(root, depth=32))
    assert all(node is not leaf for node in uia._bounded_descendants(root, depth=8))


def test_bad_subtree_does_not_hide_other_controls():
    bad = SimpleNamespace(children=MagicMock(side_effect=PermissionError("Access denied")))
    leaf = SimpleNamespace(children=lambda: [])
    good = SimpleNamespace(children=lambda: [leaf])
    root = SimpleNamespace(children=lambda: [bad, good])
    assert any(node is leaf for node in uia._bounded_descendants(root, depth=4))


def test_scan_has_node_budget():
    def node_factory():
        return SimpleNamespace(children=lambda: [node_factory()])
    node = node_factory()
    assert len(list(uia._bounded_descendants(node, depth=32, max_nodes=5))) == 5
    assert uia.last_error()["error_code"] == "uia_scan_incomplete"


def test_walk_deduplicates_provider_runtime_ids():
    first = SimpleNamespace(element_info=SimpleNamespace(runtime_id=[4, 12]), children=lambda: [])
    duplicate = SimpleNamespace(element_info=SimpleNamespace(runtime_id=[4, 12]), children=lambda: [])
    root = SimpleNamespace(children=lambda: [first, duplicate])
    assert list(uia._bounded_descendants(root, depth=8)) == [first]


def test_packaged_app_existing_window_uses_identity_not_title_count(monkeypatch, tmp_path):
    from computer.executor import Executor
    from computer import apps
    entry = apps.AppEntry(name="Fixture", target="shell:AppsFolder\\test.package!App", method="appsfolder", source="fixture")
    host = SimpleNamespace(hwnd=42, pid=100, title="Fixture", visible=True, class_name="Host", process="host.exe")
    content = SimpleNamespace(hwnd=43, pid=101, title="Fixture", visible=True, class_name="Content", process="render.exe")
    unrelated = SimpleNamespace(hwnd=44, pid=102, title="Fixture", visible=True, class_name="Browser", process="browser.exe")
    monkeypatch.setattr(windows_api, "list_windows", lambda: [host, content, unrelated])
    monkeypatch.setattr(windows_api, "application_user_model_id", lambda pid: "test.package!App" if pid in (100, 101) else "")
    monkeypatch.setattr(windows_api, "focus_window", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(apps, "resolve", lambda _: entry)
    launch = MagicMock()
    monkeypatch.setattr(apps, "launch", launch)
    executor = Executor(workspace_root=str(tmp_path))
    result = executor._open_app(CallContext(), "native-launch", {"target": "Fixture"}, {})
    assert result["ok"] and result["already_running"] and result["hwnd"] == 42
    assert result["application_id"] == "test.package!App"
    launch.assert_not_called()


def test_successful_launch_without_observation_is_uncertain_not_retried(monkeypatch, tmp_path):
    from computer.executor import Executor
    from computer import apps
    entry = apps.AppEntry(name="Fixture", target="shell:AppsFolder\\test.package!App", method="appsfolder", source="fixture")
    monkeypatch.setattr(apps, "resolve", lambda _: entry)
    monkeypatch.setattr(apps, "launch", lambda _: {"ok": True})
    executor = Executor(workspace_root=str(tmp_path))
    monkeypatch.setattr(executor, "_app_windows", lambda *_: [])
    result = executor._open_app(CallContext(), "native-launch", {"target": "Fixture", "wait_s": 0}, {})
    assert result["error_code"] == "action_outcome_unknown" and result["action_may_have_run"]


def test_window_id_reaches_canonical_executor():
    computer = SimpleNamespace(execute=MagicMock(return_value=SimpleNamespace(to_dict=lambda: {"ok": True})))
    execute(computer, CallContext(), "desktop_controls", {"window": "Fixture", "window_id": 42}, threading.Event())
    assert computer.execute.call_args.args[1] == "uia.find"
    assert computer.execute.call_args.args[2]["window_id"] == 42
    assert computer.execute.call_args.args[2]["depth"] == 32


def test_browser_confirmation_uses_executor_and_checks_page_identity():
    page = {"url": "https://fixture.invalid", "tab_id": "tab1", "document_id": "doc1", "interactive": [{"text": "Send"}]}
    def receipt(data):
        return SimpleNamespace(to_dict=lambda: {"ok": True, "verified": True, "data": data})
    computer = SimpleNamespace(execute=MagicMock(side_effect=[receipt(page), receipt(page), receipt({})]),
                               approvals=SimpleNamespace(request=MagicMock(return_value={"ok": True})))
    result = execute(computer, CallContext(), "browser_click", {"text": "Send"}, threading.Event())
    assert result["ok"]
    computer.approvals.request.assert_called_once()
    assert computer.execute.call_args.args[1:] == ("browser.act", {"text": "Send", "expected_url": page["url"],
        "expected_tab_id": "tab1", "expected_document_id": "doc1", "exact_text": True})


def test_browser_confirmation_rejects_navigation_during_approval():
    page = {"url": "https://fixture.invalid", "tab_id": "tab1", "document_id": "doc1", "interactive": [{"text": "Send"}]}
    fresh = {**page, "document_id": "doc2"}
    computer = SimpleNamespace(execute=MagicMock(side_effect=[
        SimpleNamespace(to_dict=lambda: {"ok": True, "data": page}),
        SimpleNamespace(to_dict=lambda: {"ok": True, "data": fresh})]),
        approvals=SimpleNamespace(request=MagicMock(return_value={"ok": True})))
    result = execute(computer, CallContext(), "browser_click", {"text": "Send"}, threading.Event())
    assert result["error_code"] == "stale_element"
    assert [call.args[1] for call in computer.execute.call_args_list] == ["browser.observe", "browser.observe"]


def test_declined_browser_confirmation_never_clicks():
    page = {"url": "https://fixture.invalid", "tab_id": "tab1", "document_id": "doc1", "interactive": [{"text": "Delete"}]}
    computer = SimpleNamespace(execute=MagicMock(return_value=SimpleNamespace(to_dict=lambda: {"ok": True, "data": page})),
        approvals=SimpleNamespace(request=MagicMock(return_value={"ok": False, "error_code": "owner_declined"})))
    result = execute(computer, CallContext(), "browser_click", {"text": "Delete"}, threading.Event())
    assert result["error_code"] == "owner_declined"
    computer.execute.assert_called_once()
