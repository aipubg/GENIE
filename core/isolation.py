"""Isolation model and declared sensor sources (roadmap §19-§20).

GENIE can act on the owner's real desktop, inside a workspace directory, inside a
dedicated browser profile, inside a container, inside a VM, or on an isolated
desktop session. Those are different promises with very different consequences,
and "the agent is sandboxed" means nothing until you can say *which* one is in
effect.

This module is the single place that answers that question. It reports what it
can actually establish and says `unknown` for everything else — in particular:

* **Container and VM detection are heuristics.** A negative result means "not
  detected", never "definitely bare metal". Both return their evidence so the
  answer can be argued with.
* **An isolated desktop cannot be detected.** It is a property of how GENIE was
  launched, so it must be declared explicitly (`declare_isolated_desktop`) and
  is `unknown` until then. Guessing "isolated" here would be the single most
  dangerous lie in the whole module.
* **A sensor is not "the camera" until one is declared.** Devices are listed,
  but `declared_camera` / `declared_microphone` stay None until something
  chooses. Enumerating thirteen microphones is not the same as saying which one
  is listening.

Everything here is read-only and safe to call at any time.
"""
from __future__ import annotations

import enum
import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("core.isolation")

# Declared by whoever launches an isolated desktop session. There is no reliable
# way to infer it, so it stays unknown until stated.
_ENV_ISOLATED_DESKTOP = "GENIE_ISOLATED_DESKTOP"


class IsolationKind(str, enum.Enum):
    """The confinement promises GENIE can honestly claim."""

    HOST = "host"                          # directly on the owner's desktop
    WORKSPACE_DIR = "workspace_dir"        # file writes confined to a workspace root
    BROWSER_PROFILE = "browser_profile"    # browser state in a dedicated profile
    CONTAINER = "container"
    VM = "vm"
    ISOLATED_DESKTOP = "isolated_desktop"  # separate desktop/session, declared only
    UNKNOWN = "unknown"


#: Weakest to strongest. "Effective" is the strongest level actually in effect.
_STRENGTH = {
    IsolationKind.UNKNOWN: 0,
    IsolationKind.HOST: 1,
    IsolationKind.WORKSPACE_DIR: 2,
    IsolationKind.BROWSER_PROFILE: 3,
    IsolationKind.CONTAINER: 4,
    IsolationKind.VM: 5,
    IsolationKind.ISOLATED_DESKTOP: 6,
}


def _evidence(isolated: Optional[bool], why: List[str]) -> Dict[str, Any]:
    """Shape every probe the same way: never a bare yes/no without its reasons."""
    return {"detected": isolated, "evidence": list(why),
            "state": "detected" if isolated is True
                     else ("not_detected" if isolated is False else "unknown")}


# ------------------------------------------------------------------ container
def detect_container() -> Dict[str, Any]:
    """Best-effort container detection. A negative is 'not detected', not proof."""
    why: List[str] = []
    try:
        if os.path.exists("/.dockerenv"):
            why.append("/.dockerenv present")
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text(encoding="utf-8", errors="ignore")
            for marker in ("docker", "kubepods", "containerd", "lxc"):
                if marker in text:
                    why.append(f"/proc/1/cgroup mentions {marker}")
                    break
        if os.environ.get("container"):
            why.append(f"env container={os.environ['container'][:40]}")
        osrelease = Path("/proc/sys/kernel/osrelease")
        if osrelease.exists():
            rel = osrelease.read_text(encoding="utf-8", errors="ignore").lower()
            if "microsoft" in rel or "wsl" in rel:
                why.append("WSL kernel reported")
    except (OSError, ValueError) as exc:  # noqa: BLE001 - probing must never raise
        log.debug("container probe failed: %s", exc)
        return _evidence(None, [f"probe failed: {exc}"])
    return _evidence(bool(why), why)


# ------------------------------------------------------------------------ vm
_VM_MARKERS = ("vbox", "virtualbox", "vmware", "qemu", "kvm", "xen",
               "hyper-v", "microsoft corporation", "parallels", "innotek")


def _registry_strings() -> List[str]:
    """Read the Windows hardware description, or nothing at all."""
    if platform.system() != "Windows":
        return []
    try:
        import winreg  # noqa: PLC0415 - Windows-only, imported deliberately
    except ImportError:
        return []
    out: List[str] = []
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\Description\System"),
        (winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\Description\System\BIOS"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters"),
    ]
    for hive, sub in paths:
        try:
            with winreg.OpenKey(hive, sub) as key:
                index = 0
                while True:
                    try:
                        _name, value, _type = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    if isinstance(value, str):
                        out.append(value)
                    index += 1
                    if index > 64:
                        break
        except OSError:
            continue
    return out


def detect_vm() -> Dict[str, Any]:
    """Best-effort VM detection from the platform's own hardware description."""
    why: List[str] = []
    try:
        for raw in _registry_strings():
            low = raw.lower()
            hit = next((m for m in _VM_MARKERS if m in low), None)
            if hit:
                why.append(f"hardware description mentions '{hit}'")
        if platform.system() != "Windows" and platform.machine() and not why:
            # Non-Windows with no registry path: no evidence either way.
            return _evidence(None, ["no VM probe available on this platform"])
    except Exception as exc:  # noqa: BLE001 - a probe must never break a status call
        log.debug("vm probe failed: %s", exc)
        return _evidence(None, [f"probe failed: {exc}"])
    return _evidence(bool(why), why)


# --------------------------------------------------------------- declared state
def declare_isolated_desktop(enabled: bool = True) -> None:
    """State that GENIE is running on an isolated desktop. Only the launcher knows."""
    os.environ[_ENV_ISOLATED_DESKTOP] = "1" if enabled else "0"


def detect_isolated_desktop() -> Dict[str, Any]:
    """Never inferred. Unknown unless explicitly declared."""
    raw = os.environ.get(_ENV_ISOLATED_DESKTOP, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return _evidence(True, [f"{_ENV_ISOLATED_DESKTOP}={raw}"])
    if raw in ("0", "false", "no", "off"):
        return _evidence(False, [f"{_ENV_ISOLATED_DESKTOP}={raw}"])
    return _evidence(None, ["not declared; an isolated desktop cannot be detected, "
                            "only stated by whoever launched GENIE"])


# --------------------------------------------------------------------- scopes
def workspace_scope(root: Optional[str] = None) -> Dict[str, Any]:
    """What the workspace directory actually confines, and what it does not."""
    if not root:
        return {"kind": IsolationKind.WORKSPACE_DIR.value, "state": "unknown",
                "root": "", "confines": [], "does_not_confine": [],
                "evidence": ["no workspace root given"]}
    try:
        from computer.workspace import Workspace  # noqa: PLC0415 - heavy, lazy
        ws = Workspace(root)
        identity = ws.identity() if hasattr(ws, "identity") else {}
        quota = ws.quota() if hasattr(ws, "quota") else {}
    except Exception as exc:  # noqa: BLE001
        log.debug("workspace probe failed: %s", exc)
        return {"kind": IsolationKind.WORKSPACE_DIR.value, "state": "unknown",
                "root": str(root), "confines": [], "does_not_confine": [],
                "evidence": [f"probe failed: {exc}"]}
    return {
        "kind": IsolationKind.WORKSPACE_DIR.value,
        "state": "active",
        "root": str(root),
        "workspace_id": str(identity.get("workspace_id", "")),
        "confines": ["files written through the workspace API"],
        # The honest part: a workspace directory is a convention, not a jail.
        "does_not_confine": ["process execution", "network access",
                             "reads outside the root", "the owner's desktop session"],
        "quota": quota,
        "evidence": [f"workspace root {root} is managed by computer.workspace"],
    }


def browser_scope(profile_dir: Optional[str] = None) -> Dict[str, Any]:
    """What a dedicated browser profile isolates — and what leaks through."""
    if not profile_dir:
        return {"kind": IsolationKind.BROWSER_PROFILE.value, "state": "unknown",
                "profile": "", "confines": [], "does_not_confine": [],
                "evidence": ["no browser profile directory given"]}
    path = Path(profile_dir)
    return {
        "kind": IsolationKind.BROWSER_PROFILE.value,
        "state": "active" if path.exists() else "configured_but_absent",
        "profile": str(path),
        "confines": ["cookies", "local storage", "site logins", "browser extensions"],
        # A separate profile is not a separate browser and not a sandbox.
        "does_not_confine": ["network egress", "the IP the site sees",
                             "the OS user account", "files the browser may read"],
        "evidence": ([f"profile directory exists: {path}"]
                     if path.exists() else
                     [f"profile directory configured but absent: {path}"]),
    }


# --------------------------------------------------------------------- sensors
def sensor_sources() -> Dict[str, Any]:
    """Devices that exist, and which one is actually in use (usually: none declared)."""
    cameras: List[Dict[str, Any]] = []
    camera_state = "unknown"
    try:
        from perception.camera import select_provider  # noqa: PLC0415 - heavy, lazy
        provider = select_provider()
        if provider is not None:
            camera_state = "ready" if provider.available() else "missing_runtime"
            cameras = [c.to_dict() for c in provider.cameras()]
    except Exception as exc:  # noqa: BLE001
        log.debug("camera probe failed: %s", exc)
        camera_state = "unknown"

    microphones: List[Dict[str, Any]] = []
    mic_state = "unknown"
    try:
        from core.capability_status import probe_voice  # noqa: PLC0415 - lazy
        voice = probe_voice()
        microphones = list(voice.get("microphones") or [])
        mic_state = "ready" if microphones else "missing_runtime"
    except Exception as exc:  # noqa: BLE001
        log.debug("microphone probe failed: %s", exc)

    return {
        "cameras": {"state": camera_state, "devices": cameras,
                    "declared": None,
                    "note": "devices listed; none is in use until one is declared"},
        "microphones": {"state": mic_state, "devices": microphones,
                        "declared": None,
                        "note": "devices listed; none is listening until one is declared"},
    }


# ---------------------------------------------------------------------- report
def report(*, workspace_root: Optional[str] = None,
           browser_profile: Optional[str] = None) -> Dict[str, Any]:
    """Where GENIE is running, what confines it, and how sure we are."""
    levels = {
        "workspace": workspace_scope(workspace_root),
        "browser_profile": browser_scope(browser_profile),
        "container": detect_container(),
        "vm": detect_vm(),
        "isolated_desktop": detect_isolated_desktop(),
    }
    active: List[IsolationKind] = []
    if levels["container"]["detected"]:
        active.append(IsolationKind.CONTAINER)
    if levels["vm"]["detected"]:
        active.append(IsolationKind.VM)
    if levels["isolated_desktop"]["detected"]:
        active.append(IsolationKind.ISOLATED_DESKTOP)
    if levels["browser_profile"]["state"] == "active":
        active.append(IsolationKind.BROWSER_PROFILE)
    if levels["workspace"]["state"] == "active":
        active.append(IsolationKind.WORKSPACE_DIR)
    if not active:
        active.append(IsolationKind.HOST)

    effective = max(active, key=lambda k: _STRENGTH.get(k, 0))
    caveats = [
        "container and VM detection are heuristics; 'not detected' is not proof of bare metal",
        "a workspace directory confines file writes by convention, not by enforcement",
        "a dedicated browser profile is not a network sandbox and not a separate identity",
        "an isolated desktop can only be declared, never detected",
    ]
    return {
        "effective": effective.value,
        "levels": {name: {**value, "kind": value.get("kind", name)}
                   for name, value in levels.items()},
        "sensors": sensor_sources(),
        "caveats": caveats,
    }
