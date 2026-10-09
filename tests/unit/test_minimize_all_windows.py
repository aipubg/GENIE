from types import SimpleNamespace

from computer import verifier, windows_api
from computer.verifier import VerifyContext
from core.contracts import CallContext
from director.heuristics import HeuristicDirector


def _window(hwnd, cls, *, minimized=False):
    return SimpleNamespace(hwnd=hwnd, pid=hwnd + 100, title=f"Window {hwnd}",
                           class_name=cls, process="app.exe", visible=True,
                           minimized=minimized)


def test_minimize_all_routes_to_one_canonical_action():
    decision = HeuristicDirector().classify("Minimize all windows", CallContext())
    assert [task.capability for task in decision.tasks] == ["window.minimize_all"]


def test_minimize_all_preserves_shell_and_reports_only_actual_targets(monkeypatch):
    windows = [_window(1, "Notepad"), _window(2, "Progman"),
               _window(3, "Shell_TrayWnd"), _window(4, "Browser", minimized=True)]
    called = []
    monkeypatch.setattr(windows_api, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_api, "list_windows", lambda **kwargs: windows)
    monkeypatch.setattr(windows_api, "show_window",
                        lambda hwnd, command: called.append((hwnd, command)) or True)

    result = windows_api.minimize_all_application_windows()
    assert result["ok"]
    assert called == [(1, "minimize")]
    assert result["target_hwnds"] == [1]
    assert result["skipped_shell_hwnds"] == [2, 3]


def test_minimize_all_verifier_requires_every_fresh_window_to_be_minimized():
    ctx = VerifyContext(
        capability="window.minimize_all",
        result={"ok": True, "target_hwnds": [11, 12]},
        before={"windows": [{"hwnd": 11}, {"hwnd": 12}]},
        after={"windows": [{"hwnd": 11, "minimized": True},
                           {"hwnd": 12, "minimized": False}]})
    assert not verifier._verify_minimize_all(ctx).verified
    ctx.after["windows"][1]["minimized"] = True
    assert verifier._verify_minimize_all(ctx).verified


def test_minimize_all_is_exposed_only_when_permission_plan_and_verifier_exist():
    from computer.capability_manifest import model_tools, validate

    tools = {item["name"] for item in model_tools("chat")}
    assert "minimize_all_windows" in tools
    assert not any("window.minimize_all" in item for item in validate())
