"""Phase 2 exit-gate tests — REAL, non-dry-run actions on the Windows machine.

These are the tests that prove GENIE has real hands: an action only passes if the resulting
machine state was observed and verified. They are skipped on non-Windows hosts.

Safety: they only touch Notepad, the GENIE workspace, the clipboard, the master volume
(restored afterwards) and a dedicated browser profile.
"""
from __future__ import annotations

import sys
import time

import pytest

# Category: real_machine — opens Notepad, types, moves the foreground window, uses the clipboard and the system volume
# Runs serially behind the shared REAL_DESKTOP_TEST lock (see tests/conftest.py).

from core.contracts import CallContext, Persona

WINDOWS = sys.platform.startswith("win")
pytestmark = [pytest.mark.real_machine,
              pytest.mark.skipif(not WINDOWS, reason="real Windows actions required")]


@pytest.fixture()
def ctx():
    return CallContext(person_id="owner", persona=Persona.OWNER)


def exec_(app, ctx, capability, **params):
    return app.computer.execute(ctx, capability, params)


# ------------------------------------------------------------------ app control
def test_open_notepad_is_really_verified(app, ctx):
    result = exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    try:
        assert result.ok is True, result.detail
        assert result.verified is True
        assert "notepad" in result.detail.lower()
        assert (result.data or {}).get("strategy_used") == "native-launch"
        # the window must really exist in the OS window list
        windows = exec_(app, ctx, "window.list")
        assert any("notepad" in (w.get("process") or "").lower()
                   for w in windows.data["windows"])
    finally:
        exec_(app, ctx, "application.close", target="notepad")


def test_open_uninstalled_app_fails_honestly(app, ctx):
    result = exec_(app, ctx, "application.open", target="definitely-not-installed-xyzzy")
    assert result.ok is False
    assert "not installed" in result.detail


def test_close_app_is_verified(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    time.sleep(0.8)
    result = exec_(app, ctx, "application.close", target="notepad")
    assert result.ok is True and result.verified is True
    assert "no longer running" in result.detail


# --------------------------------------------------------------- window control
def test_window_minimize_and_restore_verified(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    try:
        time.sleep(0.8)
        mini = exec_(app, ctx, "window.minimize", process="notepad.exe")
        assert mini.ok and mini.verified, mini.detail
        restore = exec_(app, ctx, "window.restore", process="notepad.exe")
        assert restore.ok and restore.verified, restore.detail
    finally:
        exec_(app, ctx, "application.close", target="notepad")


# ------------------------------------------------------------------------ audio
def test_volume_set_is_verified_and_restored(app, ctx):
    before = exec_(app, ctx, "system.audio.state")
    original = (before.data or {}).get("volume")
    assert original is not None, "Core Audio must be readable for verification"
    try:
        result = exec_(app, ctx, "system.volume.set", level=27)
        assert result.ok is True and result.verified is True, result.detail
        assert (result.data or {}).get("actual") == 27
        check = exec_(app, ctx, "system.audio.state")
        assert (check.data or {}).get("volume") == 27
    finally:
        exec_(app, ctx, "system.volume.set", level=original)
    restored = exec_(app, ctx, "system.audio.state")
    assert (restored.data or {}).get("volume") == original


def test_volume_mute_toggles_and_restores(app, ctx):
    state = exec_(app, ctx, "system.audio.state")
    was_muted = bool((state.data or {}).get("muted"))
    try:
        result = exec_(app, ctx, "system.volume.mute", muted=not was_muted)
        assert result.ok and result.verified, result.detail
        assert bool((result.data or {}).get("actual")) is (not was_muted)
        assert (result.data or {}).get("backend") == "core_audio"
    finally:
        exec_(app, ctx, "system.volume.mute", muted=was_muted)


# --------------------------------------------------------------------- clipboard
def test_clipboard_set_is_verified(app, ctx):
    original = exec_(app, ctx, "clipboard.get").data.get("text", "")
    try:
        result = exec_(app, ctx, "clipboard.set", text="genie-phase2-clipboard")
        assert result.ok and result.verified, result.detail
        read = exec_(app, ctx, "clipboard.get")
        assert read.data["text"] == "genie-phase2-clipboard"
    finally:
        exec_(app, ctx, "clipboard.set", text=original)


# ------------------------------------------------------------------------- shell
def test_powershell_captures_result(app, ctx):
    result = exec_(app, ctx, "shell.run", command="Get-Process -Name explorer | Select-Object -First 1 | ForEach-Object { $_.ProcessName }")
    assert result.ok is True and result.verified is True, result.detail
    assert "explorer" in (result.data.get("stdout") or "").lower()


def test_dangerous_command_still_refused_on_real_machine(app, ctx):
    result = exec_(app, ctx, "shell.run", command="Remove-Item -Recurse -Force C:\\",
                   tier="elevated")
    assert result.ok is False
    assert "refused" in result.detail.lower()


# ------------------------------------------------------------------------ files
def test_workspace_file_lifecycle_is_verified(app, ctx):
    write = exec_(app, ctx, "files.write", path="phase2/note.txt", text="hello phase 2")
    assert write.ok and write.verified
    read = exec_(app, ctx, "files.read", path="phase2/note.txt")
    assert read.data["text"] == "hello phase 2"
    mkdir = exec_(app, ctx, "files.mkdir", path="phase2/sub")
    assert mkdir.ok and mkdir.verified
    copy = exec_(app, ctx, "files.copy", src="phase2/note.txt", dst="phase2/sub/note.txt")
    assert copy.ok and copy.verified
    move = exec_(app, ctx, "files.move", src="phase2/sub/note.txt", dst="phase2/moved.txt")
    assert move.ok and move.verified, move.detail
    delete = exec_(app, ctx, "files.delete", path="phase2/moved.txt", confirmed=True)
    assert delete.ok and delete.verified, delete.detail
    assert exec_(app, ctx, "files.exists", path="phase2/moved.txt").data["exists"] is False


def test_destructive_file_action_needs_confirmation(app, ctx):
    exec_(app, ctx, "files.write", path="phase2/keep.txt", text="x")
    refused = exec_(app, ctx, "files.delete", path="phase2/keep.txt")
    assert refused.ok is False and "confirmation required" in refused.detail
    assert exec_(app, ctx, "files.exists", path="phase2/keep.txt").data["exists"] is True
    exec_(app, ctx, "files.delete", path="phase2/keep.txt", confirmed=True)


# -------------------------------------------------------------------- UIA + input
def test_type_text_into_notepad_and_read_it_back_with_uia(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    try:
        time.sleep(1.2)
        # target the exact window: several Notepad windows can exist at once
        windows = exec_(app, ctx, "window.list")
        notepads = [w for w in windows.data["windows"]
                    if "notepad" in (w.get("process") or "").lower()]
        assert notepads, "no Notepad window found"
        target = notepads[0]
        focused = exec_(app, ctx, "window.focus", hwnd=target["hwnd"])
        assert focused.ok and focused.verified, focused.detail
        time.sleep(0.4)

        text = "GENIE verified typing 123"
        typed = exec_(app, ctx, "input.type_text", text=text,
                      verify_in_window=target["title"])
        assert typed.ok is True, typed.detail

        found = exec_(app, ctx, "uia.find", window=target["title"],
                      control_type="Document", limit=3)
        assert found.ok and found.data["elements"], "Notepad document element not found"
        element_id = found.data["elements"][0]["element_id"]
        value = exec_(app, ctx, "uia.get_value", element_id=element_id)
        assert text in (value.data.get("value") or ""), value.data
    finally:
        exec_(app, ctx, "application.close", target="notepad")


def test_uia_set_value_is_verified(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    try:
        time.sleep(1.2)
        found = exec_(app, ctx, "uia.find", window="Notepad", control_type="Document", limit=2)
        if not found.ok or not found.data.get("elements"):
            pytest.skip("Notepad document element unavailable on this build")
        element_id = found.data["elements"][0]["element_id"]
        result = exec_(app, ctx, "uia.set_value", element_id=element_id, value="uia-set-value")
        assert result.ok is True, result.detail
        assert (result.data or {}).get("verified") is True
    finally:
        exec_(app, ctx, "application.close", target="notepad")


def test_semantic_click_on_a_real_control(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    try:
        time.sleep(1.2)
        found = exec_(app, ctx, "uia.find", window="Notepad", control_type="MenuBar", limit=5)
        if not found.ok or not found.data.get("elements"):
            pytest.skip("no MenuBar element on this Notepad build")
        element_id = found.data["elements"][0]["element_id"]
        focused = exec_(app, ctx, "uia.focus", element_id=element_id)
        assert focused.ok is True, focused.detail
    finally:
        exec_(app, ctx, "application.close", target="notepad")


# ------------------------------------------------------------- capture / state
def test_screen_capture_produces_a_real_file(app, ctx):
    result = exec_(app, ctx, "screen.capture")
    assert result.ok and result.verified, result.detail
    from pathlib import Path
    path = Path(result.data["path"])
    assert path.exists() and path.stat().st_size > 100_000


def test_computer_state_is_observable(app, ctx):
    state = exec_(app, ctx, "computer.state")
    assert state.ok
    data = state.data
    assert data["window_count"] >= 1
    assert data["monitors"]
    assert data["process_count"] > 5


# ------------------------------------------------------- cancellation and locks
def test_cancellation_releases_the_desktop_lock(app, ctx):
    import threading
    cancel = threading.Event()
    cancel.set()                      # cancel before the action starts
    result = app.computer.execute(ctx, "input.type_text", {"text": "should not be typed"},
                                 cancel_event=cancel)
    assert result.ok is False
    assert (result.data or {}).get("cancelled") is True
    assert app.computer.desktop_lock.state()["held"] is False


def test_stale_locks_are_recovered(app, ctx):
    app.locks.acquire("computer:desktop-input", "ghost-holder", ttl_ms=1)
    time.sleep(0.05)
    purged = app.locks.purge_stale()
    assert purged >= 1
    assert app.computer.desktop_lock.acquire("new-holder")["ok"] is True
    app.computer.desktop_lock.release("new-holder")


# ---------------------------------------------------------------- USER_TAKEOVER
def test_user_takeover_releases_desktop_control(app, ctx):
    """Simulate the human moving the mouse while GENIE owns desktop input."""
    from computer import windows_api as win
    lock = app.computer.desktop_lock
    assert lock.acquire("test-mission")["ok"] is True
    # raw Win32 input bypassing input_mod -> looks exactly like an external user action
    win.mouse_move(win.cursor_pos()[0] + 3, win.cursor_pos()[1] + 3)
    time.sleep(0.25)
    takeover = lock.check_takeover()
    assert takeover is not None, "takeover was not detected"
    assert takeover["released_holder"] == "test-mission"
    state = lock.state()
    assert state["held"] is False
    assert state["paused"] is True
    assert state["takeover_count"] >= 1
    lock.resume("test finished")


# ------------------------------------------------------------------- audit chain
def test_full_action_chain_is_audited(app, ctx):
    exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    exec_(app, ctx, "application.close", target="notepad")
    assert app.audit.verify() is True
    entries = app.audit.tail(50)
    actions = {e["action"] for e in entries}
    assert any(a.startswith("computer.application.open") for a in actions)
    assert any(a.startswith("trust.check:") for a in actions)
    # verification outcomes are persisted for the executor loop
    rows = app.db.query("SELECT capability, ok, strategy FROM action_verifications"
                        " ORDER BY id DESC LIMIT 20")
    assert rows
    assert all(r["strategy"] for r in rows)
