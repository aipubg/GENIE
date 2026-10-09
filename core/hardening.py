"""Safe mode, offline mode and crash recovery (core/hardening.py) — Phase 14.2.

Everything here exists so a bad day is survivable:

* **safe mode** — boot with the dangerous capabilities switched off, so an owner can still talk to
  GENIE (and read memory/missions) even if the automation layer is misbehaving.
* **offline mode** — when nothing remote is reachable, say so and route to local providers instead
  of failing every request.
* **crash recovery** — detect an unclean shutdown, purge stale locks/leases, and surface missions
  that were interrupted so they can be resumed rather than silently lost.

None of this silently discards work: recovery *reports* what it found.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.hardening")


# ------------------------------------------------------------------- safe mode
#: Capabilities that touch the owner's machine, the network, or third-party code. Safe mode
#: disables exactly these; chat, memory, missions and local inference keep working.
#:
#: NOTE — these are **capability ids**, which use dots (``browser.navigate``). They must not be
#: confused with PTE *scopes*, which use colons (``browser:navigate``). Matching on the scope
#: form silently matches nothing, which would make safe mode a no-op that merely looks defensive.
SAFE_MODE_DENY_PREFIXES = (
    "input.",                                   # typing, clicking, mouse
    "process.kill",
    "application.open", "application.close",
    "system.volume",                            # mutating system state
    "files.append", "files.copy", "files.delete",
    "files.mkdir", "files.move", "files.write",
    "clipboard.set",
    "shell.",                                   # shell.run / shell.powershell_json
    "browser.",                                 # the whole browser surface
    "plugin.", "devices.",
)


class SafeMode:
    """A degraded-but-usable GENIE."""

    def __init__(self, enabled: bool = False,
                 deny: Iterable[str] = SAFE_MODE_DENY_PREFIXES):
        self.enabled = bool(enabled)
        self.deny = tuple(deny)

    def is_allowed(self, capability: str) -> bool:
        if not self.enabled:
            return True
        return not self._denied_by(capability)

    def _denied_by(self, capability: str) -> Optional[str]:
        for prefix in self.deny:
            if capability.startswith(prefix):
                return prefix
        return None

    def explain(self, capability: str) -> str:
        if self.is_allowed(capability):
            return "allowed"
        return f"blocked in safe mode (matches '{self._denied_by(capability)}')"

    def filter_capabilities(self, capabilities: Iterable[str]) -> List[str]:
        return [c for c in capabilities if self.is_allowed(c)]

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "deny_prefixes": list(self.deny)}


_SAFE_MODE: Optional["SafeMode"] = None


def set_safe_mode(enabled: bool) -> SafeMode:
    """Turn safe mode on/off process-wide (used at boot via `--safe`)."""
    global _SAFE_MODE
    _SAFE_MODE = SafeMode(enabled=bool(enabled))
    return _SAFE_MODE


def get_safe_mode() -> SafeMode:
    """The active safe-mode policy. Defaults to off, so behaviour is unchanged unless asked."""
    global _SAFE_MODE
    if _SAFE_MODE is None:
        _SAFE_MODE = SafeMode(enabled=False)
    return _SAFE_MODE


# ---------------------------------------------------------------- offline mode
def network_available(host: str = "1.1.1.1", port: int = 443,
                      timeout: float = 1.5) -> bool:
    """Cheap reachability probe. Offline must never be guessed from a failed request."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class OfflineMode:
    """What can GENIE still do when the network is gone?"""

    def __init__(self, registry=None, probe: Optional[Callable[[], bool]] = None):
        self.registry = registry
        self.probe = probe or (lambda: network_available())

    def _providers(self) -> List[Dict[str, Any]]:
        if self.registry is None:
            return []
        try:
            return list(self.registry.providers() or [])
        except Exception as exc:
            log.debug("offline provider lookup failed: %s", exc)
            return []

    @staticmethod
    def _is_local(provider: Dict[str, Any]) -> bool:
        from core.controlcenter import ControlCenter
        return ControlCenter._is_local(provider)

    def status(self) -> Dict[str, Any]:
        reachable = bool(self.probe())
        providers = self._providers()
        local = [p for p in providers if self._is_local(p) and p.get("enabled", True)]
        remote = [p for p in providers if not self._is_local(p) and p.get("enabled", True)]
        usable = (local + remote) if reachable else local
        return {"offline": not reachable, "network_reachable": reachable,
                "local_providers": [p.get("id") or p.get("provider_id") or "" for p in local],
                "remote_providers": [p.get("id") or p.get("provider_id") or "" for p in remote],
                "usable_providers": [p.get("id") or p.get("provider_id") or "" for p in usable],
                "degraded": (not reachable) and not local,
                "note": ("no network and no local provider — GENIE can only answer from memory"
                         if (not reachable) and not local else
                         "offline: routing to local providers only" if not reachable else "")}


#: Offline probing is a network round-trip, so it is cached. Without this every request would
#: pay a TCP timeout before discovering the network is gone.
_OFFLINE_CACHE: Dict[str, Any] = {"checked_at": 0.0, "status": None}
_OFFLINE_TTL_S = 30.0


def get_offline_status(registry=None, *, ttl_s: float = _OFFLINE_TTL_S,
                       probe: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Cached offline status. Safe to call on a hot path."""
    now = time.time()
    cached = _OFFLINE_CACHE.get("status")
    if cached is not None and (now - float(_OFFLINE_CACHE.get("checked_at", 0.0))) < ttl_s:
        return cached
    status = OfflineMode(registry=registry, probe=probe).status()
    _OFFLINE_CACHE["checked_at"] = now
    _OFFLINE_CACHE["status"] = status
    return status


def reset_offline_cache() -> None:
    """Forget the cached probe result (tests, or after a network change)."""
    _OFFLINE_CACHE["checked_at"] = 0.0
    _OFFLINE_CACHE["status"] = None


# -------------------------------------------------------------- crash recovery
class CrashRecovery:
    """Detect an unclean shutdown and clean up after it."""

    def __init__(self, flag_path: Path | str):
        self.flag_path = Path(flag_path)

    def mark_start(self) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.write_text(json.dumps(
            {"pid": os.getpid(), "started_ms": int(time.time() * 1000)}), encoding="utf-8")

    def mark_clean(self) -> None:
        try:
            self.flag_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("could not clear shutdown flag: %s", exc)

    def was_unclean(self) -> bool:
        return self.flag_path.exists()

    def recover(self, *, locks=None, missions=None, resume: bool = False,
                ctx=None) -> Dict[str, Any]:
        """Clean up after a crash. Reports everything it found; never hides it."""
        unclean = self.was_unclean()

        purged = 0
        if locks is not None:
            try:
                purged = int(locks.purge_stale() or 0)
            except Exception as exc:
                log.debug("lock purge failed: %s", exc)

        interrupted: List[Dict[str, Any]] = []
        resumed: List[str] = []
        if missions is not None:
            try:
                for mission in missions.interrupted() or []:
                    mid = getattr(mission, "mission_id", None) or (
                        mission.get("mission_id") if isinstance(mission, dict) else "")
                    interrupted.append(mid)
            except Exception as exc:
                log.debug("interrupted mission lookup failed: %s", exc)
            if resume and ctx is not None:
                for mid in interrupted:
                    try:
                        missions.resume(ctx, mid)
                        resumed.append(mid)
                    except Exception as exc:
                        log.debug("resume failed for %s: %s", mid, exc)

        self.mark_clean()
        report = {"unclean_shutdown": unclean, "locks_purged": purged,
                  "interrupted_missions": interrupted, "resumed": resumed,
                  "recovered_ms": int(time.time() * 1000)}
        if unclean:
            log.warning("recovered from an unclean shutdown: %s", report)
        return report
