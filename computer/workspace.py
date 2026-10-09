"""GENIE isolated workspace (computer/workspace).

Two worlds (master spec §22):

    GENIE Workspace  -> agent experiments, builds, downloads, temp files   (safe to break)
    User Desktop     -> personal apps, games, projects, real interaction   (protected)

Every filesystem/agent action is workspace-first. Crossing the boundary requires an explicit
PTE scope plus confirmation. Isolation is *detected*, not assumed: if Hyper-V/WSL are
available they can host a stronger boundary, otherwise GENIE uses a controlled isolated
directory tree with path enforcement (never "run whatever the agent wants on the desktop").
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

from . import state

log = get_logger("computer.workspace")

WORKSPACE_SUBDIRS = ["coding", "builds", "downloads", "research", "agents", "temp", "artifacts"]

#: Default ceiling for the GENIE workspace (§11C). Agents must never fill the owner's disk.
DEFAULT_QUOTA_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in WORKSPACE_SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)

    # ------------------------------------------------------------------ paths
    def path(self, *parts: str, create_parent: bool = True) -> Path:
        target = (self.root.joinpath(*parts)).resolve()
        if not self.is_inside(target):
            raise PermissionError(f"path escapes the GENIE workspace: {target}")
        if create_parent:
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def is_inside(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def classify(self, path: str | Path) -> str:
        return "workspace" if self.is_inside(path) else "outside"

    # ------------------------------------------------------------------- info
    def usage(self) -> Dict[str, Any]:
        total = 0
        files = 0
        for p in self.root.rglob("*"):
            if p.is_file():
                files += 1
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return {"root": str(self.root), "files": files, "bytes": total,
                "mb": round(total / 1e6, 2)}

    def list(self, subdir: str = "") -> List[Dict[str, Any]]:
        base = self.path(subdir, create_parent=True) if subdir else self.root
        out = []
        for p in sorted(base.iterdir()):
            try:
                st = p.stat()
                out.append({"name": p.name, "is_dir": p.is_dir(), "size": st.st_size,
                            "mtime": int(st.st_mtime)})
            except OSError:
                continue
        return out


    # ------------------------------------------------------ workspace identity
    def identity(self) -> Dict[str, Any]:
        """Who is this workspace? (§11C) Distinguishes GENIE's computer from the user's."""
        marker = self.root / ".genie-workspace"
        created = None
        if marker.exists():
            try:
                created = int(marker.read_text(encoding="utf-8").strip() or 0) or None
            except OSError:
                created = None
        return {"kind": "genie-workspace", "root": str(self.root),
                "workspace_id": self._workspace_id(), "created_ms": created,
                "isolation": detect_isolation().get("preferred", "local-workspace"),
                "subdirs": list(WORKSPACE_SUBDIRS), "usage": self.usage()}

    def _workspace_id(self) -> str:
        import hashlib
        return hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]

    def ensure_identity(self) -> Dict[str, Any]:
        marker = self.root / ".genie-workspace"
        if not marker.exists():
            import time
            marker.write_text(str(int(time.time() * 1000)), encoding="utf-8")
        return self.identity()

    # -------------------------------------------------------- mission ownership
    def mission_dir(self, mission_id: str, sub: str = "") -> Path:
        """Per-mission scoped area so concurrent missions never collide (§11C)."""
        safe = "".join(c for c in str(mission_id) if c.isalnum() or c in "-_") or "unassigned"
        target = self.path("missions", safe, sub, create_parent=True) if sub \
            else self.path("missions", safe, create_parent=True)
        for leaf in ("downloads", "artifacts", "temp"):
            (self.path("missions", safe, leaf)).mkdir(parents=True, exist_ok=True)
        return target

    def _registry_path(self) -> Path:
        return self.root / "missions.json"

    def _registry(self) -> Dict[str, Any]:
        import json
        p = self._registry_path()
        if not p.exists():
            return {"missions": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8") or '{"missions": {}}')
        except (OSError, ValueError):
            return {"missions": {}}

    def _write_registry(self, data: Dict[str, Any]) -> None:
        import json
        self._registry_path().write_text(json.dumps(data, indent=2), encoding="utf-8")

    def claim_mission(self, mission_id: str, *, agent_id: str = "", purpose: str = ""
                      ) -> Dict[str, Any]:
        """Claim workspace ownership for a mission (agents work here, not on the desktop)."""
        import time
        safe = "".join(c for c in str(mission_id) if c.isalnum() or c in "-_")
        data = self._registry()
        self.mission_dir(safe)  # create the scoped tree
        data["missions"][safe] = {"mission_id": safe, "agent_id": agent_id,
                                  "purpose": purpose, "claimed_ms": int(time.time() * 1000),
                                  "state": "active"}
        self._write_registry(data)
        return {"ok": True, "mission_id": safe, "path": str(self.mission_dir(safe))}

    def release_mission(self, mission_id: str, *, purge: bool = False) -> Dict[str, Any]:
        """Release ownership; optionally purge the mission's files (cleanup rule)."""
        safe = "".join(c for c in str(mission_id) if c.isalnum() or c in "-_")
        data = self._registry()
        entry = data["missions"].pop(safe, None)
        freed = 0
        if purge:
            target = self.path("missions", safe, create_parent=False)
            if target.exists():
                freed = self._tree_bytes(target)
                shutil.rmtree(target, ignore_errors=True)
        self._write_registry(data)
        return {"ok": True, "mission_id": safe, "released": entry is not None,
                "purged": purge, "bytes_freed": freed}

    def active_missions(self) -> List[Dict[str, Any]]:
        return list(self._registry().get("missions", {}).values())

    def _tree_bytes(self, path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    # ------------------------------------------------------------ storage quota
    def quota(self) -> Dict[str, Any]:
        used = self.usage()["bytes"]
        cap = self._quota_cap()
        return {"used_bytes": used, "cap_bytes": cap,
                "remaining_bytes": None if cap is None else max(0, cap - used),
                "pct": None if not cap else round(used / cap * 100, 1)}

    def _quota_cap(self) -> Optional[int]:
        p = self.root / "quota.json"
        if not p.exists():
            return DEFAULT_QUOTA_BYTES
        try:
            import json
            return int(json.loads(p.read_text(encoding="utf-8")).get("cap_bytes", 0)) or None
        except (OSError, ValueError):
            return DEFAULT_QUOTA_BYTES

    def set_quota(self, cap_bytes: Optional[int]) -> Dict[str, Any]:
        import json
        (self.root / "quota.json").write_text(
            json.dumps({"cap_bytes": cap_bytes}), encoding="utf-8")
        return self.quota()

    def check_quota(self, *, need_bytes: int = 0) -> Dict[str, Any]:
        """Refuse work that would burst the workspace quota (§11C)."""
        q = self.quota()
        if q["cap_bytes"] is None:
            return {"ok": True, "quota": q}
        if need_bytes and need_bytes > (q["remaining_bytes"] or 0):
            return {"ok": False, "error": f"workspace quota exceeded: need {need_bytes}, "
                                          f"{q['remaining_bytes']} left", "quota": q}
        if q["remaining_bytes"] == 0:
            return {"ok": False, "error": "workspace quota exhausted", "quota": q}
        return {"ok": True, "quota": q}

    # ----------------------------------------------------------------- cleanup
    def cleanup(self, *, max_age_days: float = 7.0, mission_id: str = "",
                dry_run: bool = True) -> Dict[str, Any]:
        """Remove stale temp/downloads. dry_run=true reports only — never guess-delete."""
        import time
        cutoff = time.time() - max_age_days * 86400
        roots: List[Path] = []
        if mission_id:
            safe = "".join(c for c in str(mission_id) if c.isalnum() or c in "-_")
            roots.append(self.path("missions", safe, "temp", create_parent=False))
            roots.append(self.path("missions", safe, "downloads", create_parent=False))
        else:
            roots.append(self.root / "temp")
            roots.append(self.root / "downloads")
            roots.append(self.root / "missions")
        removed: List[str] = []
        freed = 0
        for base in roots:
            if not base.exists():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file():
                    continue
                try:
                    if p.stat().st_mtime >= cutoff:
                        continue
                    size = p.stat().st_size
                    if not dry_run:
                        p.unlink()
                    removed.append(str(p))
                    freed += size
                except OSError:
                    continue
        return {"ok": True, "dry_run": dry_run, "removed": len(removed),
                "bytes_freed": freed, "max_age_days": max_age_days,
                "sample": removed[:20]}

    # ------------------------------------------------- sessions & browser profile
    def session_dir(self, name: str = "default") -> Path:
        """Persistent session/profile area — logins survive across missions (§11C)."""
        safe = "".join(c for c in str(name) if c.isalnum() or c in "-_") or "default"
        return self.path("sessions", safe, create_parent=True)

    def browser_profile_dir(self, session: str = "default") -> Path:
        """Dedicated Chrome profile for the GENIE computer — never the user's Chrome."""
        return self.path("sessions", session, "browser-profile", create_parent=True)

    def downloads_dir(self, mission_id: str = "") -> Path:
        if mission_id:
            safe = "".join(c for c in str(mission_id) if c.isalnum() or c in "-_")
            return self.path("missions", safe, "downloads", create_parent=True)
        return self.path("downloads", create_parent=True)

    # ------------------------------------------------------------------- shell
    def shell(self, command: str, *, cwd: str = "", timeout: int = 60,
              mission_id: str = "") -> Dict[str, Any]:
        """Run a command inside the workspace. cwd is forced inside — never the desktop."""
        base = self.mission_dir(mission_id) if mission_id else self.root
        work = (base / cwd).resolve() if cwd else base
        if not self.is_inside(work):
            return {"ok": False, "error": f"cwd escapes the GENIE workspace: {work}"}
        work.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(command, cwd=str(work), shell=True, timeout=timeout,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s", "cwd": str(work)}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "cwd": str(work)}
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "cwd": str(work), "stdout": (proc.stdout or "")[:8000],
                "stderr": (proc.stderr or "")[:2000]}


def detect_isolation() -> Dict[str, Any]:
    """What isolation options does this machine offer? (owner requirement: detect, never
    block development on a missing virtualization technology)

    A-031: detection is **passive** — we look for installed binaries and services on disk
    instead of executing them. Spawning `wsl.exe`/`dism` on every boot is slow and can be
    blocked by host security policy; GENIE must never depend on being allowed to run them.
    """
    result: Dict[str, Any] = {"options": [], "preferred": "local-workspace", "details": {}}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))

    # 1. Hyper-V (vmms service binary + vmcompute present)
    hyperv = (system_root / "System32" / "vmms.exe").exists() and \
             (system_root / "System32" / "vmcompute.exe").exists()
    result["details"]["hyperv"] = hyperv
    if hyperv:
        result["options"].append("hyperv")

    # 2. WSL2 (wsl.exe present + a WSL distro store)
    wsl_exe = (system_root / "System32" / "wsl.exe").exists()
    wsl_store = (os.environ.get("LOCALAPPDATA") and
                 Path(os.environ["LOCALAPPDATA"], "Packages").exists())
    result["details"]["wsl2_binary"] = wsl_exe
    if wsl_exe and wsl_store:
        result["options"].append("wsl2")

    # 3. Windows Sandbox
    sandbox = (system_root / "System32" / "WindowsSandbox.exe").exists()
    result["details"]["windows_sandbox"] = sandbox
    if sandbox:
        result["options"].append("windows-sandbox")

    # 4. directory isolation is always available (never blocked)
    result["options"].append("local-workspace")
    for preferred in ("hyperv", "windows-sandbox", "wsl2"):
        if preferred in result["options"]:
            result["preferred"] = preferred
            break
    return result


def resource_profile() -> Dict[str, Any]:
    """Adaptive profile for low-end machines (spec §8.7 / low-end PC support)."""
    info = state.system_info()
    ram = info.get("ram_total_gb", 0) or 0
    cpus = info.get("cpu_count", 1) or 1
    if ram and ram < 6:
        profile = "low"
    elif ram and ram < 12:
        profile = "balanced"
    else:
        profile = "full"
    return {
        "profile": profile,
        "ram_total_gb": ram,
        "cpu_count": cpus,
        "scale": {"low": 0.4, "balanced": 0.7, "full": 1.0}[profile],
        "advice": {
            "low": "reduce polling, single agent, no continuous capture",
            "balanced": "moderate polling, up to 2 agents",
            "full": "full polling, multi-agent",
        }[profile],
    }


_WORKSPACE: Optional[Workspace] = None


def get_workspace(root: str | Path | None = None) -> Workspace:
    global _WORKSPACE
    if _WORKSPACE is None:
        if root is None:
            from core.config import get_config
            cfg = get_config()
            root = cfg.data_dir / cfg.get("computer.workspace", "workspace")
        _WORKSPACE = Workspace(root)
    return _WORKSPACE
