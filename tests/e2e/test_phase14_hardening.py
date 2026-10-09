"""Phase 14.2 — safe mode, offline mode, crash recovery.

These exist so a bad day is survivable: the owner can still talk to GENIE with automation
switched off, the system routes locally when the network is gone, and a crash leaves no stale
locks or silently-lost missions.
"""
from __future__ import annotations

import pytest

from core.hardening import CrashRecovery, OfflineMode, SafeMode, network_available


# ------------------------------------------------------------------ safe mode
def test_safe_mode_off_allows_everything():
    safe = SafeMode(enabled=False)
    assert safe.is_allowed("shell.run")
    assert safe.is_allowed("browser.navigate")
    assert safe.filter_capabilities(["shell.run", "memory.read"]) == \
        ["shell.run", "memory.read"]


def test_safe_mode_blocks_machine_and_network_capabilities():
    """Real capability ids (dots), not PTE scopes (colons)."""
    safe = SafeMode(enabled=True)
    for cap in ("shell.run", "input.click", "input.type_text", "files.write",
                "files.delete", "application.open", "system.volume.set",
                "clipboard.set", "process.kill", "browser.navigate",
                "browser.click", "plugin.invoke", "devices.run"):
        assert safe.is_allowed(cap) is False, f"{cap} must be blocked in safe mode"


def test_safe_mode_keeps_the_owner_able_to_talk_to_genie():
    safe = SafeMode(enabled=True)
    for cap in ("memory.read", "memory.write", "missions.create", "chat.send",
                "agents.status", "providers.local", "files.read", "files.list",
                "clipboard.get", "workspace.list", "screen.capture"):
        assert safe.is_allowed(cap) is True, f"{cap} must stay available in safe mode"


def test_safe_mode_explains_why_something_is_blocked():
    safe = SafeMode(enabled=True)
    assert "safe mode" in safe.explain("browser.navigate")
    assert safe.explain("memory.read") == "allowed"


def test_safe_mode_reports_its_state():
    state = SafeMode(enabled=True).to_dict()
    assert state["enabled"] is True
    assert "browser." in state["deny_prefixes"]


# ---------------------------------------------------------------- offline mode
class _Registry:
    def __init__(self, providers):
        self._providers = providers

    def providers(self):
        return list(self._providers)


def test_online_all_providers_are_usable():
    reg = _Registry([{"id": "ollama", "name": "Ollama", "enabled": True},
                     {"id": "openai", "name": "OpenAI", "enabled": True}])
    status = OfflineMode(registry=reg, probe=lambda: True).status()
    assert status["offline"] is False
    assert set(status["usable_providers"]) == {"ollama", "openai"}


def test_offline_routes_to_local_providers_only():
    reg = _Registry([{"id": "ollama", "name": "Ollama", "enabled": True},
                     {"id": "openai", "name": "OpenAI", "enabled": True}])
    status = OfflineMode(registry=reg, probe=lambda: False).status()
    assert status["offline"] is True
    assert status["usable_providers"] == ["ollama"]
    assert "openai" in status["remote_providers"]
    assert status["degraded"] is False
    assert "local" in status["note"]


def test_offline_with_no_local_provider_is_degraded_but_honest():
    reg = _Registry([{"id": "openai", "name": "OpenAI", "enabled": True}])
    status = OfflineMode(registry=reg, probe=lambda: False).status()
    assert status["degraded"] is True
    assert status["usable_providers"] == []
    assert "memory" in status["note"]


def test_offline_without_a_registry_does_not_crash():
    status = OfflineMode(registry=None, probe=lambda: False).status()
    assert status["offline"] is True and status["usable_providers"] == []


def test_network_probe_returns_a_boolean():
    assert isinstance(network_available(timeout=0.5), bool)


# -------------------------------------------------------------- crash recovery
def test_clean_shutdown_leaves_no_flag(tmp_path):
    rec = CrashRecovery(tmp_path / "running.flag")
    assert rec.was_unclean() is False
    rec.mark_start()
    assert rec.was_unclean() is True
    rec.mark_clean()
    assert rec.was_unclean() is False


def test_recovery_detects_an_unclean_shutdown(tmp_path):
    rec = CrashRecovery(tmp_path / "running.flag")
    rec.mark_start()  # simulating a crash: flag never cleared
    report = rec.recover()
    assert report["unclean_shutdown"] is True
    assert rec.was_unclean() is False, "recovery must clear the flag"


def test_recovery_purges_stale_locks(tmp_path):
    class _Locks:
        def purge_stale(self):
            return 3

    rec = CrashRecovery(tmp_path / "running.flag")
    rec.mark_start()
    report = rec.recover(locks=_Locks())
    assert report["locks_purged"] == 3


def test_recovery_reports_interrupted_missions_without_losing_them(tmp_path):
    class _Mission:
        mission_id = "m-1"

    class _Missions:
        def interrupted(self):
            return [_Mission()]

    rec = CrashRecovery(tmp_path / "running.flag")
    rec.mark_start()
    report = rec.recover(missions=_Missions())
    assert report["interrupted_missions"] == ["m-1"]
    assert report["resumed"] == [], "must not auto-resume unless asked"


def test_recovery_can_resume_when_explicitly_asked(tmp_path):
    class _Mission:
        mission_id = "m-2"

    class _Missions:
        def interrupted(self):
            return [_Mission()]

        def resume(self, ctx, mission_id):
            assert mission_id == "m-2"
            return _Mission()

    rec = CrashRecovery(tmp_path / "running.flag")
    rec.mark_start()
    report = rec.recover(missions=_Missions(), resume=True, ctx=object())
    assert report["resumed"] == ["m-2"]


def test_recovery_survives_broken_services(tmp_path):
    class _Boom:
        def purge_stale(self):
            raise RuntimeError("locks db gone")

        def interrupted(self):
            raise RuntimeError("missions db gone")

    rec = CrashRecovery(tmp_path / "running.flag")
    rec.mark_start()
    report = rec.recover(locks=_Boom(), missions=_Boom())
    # cleanup failing must not stop GENIE from starting
    assert report["unclean_shutdown"] is True
    assert report["locks_purged"] == 0


# ------------------------------------------------------------------ wiring
def test_safe_mode_is_actually_enforced_by_the_capability_gate(app):
    """Wiring test — a safe mode nobody consults is just a flag."""
    from core.contracts import CallContext
    from core.hardening import set_safe_mode

    set_safe_mode(True)
    try:
        out = app.computer.execute(CallContext(), "browser.navigate",
                                   {"url": "https://example.com"})
        assert out.ok is False
        assert "safe mode" in (out.detail or ""), out.detail
    finally:
        set_safe_mode(False)


def test_with_safe_mode_off_the_gate_does_not_invoke_it(app):
    from core.contracts import CallContext
    from core.hardening import set_safe_mode

    set_safe_mode(False)
    out = app.computer.execute(CallContext(), "browser.navigate",
                               {"url": "https://example.com"})
    # it may still be denied by trust/permission — but not *because* of safe mode
    assert "safe mode" not in (out.detail or ""), out.detail


def test_safe_mode_does_not_mask_other_errors(app):
    from core.contracts import CallContext
    from core.hardening import set_safe_mode

    set_safe_mode(True)
    try:
        out = app.computer.execute(CallContext(), "definitely.not_a_capability")
        assert "unsupported capability" in (out.detail or ""), out.detail
    finally:
        set_safe_mode(False)


# ------------------------------------------------------ offline mode caching
def test_offline_probe_is_cached_so_requests_do_not_each_pay_a_timeout():
    from core.hardening import get_offline_status, reset_offline_cache

    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return False

    reset_offline_cache()
    reg = _Registry([{"id": "ollama", "name": "Ollama", "enabled": True}])
    first = get_offline_status(reg, probe=probe, ttl_s=60)
    second = get_offline_status(reg, probe=probe, ttl_s=60)
    assert first["offline"] is True and second["offline"] is True
    assert calls["n"] == 1, "the second lookup must reuse the cached probe"

    reset_offline_cache()
    get_offline_status(reg, probe=probe, ttl_s=60)
    assert calls["n"] == 2, "resetting the cache must force a fresh probe"


def test_offline_cache_expires():
    from core.hardening import get_offline_status, reset_offline_cache

    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True

    reset_offline_cache()
    get_offline_status(None, probe=probe, ttl_s=0)   # zero TTL = always re-probe
    get_offline_status(None, probe=probe, ttl_s=0)
    assert calls["n"] == 2


def test_gateway_candidate_selection_survives_the_offline_filter(app, monkeypatch):
    """Wiring test — the gateway consults offline status without breaking routing."""
    from core.contracts import ModelRequirement
    from core import hardening

    monkeypatch.setattr(hardening, "network_available", lambda *a, **k: False)
    hardening.reset_offline_cache()

    cands = app.gateway.candidates(ModelRequirement())
    assert isinstance(cands, list), "candidate selection must not raise when offline"

    hardening.reset_offline_cache()
