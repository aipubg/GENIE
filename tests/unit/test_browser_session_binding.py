"""Existing session attachment and cross-workspace isolation regressions."""
from unittest.mock import MagicMock

import pytest

from browser import service, mode


def test_existing_selection_really_attaches(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    info = {"found": True, "attachable": True, "pid": 71, "debug_port": 9334,
            "exe": "brave.exe", "profile": "Default"}
    monkeypatch.setattr(mode, "detect_owner_browser", lambda *a, **k: info)
    attach = MagicMock(side_effect=lambda *a, **k: setattr(browser, "_active_tab", "chosen"))
    monkeypatch.setattr(browser, "_attach_existing", attach)
    result = browser.select_session({"mode": "owner_existing", "browser": "brave",
                                     "task_id": "owner", "tab_id": "chosen"})
    assert result["ok"] and result["session"]["tab_id"] == "chosen"
    attach.assert_called_once_with(info, tab_id="chosen")


def test_failed_attach_never_claims_control(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    monkeypatch.setattr(mode, "detect_owner_browser", lambda *a, **k: {"found": True, "attachable": True})
    monkeypatch.setattr(browser, "_attach_existing", MagicMock(side_effect=RuntimeError("port changed")))
    assert not browser.select_session({"mode": "owner_existing", "browser": "brave",
                                       "task_id": "owner"})["ok"]
    assert browser.session_context() == {}
    with pytest.raises(service.cdp.CDPError, match="port changed"):
        browser._connect_page()


def test_attachment_rejects_changed_port_owner(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    monkeypatch.setattr(service.cdp, "browser_ready", lambda *a, **k: {})
    monkeypatch.setattr(service, "_default_port_owner", lambda _: 99)
    with pytest.raises(service.cdp.CDPError, match="ownership"):
        browser._attach_existing({"debug_port": 9334, "pid": 71}, "chosen")
    assert not browser._owner_browser


def test_attachment_rejects_ambiguous_tabs(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    monkeypatch.setattr(service.cdp, "browser_ready", lambda *a, **k: {})
    monkeypatch.setattr(service, "_default_port_owner", lambda _: 71)
    monkeypatch.setattr(service.cdp, "page_targets", lambda _: [{"id": "one"}, {"id": "two"}])
    with pytest.raises(service.cdp.CDPError, match="ambiguous"):
        browser._attach_existing({"debug_port": 9334, "pid": 71})


def test_workspace_provider_does_not_rebind_another(monkeypatch, tmp_path):
    service.reset_browser()
    first = service.get_browser(workspace_root=tmp_path / "one")
    close = MagicMock()
    monkeypatch.setattr(first, "close", close)
    second = service.get_browser(workspace_root=tmp_path / "two")
    assert first is not second
    assert service.get_browser(workspace_root=tmp_path / "one") is first
    assert first.profile_dir == (tmp_path / "one/browser-profile").resolve()
    close.assert_not_called()
    service.reset_browser()


def test_other_task_cannot_replace_or_release_binding(tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    browser._task_session = {"task_id": "first", "mode": "owner_existing"}
    assert browser.select_session({"mode": "release", "task_id": "second"})["error_code"] == "session_locked"
    assert browser.session_context()["task_id"] == "first"
    assert browser.select_session({"mode": "release", "task_id": "first"})["released"]
    assert browser.session_context() == {}


def test_followup_navigation_inherits_selected_mode(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    browser._task_session = {"task_id": "owner", "mode": "owner_existing"}
    navigate = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(browser, "navigate", navigate)
    browser.handle("browser.navigate", {"url": "https://example.invalid"})
    assert navigate.call_args.args[0]["browser_mode"] == "owner_existing"


def test_owned_launch_failure_does_not_rotate_profile(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    launch = MagicMock(side_effect=service.cdp.CDPError("profile already in use"))
    monkeypatch.setattr(service.cdp, "launch", launch)
    outcome = browser.ensure(attempts=3)
    assert not outcome["ok"]
    assert launch.call_count == 1
    assert browser.profile_dir == (tmp_path / "browser-profile").resolve()


def test_scroll_uses_declared_dy(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    client = MagicMock()
    client.evaluate.side_effect = [{"x": 0, "y": 0}, None, {"x": 0, "y": 800}]
    monkeypatch.setattr(browser, "_connect_page", lambda: client)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    result = browser.scroll({"dy": 800})
    assert result["verify"]["verified"]
    assert any("800" in str(call) for call in client.evaluate.call_args_list)


def test_reload_verifies_same_tab_new_document(monkeypatch, tmp_path):
    browser = service.BrowserService(workspace_root=tmp_path)
    browser._active_tab = "tab-1"
    client = MagicMock()
    client.evaluate.side_effect = [
        {"url": "https://example.test/", "token": 1},
        {"url": "https://example.test/", "token": 2, "ready": "complete"},
    ]
    monkeypatch.setattr(browser, "_connect_page", lambda: client)
    result = browser.reload({})
    assert result["ok"] and result["tab_id"] == "tab-1"
    client.send.assert_called_once_with("Page.reload", {"ignoreCache": False})


def test_web_followups_keep_selected_mode():
    from director.heuristics import plan_web_action
    assert plan_web_action("Brave mein YouTube kholo")[0]["capability"] == "browser.session"
    for phrase, capability in (("scroll down", "browser.scroll"),
                               ("reload this page", "browser.reload")):
        step = plan_web_action(phrase)[0]
        assert step["capability"] == capability
        assert "browser_mode" not in step["params"]
