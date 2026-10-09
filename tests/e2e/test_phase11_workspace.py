"""Phase 11C — GENIE Workspace / background computer.

The workspace is GENIE's own computer: an isolated tree with its own browser profile, sessions,
downloads, shell and per-mission ownership. Agents do autonomous browsing/research/building here
instead of hijacking the owner's foreground desktop.

These are real tests: real directories are created, real files are written, a real subprocess runs,
and path escapes are really attempted and really refused. Nothing is a stub.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from computer import workspace as wsmod
from computer.workspace import DEFAULT_QUOTA_BYTES, Workspace


@pytest.fixture()
def ws(tmp_path) -> Workspace:
    """A fresh GENIE workspace (never the user's desktop)."""
    saved = wsmod._WORKSPACE
    wsmod._WORKSPACE = None
    yield Workspace(tmp_path / "genie-workspace")
    wsmod._WORKSPACE = saved


# ------------------------------------------------------------------ identity
def test_workspace_has_its_own_identity_isolated_from_the_user_desktop(ws):
    ident = ws.ensure_identity()
    assert ident["kind"] == "genie-workspace"
    assert ident["workspace_id"]
    assert ident["created_ms"]
    # the workspace is its own root — never the owner's home/desktop
    home = Path(os.path.expanduser("~")).resolve()
    assert ws.is_inside(home) is False, "workspace must not contain the user's home"
    assert ident["isolation"] in ("local-workspace", "hyperv", "wsl2", "windows-sandbox")
    # identity is stable across calls
    assert ws.identity()["workspace_id"] == ident["workspace_id"]


# ------------------------------------------------------------ mission ownership
def test_mission_claim_creates_scoped_tree_and_release_purges(ws):
    claim = ws.claim_mission("m-11c", agent_id="agt_1", purpose="background research")
    assert claim["ok"] is True
    base = Path(claim["path"])
    # every mission gets its own downloads/artifacts/temp — concurrent missions never collide
    for leaf in ("downloads", "artifacts", "temp"):
        assert (base / leaf).is_dir(), f"missing {leaf}"
    assert [m["mission_id"] for m in ws.active_missions()] == ["m-11c"]
    assert ws.active_missions()[0]["agent_id"] == "agt_1"

    # a second mission is fully separate
    ws.claim_mission("m-other", agent_id="agt_2")
    assert ws.mission_dir("m-11c") != ws.mission_dir("m-other")
    assert len(ws.active_missions()) == 2

    released = ws.release_mission("m-11c", purge=True)
    assert released["ok"] and released["purged"] is True
    assert [m["mission_id"] for m in ws.active_missions()] == ["m-other"]


def test_downloads_are_mission_scoped(ws):
    ws.claim_mission("m-dl")
    assert ws.downloads_dir("m-dl") == ws.mission_dir("m-dl") / "downloads"
    assert ws.downloads_dir() == ws.root / "downloads"


# ------------------------------------------------------------- path enforcement
def test_path_escape_is_refused(ws):
    with pytest.raises(PermissionError):
        ws.path("..", "escape.txt")
    with pytest.raises(PermissionError):
        ws.path("../../..", "escape.txt")
    # inside paths are fine
    assert ws.is_inside(ws.path("research", "notes.md"))


def test_shell_refuses_to_escape_but_runs_inside(ws):
    ws.claim_mission("m-shell")
    # a genuine escape (above the workspace root) is refused
    bad = ws.shell("echo pwned", cwd="../../../../../..", mission_id="m-shell")
    assert bad["ok"] is False and "escapes" in bad["error"]
    # an absolute path outside the workspace is refused too
    outside = ws.shell("echo pwned", cwd=str(Path(os.path.expanduser("~"))),
                       mission_id="m-shell")
    assert outside["ok"] is False
    # legitimate work inside the workspace really executes
    ok = ws.shell("echo genie-ws-ok", mission_id="m-shell")
    assert ok["ok"] is True and "genie-ws-ok" in ok["stdout"]
    assert ws.is_inside(ok["cwd"])
    # and it is rooted at the mission directory
    assert Path(ok["cwd"]).name == "m-shell"


def test_shell_writes_a_real_file_inside_the_workspace(ws):
    ws.claim_mission("m-write")
    r = ws.shell("echo hello > out.txt", mission_id="m-write")
    assert r["ok"] is True
    produced = ws.mission_dir("m-write") / "out.txt"
    assert produced.exists() and "hello" in produced.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- quota
def test_quota_is_enforced(ws):
    assert ws.quota()["cap_bytes"] == DEFAULT_QUOTA_BYTES
    ws.set_quota(1024)  # 1 KiB ceiling
    q = ws.quota()
    assert q["cap_bytes"] == 1024
    # a write larger than what is left is refused rather than silently filling the disk
    big = ws.check_quota(need_bytes=999_999)
    assert big["ok"] is False and "quota" in big["error"]
    # a small request still passes
    assert ws.check_quota(need_bytes=10)["ok"] is True


# -------------------------------------------------------------------- cleanup
def test_cleanup_is_dry_run_by_default_then_really_removes(ws):
    ws.claim_mission("m-clean")
    stale = ws.mission_dir("m-clean") / "temp" / "old.txt"
    stale.write_text("x" * 256, encoding="utf-8")
    os.utime(stale, (time.time() - 20 * 86400, time.time() - 20 * 86400))
    fresh = ws.mission_dir("m-clean") / "temp" / "new.txt"
    fresh.write_text("keep me", encoding="utf-8")

    dry = ws.cleanup(max_age_days=7, mission_id="m-clean")
    assert dry["dry_run"] is True
    assert dry["removed"] == 1 and stale.exists(), "dry run must not delete"

    real = ws.cleanup(max_age_days=7, mission_id="m-clean", dry_run=False)
    assert real["removed"] == 1
    assert not stale.exists(), "stale file should be gone"
    assert fresh.exists(), "fresh file must survive"


# ------------------------------------------------- sessions & browser profile
def test_browser_profile_is_dedicated_to_genie_not_the_user_chrome(ws):
    profile = ws.browser_profile_dir("default")
    assert ws.is_inside(profile)
    # it is inside the workspace session tree — never the user's real Chrome user-data-dir
    # (test tmp dirs legitimately live under AppData\Local\Temp, so compare the Chrome dir itself)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        chrome_default = Path(local) / "Google" / "Chrome" / "User Data"
        assert not str(profile.resolve()).startswith(str(chrome_default)), \
            "must not reuse the user's Chrome profile"
    # sessions persist per name
    assert ws.session_dir("research") != ws.session_dir("default")
    assert ws.session_dir("default") == ws.session_dir("default")


# ------------------------------------------------------- executor integration
def test_workspace_capabilities_are_reachable_through_the_executor(tmp_path):
    """The workspace is exposed through the Computer contracts, not a parallel system."""
    from core.contracts import CallContext
    from computer.executor import Executor

    saved = wsmod._WORKSPACE
    wsmod._WORKSPACE = None
    try:
        ex = Executor(workspace_root=str(tmp_path / "exec-ws"))
        assert Path(ex.workspace.root).name == "exec-ws"
        ctx = CallContext()

        ident = ex.execute(ctx, "workspace.identity").to_dict()
        assert ident["ok"] and ident["result"]["kind"] == "genie-workspace"

        claimed = ex.execute(ctx, "workspace.mission_claim",
                             {"mission_id": "m-exec", "agent_id": "agt_x"}).to_dict()
        assert claimed["ok"] and claimed["result"]["ok"]

        listed = ex.execute(ctx, "workspace.missions").to_dict()
        assert [m["mission_id"] for m in listed["result"]["missions"]] == ["m-exec"]

        quota = ex.execute(ctx, "workspace.quota").to_dict()
        assert quota["ok"] and "cap_bytes" in quota["result"]

        shell = ex.execute(ctx, "workspace.shell",
                           {"command": "echo via-executor", "mission_id": "m-exec"}).to_dict()
        assert shell["ok"] and "via-executor" in shell["result"]["stdout"]

        released = ex.execute(ctx, "workspace.mission_release",
                              {"mission_id": "m-exec", "purge": True}).to_dict()
        assert released["ok"] and released["result"]["purged"] is True
    finally:
        wsmod._WORKSPACE = saved
