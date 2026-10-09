"""GENIE Plugin SDK (plugins/sdk.py).

A plugin is a folder with a manifest and an adapter:

    plugins/installed/<id>/
        plugin.json     manifest (declared below)
        adapter.py      the code GENIE runs — in a SEPARATE process
        README.md
        tests/

Contracts:
  * plugins talk to GENIE only through this protocol (line-delimited JSON over stdio)
  * a plugin never touches the Mission DB, Memory DB, NEDLE2 state or any other service
  * capabilities are declared up-front; nothing is implicit
  * permissions are declared and must be granted by the owner (default deny)

Patterns adopted from the supplied repos: a *declared capability catalogue* with lifecycle
events (deepseek-harness architecture notes) and an activity/tab model for long-lived
sessions (pinchtab). No code was imported from either — they are Node/TS projects and GENIE's
daemon stays dependency-free (D-040/D-051).
"""
from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

MANIFEST_NAME = "plugin.json"
ADAPTER_NAME = "adapter.py"
SUPPORTED_PROTOCOL = 1

# Permission scopes a plugin may request (default deny: anything not granted is refused)
KNOWN_PERMISSION_PREFIXES = (
    "application.", "filesystem.", "network.", "device.", "clipboard.", "audio.",
    "browser.", "system.", "secrets.",
)


class PluginError(Exception):
    """Structured plugin error propagated to the caller unchanged."""

    def __init__(self, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


@dataclass
class CapabilitySpec:
    """One invocable capability a plugin provides."""

    name: str                       # e.g. "play"  -> GENIE capability "plugin.spotify.play"
    description: str = ""
    params: Dict[str, Any] = field(default_factory=dict)      # simple JSON-schema-ish hints
    returns: str = ""
    verification: str = ""          # how GENIE should confirm the effect
    timeout_ms: int = 15_000
    dangerous: bool = False
    requires_app: str = ""          # e.g. "spotify.exe" — absent => honest UNAVAILABLE
    permissions: List[str] = field(default_factory=list)   # subset required for THIS capability

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "params": self.params,
                "returns": self.returns, "verification": self.verification,
                "timeout_ms": self.timeout_ms, "dangerous": self.dangerous,
                "requires_app": self.requires_app, "permissions": self.permissions}


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    entry: str = ADAPTER_NAME
    description: str = ""
    capabilities: List[CapabilitySpec] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=lambda: ["windows"])
    dependencies: Dict[str, str] = field(default_factory=dict)
    health: Dict[str, Any] = field(default_factory=dict)
    author: str = ""
    license: str = ""
    min_genie_version: str = "1.0"
    first_party: bool = False

    # ------------------------------------------------------------------ helpers
    def capability_names(self) -> List[str]:
        return [f"plugin.{self.id}.{c.name}" for c in self.capabilities]

    def spec_for(self, capability: str) -> Optional[CapabilitySpec]:
        prefix = f"plugin.{self.id}."
        if not capability.startswith(prefix):
            return None
        short = capability[len(prefix):]
        return next((c for c in self.capabilities if c.name == short), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "entry": self.entry, "description": self.description,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "observations": self.observations, "permissions": self.permissions,
            "platforms": self.platforms, "dependencies": self.dependencies,
            "health": self.health, "author": self.author, "license": self.license,
            "min_genie_version": self.min_genie_version, "first_party": self.first_party,
        }


_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def parse_manifest(raw: Dict[str, Any]) -> PluginManifest:
    """Validate a manifest dict and return the typed manifest."""
    missing = [key for key in ("id", "name", "version", "capabilities") if not raw.get(key)]
    if missing:
        raise PluginError("invalid_manifest", f"missing required fields: {', '.join(missing)}")
    if not _ID_RE.match(str(raw["id"])):
        raise PluginError("invalid_manifest", f"invalid plugin id: {raw['id']!r}")

    caps: List[CapabilitySpec] = []
    for entry in raw.get("capabilities") or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        name = str(entry.get("name", ""))
        if not _CAP_RE.match(name):
            raise PluginError("invalid_manifest", f"invalid capability name: {name!r}")
        caps.append(CapabilitySpec(
            name=name,
            description=str(entry.get("description", "")),
            params=entry.get("params") or {},
            returns=str(entry.get("returns", "")),
            verification=str(entry.get("verification", "")),
            timeout_ms=int(entry.get("timeout_ms", 15_000)),
            dangerous=bool(entry.get("dangerous", False)),
            requires_app=str(entry.get("requires_app", "")),
            permissions=[str(p) for p in (entry.get("permissions") or [])],
        ))
    if not caps:
        raise PluginError("invalid_manifest", "a plugin must declare at least one capability")

    permissions = [str(p) for p in (raw.get("permissions") or [])]
    for permission in permissions:
        if not permission.startswith(KNOWN_PERMISSION_PREFIXES):
            raise PluginError("invalid_manifest",
                              f"unknown permission scope: {permission!r}")

    return PluginManifest(
        id=str(raw["id"]), name=str(raw["name"]), version=str(raw["version"]),
        entry=str(raw.get("entry", ADAPTER_NAME)), description=str(raw.get("description", "")),
        capabilities=caps, observations=[str(o) for o in (raw.get("observations") or [])],
        permissions=permissions, platforms=[str(p) for p in (raw.get("platforms") or ["windows"])],
        dependencies=raw.get("dependencies") or {}, health=raw.get("health") or {},
        author=str(raw.get("author", "")), license=str(raw.get("license", "")),
        min_genie_version=str(raw.get("min_genie_version", "1.0")),
        first_party=bool(raw.get("first_party", False)),
    )


def load_manifest(directory: str | Path) -> PluginManifest:
    path = Path(directory) / MANIFEST_NAME
    if not path.exists():
        raise PluginError("manifest_missing", f"{MANIFEST_NAME} not found in {directory}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginError("invalid_manifest", f"{MANIFEST_NAME} is not valid JSON: {exc}")
    return parse_manifest(raw)


def platform_supported(manifest: PluginManifest) -> bool:
    current = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform, sys.platform)
    return current in manifest.platforms


# --------------------------------------------------------------------------- protocol
@dataclass
class Request:
    id: str
    method: str                       # initialize | health | invoke | shutdown
    params: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"id": self.id, "method": self.method, "params": self.params})

    @classmethod
    def from_json(cls, line: str) -> "Request":
        raw = json.loads(line)
        return cls(id=str(raw.get("id", "")), method=str(raw.get("method", "")),
                   params=raw.get("params") or {})


@dataclass
class Response:
    id: str
    ok: bool
    result: Any = None
    error: Optional[Dict[str, Any]] = None
    duration_ms: int = 0

    def to_json(self) -> str:
        return json.dumps({"id": self.id, "ok": self.ok, "result": self.result,
                           "error": self.error, "duration_ms": self.duration_ms})

    @classmethod
    def from_json(cls, line: str) -> "Response":
        raw = json.loads(line)
        return cls(id=str(raw.get("id", "")), ok=bool(raw.get("ok")),
                   result=raw.get("result"), error=raw.get("error"),
                   duration_ms=int(raw.get("duration_ms", 0)))


# ------------------------------------------------------------------------ adapter
class PluginAdapter:
    """Base class every plugin adapter subclasses.

    The adapter runs inside the plugin host process, never inside the daemon. It may use the
    standard library freely, but it has no access to GENIE's databases or internal state.
    """

    def __init__(self, manifest: PluginManifest, workspace: Optional[Path] = None):
        self.manifest = manifest
        self.workspace = Path(workspace) if workspace else Path.cwd()

    # ------------------------------------------------------------- lifecycle
    def initialize(self) -> Dict[str, Any]:
        """Called once when the host starts. Return {"ok": True} or raise."""
        return {"ok": True}

    def health(self) -> Dict[str, Any]:
        """Report health. Return {"ok": bool, "detail": str, "available": bool}."""
        return {"ok": True, "available": True, "detail": "ready"}

    def shutdown(self) -> None:
        """Release resources."""

    # -------------------------------------------------------------- invoking
    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a capability. Must return {"ok": bool, ...} — never assume success."""
        raise PluginError("not_implemented", f"{capability} is not implemented")

    # ---------------------------------------------------------------- helpers
    def unavailable(self, reason: str) -> Dict[str, Any]:
        """Honest UNAVAILABLE result (target application missing, service down, ...)."""
        return {"ok": False, "available": False, "detail": reason, "error": reason,
                "error_code": "unavailable"}
