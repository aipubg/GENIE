from types import SimpleNamespace

from computer import verifier, windows_api
from computer.verifier import VerifyContext
from computer.service import ComputerService
from core.contracts import CallContext


def test_text_entry_without_observed_target_is_rejected_before_execution(monkeypatch):
    service = ComputerService()
    called = []
    monkeypatch.setattr(service.executor, "execute", lambda *a, **k: called.append(True))
    monkeypatch.setattr(windows_api, "list_windows", lambda: [])

    result = service.execute(CallContext(trace_id="task-a"), "input.type_text", {"text": "hello"})

    assert not result.ok
    assert result.data["error_code"] == "target_not_grounded"
    assert not called


def test_text_entry_rejects_title_mismatch_for_observed_hwnd(monkeypatch):
    service = ComputerService()
    called = []
    monkeypatch.setattr(service.executor, "execute", lambda *a, **k: called.append(True))
    monkeypatch.setattr(windows_api, "list_windows", lambda: [
        SimpleNamespace(hwnd=44, pid=8, process="notepad.exe", title="Untitled - Notepad")])

    result = service.execute(CallContext(trace_id="task-a"), "input.type_text",
                             {"text": "hello", "window_id": 44,
                              "verify_in_window": "Different window"})

    assert not result.ok
    assert result.data["error_code"] == "target_not_grounded"
    assert not called


def test_typed_text_verifier_never_accepts_unreadable_or_ungrounded_input(monkeypatch):
    context = VerifyContext("input.type_text", params={"text": "the requested text"},
                            result={"ok": True})
    result = verifier._verify_typed_text(context)
    assert not result.verified
    assert result.evidence["unverified_target"] is True

    monkeypatch.setattr(verifier.uia, "available", lambda: False)
    context.params.update(window_id=44, verify_in_window="Untitled - Notepad")
    result = verifier._verify_typed_text(context)
    assert not result.verified
    assert result.evidence["uia_available"] is False
