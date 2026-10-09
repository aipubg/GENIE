"""Phase 2 unit tests: discovery, planning, verification, workspace, shell safety, files."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from computer import apps, files, planner, shell, verifier, workspace, windows_api as win


# --------------------------------------------------------------------- discovery
def test_discovery_finds_installed_apps():
    entries = apps.discover(force=True)
    assert len(entries) > 5
    assert all(e.target for e in entries)


def test_resolve_known_system_apps():
    for name in ("notepad", "cmd"):
        entry = apps.resolve(name)
        assert entry is not None, name
        assert Path(entry.target).exists()


def test_resolve_unknown_app_returns_none():
    assert apps.resolve("definitely-not-installed-xyzzy") is None


def test_discovery_cache_is_used():
    apps.discover(force=True)
    first = apps.discover()
    second = apps.discover()
    assert first is second          # cached list object


# ----------------------------------------------------------------------- planner
def test_strategies_are_priority_ordered():
    strategies = planner.available_strategies("application.open")
    priorities = [s.priority for s in strategies]
    assert priorities == sorted(priorities)
    assert strategies[0].name == "native-launch"


def test_plan_marks_missing_application_unplannable():
    plan = planner.plan("application.open", {"target": "definitely-not-installed-xyzzy"})
    assert plan["verified"] is False
    assert plan["error_code"] == "application_not_discovered"
    assert "not found" in plan["reason"]


def test_plan_flags_lock_and_state_needs():
    assert planner.plan("input.type_text", {})["needs_desktop_lock"] is True
    assert planner.plan("application.open", {})["needs_full_state"] is True
    assert planner.plan("system.volume.set", {})["needs_desktop_lock"] is False


def test_raw_input_is_last_resort_for_open_app():
    names = [s.name for s in planner.strategies_for("application.open")]
    assert names.index("raw-search") > names.index("native-launch")


# --------------------------------------------------------------------- verifiers
def test_every_mutating_capability_has_a_verifier():
    """Contract: nothing may be reported COMPLETED without a verifier."""
    missing = [cap for cap in planner.CHAINS
               if not verifier.has_verifier(cap) and cap not in verifier.READ_ONLY]
    assert missing == [], f"capabilities without verification: {missing}"


def test_unknown_capability_is_never_verified():
    out = verifier.verify("application.explode", {}, {"ok": True}, {}, {})
    assert out.verified is False
    assert "no verifier" in out.detail


def test_volume_verifier_compares_actual_state():
    ok = verifier.verify("system.volume.set", {"level": 30}, {},
                         {"audio": {"volume": 60}}, {"audio": {"volume": 30}})
    assert ok.verified is True
    bad = verifier.verify("system.volume.set", {"level": 30}, {},
                          {"audio": {"volume": 60}}, {"audio": {"volume": 60}})
    assert bad.verified is False
    assert "expected 30" in bad.detail


def test_app_open_verifier_requires_process_and_visible_window():
    after = {"windows": [{"title": "Untitled - Notepad", "process": "notepad.exe",
                          "visible": True, "hwnd": 1}],
             "processes": [{"name": "notepad.exe", "pid": 10}]}
    ok = verifier.verify("application.open", {"target": "notepad"},
                         {"expected_process": "notepad.exe"}, {}, after)
    assert ok.verified is True

    hidden = {"windows": [{"title": "x", "process": "notepad.exe", "visible": False, "hwnd": 1}],
              "processes": [{"name": "notepad.exe", "pid": 10}]}
    bad = verifier.verify("application.open", {"target": "notepad"},
                          {"expected_process": "notepad.exe"}, {}, hidden)
    assert bad.verified is False
    assert "no visible window" in bad.detail


def test_file_move_verifier_requires_source_gone(tmp_path):
    src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
    src.write_text("x")
    dst.write_text("x")
    bad = verifier.verify("files.move", {}, {"src": str(src), "dst": str(dst)}, {}, {})
    assert bad.verified is False            # source still present
    src.unlink()
    ok = verifier.verify("files.move", {}, {"src": str(src), "dst": str(dst)}, {}, {})
    assert ok.verified is True


# --------------------------------------------------------------------- workspace
def test_workspace_blocks_path_escape(tmp_path):
    ws = workspace.Workspace(tmp_path / "ws")
    with pytest.raises(PermissionError):
        ws.path("..", "..", "escape.txt")
    assert ws.classify(ws.root / "coding" / "x.txt") == "workspace"
    assert ws.classify(tmp_path / "outside.txt") == "outside"


def test_workspace_creates_standard_subdirs(tmp_path):
    ws = workspace.Workspace(tmp_path / "ws2")
    for sub in workspace.WORKSPACE_SUBDIRS:
        assert (ws.root / sub).is_dir()


def test_isolation_detection_is_passive_and_safe():
    info = workspace.detect_isolation()
    assert "local-workspace" in info["options"]
    assert info["preferred"] in info["options"]
    assert "hyperv" in info["details"]


def test_resource_profile_scales():
    profile = workspace.resource_profile()
    assert profile["profile"] in ("low", "balanced", "full")
    assert 0 < profile["scale"] <= 1.0


# ------------------------------------------------------------------ shell safety
def test_destructive_commands_are_refused():
    for command in ("format C:", "diskpart", "Stop-Computer", "reg delete HKLM\\Software"):
        res = shell.run(command)
        assert res.refused is True, command
        assert res.ok is False


def test_powershell_parameter_is_not_a_false_positive():
    # '-Format' is a parameter, not a destructive verb (A-032)
    assert shell.is_forbidden("Get-Date -Format o") is None


def test_safe_allowlist_blocks_arbitrary_commands():
    assert shell.is_safe("Get-Process") is True
    assert shell.is_safe("Invoke-WebRequest http://x") is False
    res = shell.run("Invoke-WebRequest http://example.com", tier="safe")
    assert res.refused is True


def test_command_hash_is_stable():
    assert shell.command_hash("whoami") == shell.command_hash("whoami")


# ------------------------------------------------------------------------ files
def test_atomic_write_and_read(tmp_path):
    target = tmp_path / "deep" / "note.txt"
    out = files.write_text(target, "hello genie")
    assert out["ok"] and target.read_text(encoding="utf-8") == "hello genie"
    assert not list(target.parent.glob("*.genie-tmp"))     # temp file cleaned up
    assert files.read_text(target)["text"] == "hello genie"


def test_copy_verifies_hash(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"0" * 5000)
    dst = tmp_path / "dst.bin"
    out = files.copy(src, dst)
    assert out["ok"] and out["sha256_match"] is True


def test_find_returns_newest_first(tmp_path):
    old = tmp_path / "manual_old.pdf"
    new = tmp_path / "manual_new.pdf"
    old.write_text("a")
    new.write_text("b")
    import os
    os.utime(old, (1_600_000_000, 1_600_000_000))
    result = files.find(tmp_path, name_contains="manual", extensions=["pdf"])
    assert result["count"] == 2
    assert result["matches"][0]["name"] == "manual_new.pdf"


def test_delete_is_state_verified(tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("x")
    out = files.delete(target, to_recycle_bin=False)
    assert out["ok"] is True and not target.exists()
    assert files.delete(target)["ok"] is False      # already gone


# ------------------------------------------------------------------------ state
def test_system_info_reports_capabilities():
    info = __import__("computer.state", fromlist=["system_info"]).system_info()
    assert info["os"]
    assert info["cpu_count"] >= 1
    if win.available():
        assert "ram_total_gb" in info


# --------------------------------------------------------------------- state snapshot
def test_snapshot_survives_the_foreground_window_vanishing(monkeypatch):
    """Regression: the snapshot called `foreground_window()` twice.

    If the foreground window disappeared between the two calls (an app closed, focus moved),
    the second call returned None and the snapshot raised AttributeError — taking a real command
    down with it mid-action. The API must be consulted once.
    """
    from computer import state as computer_state

    calls = {"n": 0}
    fake_window = win.WindowInfo(hwnd=1, title="Fake", class_name="X", pid=1)

    def flaky():
        calls["n"] += 1
        return fake_window if calls["n"] == 1 else None

    # Isolate this probe from the shared windows_api module: DesktopAwareness may
    # poll that module on another thread during the full suite, but is not part
    # of the snapshot implementation being counted here.
    original_win = computer_state.win

    class SnapshotWin:
        def __getattr__(self, name):
            return getattr(original_win, name)

    snapshot_win = SnapshotWin()
    snapshot_win.foreground_window = flaky
    monkeypatch.setattr(computer_state, "win", snapshot_win)
    snapshot = computer_state.snapshot(include_processes=False)
    assert snapshot.foreground == fake_window.to_dict()
    assert calls["n"] == 1, "the foreground window must be read exactly once"


def test_snapshot_tolerates_no_foreground_window(monkeypatch):
    from computer import state as computer_state
    monkeypatch.setattr(computer_state.win, "foreground_window", lambda: None)
    assert computer_state.snapshot(include_processes=False).foreground is None


# --------------------------------------------------------------------- state snapshot
