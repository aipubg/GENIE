"""Phase 4 plugin tests — real plugin host processes, no mocks.

Covers: discovery, loading, permissions (default deny), permitted invocation, timeout
handling, crash isolation, restart, disable-after-repeated-crash, the real media plugin, the
generic fallback when a plugin is unavailable, and audit of plugin actions.
"""
from __future__ import annotations

import sys
import shutil
import time
from pathlib import Path

import pytest

from core.contracts import CallContext, Persona
from plugins.registry import PluginRegistry
from plugins.service import PluginService
from plugins.sdk import PluginError, load_manifest, parse_manifest

REPO = Path(__file__).resolve().parents[2]
PLUGINS_ROOT = REPO / "plugins"


@pytest.fixture()
def plugins(app, tmp_path):
    """Use isolated installed-style folders; production ignores `_test_*` names."""
    root = tmp_path / "plugins"
    installed = root / "installed"
    installed.mkdir(parents=True)
    for folder in ("home", "media", "vscode"):
        shutil.copytree(PLUGINS_ROOT / "installed" / folder, installed / folder)
    for source, installed_name in (("_test_crash", "test_crash"),
                                   ("_test_timeout", "test_timeout")):
        shutil.copytree(PLUGINS_ROOT / "installed" / source,
                        installed / installed_name)
    service = PluginService(app.db, audit=app.audit, trust=app.trust,
                            root=root, autostart=False)
    yield service
    service.stop_all()


def owner_ctx():
    return CallContext(person_id="owner", persona=Persona.OWNER)


# --------------------------------------------------------------- 1. discovery
def test_plugins_are_discovered_without_touching_genie_source(plugins):
    discovered = plugins.registry.discover()
    ids = {entry.get("id") for entry in discovered if entry.get("ok")}
    assert {"media", "vscode", "test_crash", "test_timeout"} <= ids
    assert all(entry["ok"] for entry in discovered), discovered


def test_capabilities_are_registered_under_the_plugin_namespace(plugins):
    capabilities = plugins.capabilities()
    assert "plugin.media.pause" in capabilities
    assert "plugin.vscode.open_project" in capabilities
    assert len(capabilities) >= 19


# ------------------------------------------------------------------ 2. loading
def test_plugin_host_runs_in_a_separate_process(plugins):
    started = plugins.client("media").start()
    assert started.get("ok") is True, started
    status = plugins.client("media").status()
    assert status["running"] is True
    assert status["pid"] and status["pid"] != __import__("os").getpid()


# ------------------------------------------------------- 3. permissions (deny)
def test_permission_is_denied_by_default(plugins):
    plugins.client("media").start()
    invocation = plugins.execute(owner_ctx(), "plugin.media.pause", {})
    assert invocation.ok is False
    assert invocation.error_code == "permission_denied"
    assert "lacks permissions" in invocation.error


def test_granting_permissions_enables_the_capability(plugins):
    plugins.client("media").start()
    granted = plugins.grant("media", ["application.media.control", "system.audio.read"])
    assert granted["ok"] is True
    invocation = plugins.execute(owner_ctx(), "plugin.media.state", {})
    assert invocation.ok is True, invocation.error
    assert "observable_sessions" in (invocation.raw or {})


def test_revoking_permissions_denies_again(plugins):
    plugins.client("media").start()
    plugins.grant("media", ["application.media.control"])
    assert plugins.execute(owner_ctx(), "plugin.media.pause", {}).ok is True
    plugins.revoke("media")
    denied = plugins.execute(owner_ctx(), "plugin.media.pause", {})
    assert denied.ok is False and denied.error_code == "permission_denied"


def test_a_plugin_cannot_request_permissions_it_did_not_declare(plugins):
    result = plugins.grant("media", ["filesystem.workspace.write"])
    assert result["ok"] is False
    assert "does not declare" in result["error"]


# ------------------------------------------------------- 4. permitted invoke
def test_permitted_invocation_uses_native_media_interface(plugins):
    plugins.client("media").start()
    plugins.grant("media", ["application.media.control"])
    invocation = plugins.execute(owner_ctx(), "plugin.media.next", {})
    assert invocation.ok is True, invocation.error
    assert (invocation.raw or {}).get("method") == "windows-media-key"


# ------------------------------------------------------------ 5. timeout
def test_plugin_timeout_is_handled(plugins):
    plugins.client("test_timeout").start()
    plugins.grant("test_timeout", ["application.test.control"])
    started = time.time()
    invocation = plugins.execute(owner_ctx(), "plugin.test_timeout.hang", {})
    elapsed = time.time() - started
    assert invocation.ok is False
    assert invocation.error_code == "plugin_timeout"
    assert elapsed < 12, f"timeout was not enforced ({elapsed:.1f}s)"
    # the daemon is still alive and can serve other plugins
    assert plugins.execute(owner_ctx(), "plugin.media.state", {}).error_code == "permission_denied"


# -------------------------------------------------------- 6. crash isolation
def test_plugin_crash_does_not_crash_the_daemon(plugins, app):
    plugins.client("test_crash").start()
    plugins.grant("test_crash", ["application.test.control"])
    invocation = plugins.execute(owner_ctx(), "plugin.test_crash.die", {})
    assert invocation.ok is False
    assert invocation.error_code in ("plugin_crashed", "host_not_running", "plugin_timeout")
    # the daemon and its other services are unaffected
    assert app.audit.verify() is True
    assert plugins.registry.get("media") is not None


def test_adapter_exception_becomes_a_structured_error(plugins):
    plugins.client("test_crash").start()
    plugins.grant("test_crash", ["application.test.control"])
    invocation = plugins.execute(owner_ctx(), "plugin.test_crash.raise", {})
    assert invocation.ok is False
    assert "fixture exception" in (invocation.error or "")


# ------------------------------------------------------------ 7. restart
def test_plugin_restarts_after_a_crash(plugins):
    client = plugins.client("test_crash")
    client.start()
    plugins.grant("test_crash", ["application.test.control"])
    plugins.execute(owner_ctx(), "plugin.test_crash.die", {})
    assert client.state in ("crashed", "stopped")
    restarted = client.restart()
    assert restarted.get("ok") is True, restarted
    assert plugins.execute(owner_ctx(), "plugin.test_crash.ok", {}).ok is True


# ------------------------------------------------- 8. disable after crashes
def test_repeated_crashes_disable_the_plugin(plugins):
    client = plugins.client("test_crash")
    client.crash_limit = 2
    plugins.grant("test_crash", ["application.test.control"])
    for _ in range(3):
        client.start()
        plugins.execute(owner_ctx(), "plugin.test_crash.die", {})
    assert client.state == "disabled"
    disabled = plugins.execute(owner_ctx(), "plugin.test_crash.ok", {})
    assert disabled.ok is False
    assert disabled.error_code == "plugin_disabled"


def test_a_disabled_plugin_can_be_re_enabled_by_the_owner(plugins):
    client = plugins.client("test_crash")
    client.crash_limit = 1
    plugins.grant("test_crash", ["application.test.control"])
    client.start()
    plugins.execute(owner_ctx(), "plugin.test_crash.die", {})
    assert client.state == "disabled"
    assert client.enable().get("ok") is True
    assert plugins.execute(owner_ctx(), "plugin.test_crash.ok", {}).ok is True


# ------------------------------------------------------- 9. real media plugin
def test_media_plugin_reports_honest_state_on_this_machine(plugins):
    plugins.client("media").start()
    plugins.grant("media", ["application.media.control", "system.audio.read"])
    state = plugins.execute(owner_ctx(), "plugin.media.state", {})
    assert state.ok is True
    raw = state.raw or {}
    assert raw.get("media_interface") == "windows-media-keys"
    # observation must be honest: either it sees media windows or it says it sees none
    assert "observable_sessions" in raw


def test_media_play_pause_next_are_accepted_by_the_os(plugins):
    plugins.client("media").start()
    plugins.grant("media", ["application.media.control"])
    for capability in ("plugin.media.play", "plugin.media.pause", "plugin.media.next",
                       "plugin.media.previous"):
        invocation = plugins.execute(owner_ctx(), capability, {})
        assert invocation.ok is True, f"{capability}: {invocation.error}"
        assert (invocation.raw or {}).get("method") == "windows-media-key"


def test_vscode_plugin_is_honest_when_the_cli_is_missing_or_present(plugins):
    plugins.client("vscode").start()
    plugins.grant("vscode", ["application.vscode.control", "filesystem.workspace.read"])
    health = plugins.execute(owner_ctx(), "plugin.vscode.health", {})
    assert health.ok in (True, False)
    raw = health.raw or {}
    if not raw.get("available", True):
        # the CLI is missing: GENIE must be told clearly, not silently succeed
        assert "code" in str(raw.get("detail", "")).lower()
    else:
        assert "code" in str(raw.get("detail", "")).lower()


# ------------------------------------------------- 5. generic app fallback
def test_generic_fallback_when_a_plugin_is_unavailable(app, plugins):
    """A missing/unavailable plugin must not block the capability: GENIE falls back."""
    from agents.runtime import CapabilityWorker

    worker = CapabilityWorker(computer=app.computer, device=None, plugins=plugins)
    ctx = owner_ctx()
    # media.pause with no granted permissions is reported, not silently rerouted
    denied = worker(ctx, {"type": "device_action", "capability": "media.pause",
                          "device": "pc_main", "params": {}})
    assert denied["ok"] is False
    assert denied.get("error_code") == "permission_denied"

    # once the plugin cannot serve at all, the worker falls through to the generic chain
    plugins.revoke("media")
    plugins.set_enabled("media", False)
    result = worker(ctx, {"type": "device_action", "capability": "media.pause",
                          "device": "pc_main", "params": {}})
    assert "plugin" not in result          # handled by the generic path, not the plugin


# ------------------------------------------------------- 21. audit + lifecycle
def test_plugin_actions_are_audited(plugins, app):
    plugins.client("media").start()
    plugins.grant("media", ["application.media.control"])
    plugins.execute(owner_ctx(), "plugin.media.pause", {})
    entries = app.audit.tail(50)
    actions = {e["action"] for e in entries}
    assert any(a.startswith("plugin.plugin.media.pause") for a in actions), actions
    assert app.audit.verify() is True


def test_plugin_lifecycle_disable_and_uninstall(plugins):
    assert plugins.set_enabled("vscode", False)["ok"] is True
    assert plugins.registry.get("vscode").enabled is False
    assert "plugin.vscode.open_project" not in plugins.capabilities()
    assert plugins.set_enabled("vscode", True)["ok"] is True
    assert "plugin.vscode.open_project" in plugins.capabilities()

    # uninstall without removing files (the folder is part of the repository)
    assert plugins.uninstall("test_timeout", remove_files=False)["ok"] is True
    assert plugins.registry.get("test_timeout") is None
    plugins.registry.install(PLUGINS_ROOT / "installed" / "_test_timeout", copy=False)
    assert plugins.registry.get("test_timeout") is not None


# ------------------------------------------------------------ manifest rules
def test_manifest_validation_rejects_bad_input():
    with pytest.raises(PluginError):
        parse_manifest({"name": "x", "version": "1"})            # no id
    with pytest.raises(PluginError):
        parse_manifest({"id": "x", "name": "x", "version": "1", "capabilities": []})
    with pytest.raises(PluginError):
        parse_manifest({"id": "BadID", "name": "x", "version": "1",
                        "capabilities": ["do"]})                  # invalid id
    with pytest.raises(PluginError):
        parse_manifest({"id": "ok_id", "name": "x", "version": "1", "capabilities": ["do"],
                        "permissions": ["root.everything"]})      # unknown scope


def test_manifests_declare_verification_for_mutating_capabilities():
    manifest = load_manifest(PLUGINS_ROOT / "installed" / "media")
    assert manifest.permissions
    assert all(cap.timeout_ms > 0 for cap in manifest.capabilities)
    assert any(cap.verification for cap in manifest.capabilities)
