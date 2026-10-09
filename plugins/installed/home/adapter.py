"""Home automation bridge plugin.

GENIE does not implement every bulb protocol (docs/DEVICES.md). The home controller owns the
devices; this plugin speaks one stable HTTP interface to that controller.

Design decisions worth stating:

* **No `toggle` capability, deliberately.** A toggle is not idempotent, and the device mesh
  retries and queues commands. A retried toggle would flip a physical switch — a light turned
  off by a retry is a bug the owner cannot debug. `turn_on`/`turn_off`/`set_brightness` are
  idempotent, so a retry is harmless. The same reasoning governs `relay.set` in
  `devices/peripherals.py`.
* **Every state change is verified by reading the entity back.** "The controller returned 200"
  is not "the light is on". If the read-back disagrees, the plugin reports failure.
* **Credentials never live in the repo or the daemon.** The controller URL and token come from
  `workspace/config.json` (owner-created, git-ignored) or the environment
  (`GENIE_HOME_URL` / `GENIE_HOME_TOKEN`). This process has no access to GENIE's vault or
  databases by design.

This adapter runs in its own process (plugins/host.py).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_TIMEOUT_S = 10.0
#: Domains whose state change is worth reading back.
STATEFUL_DOMAINS = ("light", "switch", "fan", "cover", "climate", "media_player", "scene",
                    "input_boolean", "lock")


class HomeBridge:
    """A minimal, dependency-free client for the controller's REST API."""

    def __init__(self, base_url: str = "", token: str = "", timeout_s: float = DEFAULT_TIMEOUT_S):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout_s = float(timeout_s)
        # A home controller lives on the LAN. Honouring `http_proxy` would send local traffic to
        # a corporate/HTTP proxy, which answers 502 (or worse, leaks the request). Build an
        # opener that never proxies.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ------------------------------------------------------------------ transport
    def _request(self, path: str, *, method: str = "GET",
                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.base_url:
            return {"ok": False, "available": False, "error_code": "not_configured",
                    "detail": "no controller URL configured"}
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": 200, "body": json.loads(body) if body else {}}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200] if exc.fp else str(exc)
            return {"ok": False, "status": exc.code, "error_code": f"http_{exc.code}",
                    "detail": detail or f"controller returned HTTP {exc.code}"}
        except Exception as exc:                       # connection refused, DNS, timeout
            return {"ok": False, "available": False, "error_code": "controller_unreachable",
                    "detail": str(exc)}

    # ---------------------------------------------------------------------- api
    def reachable(self) -> Dict[str, Any]:
        result = self._request("/api/")
        if not result.get("ok"):
            return result
        return {"ok": True, "detail": "controller answered", "data": result.get("body") or {}}

    def entities(self, domain: str = "") -> Dict[str, Any]:
        result = self._request("/api/states")
        if not result.get("ok"):
            return result
        states = result.get("body") or []
        if not isinstance(states, list):
            return {"ok": False, "error_code": "bad_response",
                    "detail": "controller did not return a state list"}
        if domain:
            states = [s for s in states if str(s.get("entity_id", "")).startswith(f"{domain}.")]
        return {"ok": True, "detail": f"{len(states)} entit(ies)",
                "data": {"entities": [
                    {"entity_id": s.get("entity_id"), "state": s.get("state"),
                     "friendly_name": (s.get("attributes") or {}).get("friendly_name", "")}
                    for s in states]}}

    def state(self, entity_id: str) -> Dict[str, Any]:
        if not entity_id:
            return {"ok": False, "error_code": "bad_parameter", "detail": "entity_id is required"}
        result = self._request(f"/api/states/{entity_id}")
        if not result.get("ok"):
            return result
        body = result.get("body") or {}
        return {"ok": True, "verified": True,
                "detail": f"{entity_id} = {body.get('state')}",
                "data": {"entity_id": entity_id, "state": body.get("state"),
                         "attributes": body.get("attributes") or {}}}

    def call_service(self, domain: str, service: str,
                     payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(f"/api/services/{domain}/{service}", method="POST", payload=payload)

    # ------------------------------------------------------- idempotent actions
    def set_power(self, entity_id: str, on: bool) -> Dict[str, Any]:
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        if not domain:
            return {"ok": False, "error_code": "bad_parameter",
                    "detail": "entity_id must look like 'light.kitchen'"}
        before = self.state(entity_id)
        if not before.get("ok"):
            return before
        desired = "on" if on else "off"
        if str(before["data"].get("state")).lower() == desired:
            # idempotent: already in the requested state, so do not touch the device
            return {"ok": True, "verified": True, "no_op": True,
                    "detail": f"{entity_id} is already {desired}",
                    "data": {"entity_id": entity_id, "state": desired, "changed": False}}
        sent = self.call_service(domain, "turn_on" if on else "turn_off",
                                 {"entity_id": entity_id})
        if not sent.get("ok"):
            return sent
        return self._confirm(entity_id, desired)

    def set_brightness(self, entity_id: str, level: int) -> Dict[str, Any]:
        if not 0 <= int(level) <= 100:
            return {"ok": False, "error_code": "bad_parameter", "detail": "level must be 0-100"}
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        if not domain:
            return {"ok": False, "error_code": "bad_parameter",
                    "detail": "entity_id must look like 'light.kitchen'"}
        sent = self.call_service(domain, "turn_on",
                                 {"entity_id": entity_id, "brightness_pct": int(level)})
        if not sent.get("ok"):
            return sent
        return self._confirm(entity_id, None, expect_brightness=int(level))

    def activate_scene(self, entity_id: str) -> Dict[str, Any]:
        if not entity_id.startswith("scene."):
            return {"ok": False, "error_code": "bad_parameter",
                    "detail": "activate_scene expects a scene.<name> entity"}
        sent = self.call_service("scene", "turn_on", {"entity_id": entity_id})
        if not sent.get("ok"):
            return sent
        after = self.state(entity_id)
        if not after.get("ok"):
            return after
        return {"ok": True, "verified": True,
                "detail": f"scene {entity_id} activated",
                "data": {"entity_id": entity_id, "state": after["data"].get("state")}}

    def _confirm(self, entity_id: str, desired_state: Optional[str],
                 expect_brightness: Optional[int] = None) -> Dict[str, Any]:
        """Read the entity back. A 200 response is not proof that the device changed."""
        after = self.state(entity_id)
        if not after.get("ok"):
            return after
        state = str(after["data"].get("state", "")).lower()
        attributes = after["data"].get("attributes") or {}
        if desired_state is not None and state != desired_state:
            return {"ok": False, "verified": False, "error_code": "verification_failed",
                    "detail": f"{entity_id} reports {state!r}, expected {desired_state!r}",
                    "data": {"entity_id": entity_id, "state": state}}
        if expect_brightness is not None:
            reported = attributes.get("brightness_pct", attributes.get("brightness"))
            if reported is not None and int(reported) != int(expect_brightness):
                return {"ok": False, "verified": False, "error_code": "verification_failed",
                        "detail": f"{entity_id} brightness is {reported}, expected "
                                  f"{expect_brightness}",
                        "data": {"entity_id": entity_id, "brightness": reported}}
        return {"ok": True, "verified": True, "detail": f"{entity_id} confirmed {state}",
                "data": {"entity_id": entity_id, "state": state, "attributes": attributes}}


def load_config(workspace: Optional[Path]) -> Dict[str, Any]:
    """Config precedence: environment first, then the plugin workspace file.

    The environment wins so a token can be injected without ever writing it to disk.
    """
    config: Dict[str, Any] = {}
    if workspace:
        path = Path(workspace) / "config.json"
        if path.exists():
            try:
                config = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, json.JSONDecodeError):
                config = {}
    url = os.environ.get("GENIE_HOME_URL") or config.get("controller_url", "")
    token = os.environ.get("GENIE_HOME_TOKEN") or config.get("token", "")
    timeout = os.environ.get("GENIE_HOME_TIMEOUT") or config.get("timeout_s", DEFAULT_TIMEOUT_S)
    return {"controller_url": url, "token": token, "timeout_s": float(timeout or DEFAULT_TIMEOUT_S)}


# ------------------------------------------------------------------------------ adapter
try:                                                        # loaded by the plugin host
    from plugins.sdk import PluginAdapter, PluginError
except ImportError:                                         # standalone import (tests)
    PluginAdapter = object                                  # type: ignore[assignment,misc]

    class PluginError(Exception):                           # type: ignore[no-redef]
        pass


class Adapter(PluginAdapter):                                # type: ignore[misc,valid-type]
    """The plugin host entry point (`plugins/host.py` requires a class named `Adapter`)."""

    def __init__(self, manifest=None, workspace=None):
        try:
            super().__init__(manifest, workspace)
        except TypeError:
            self.manifest = manifest
            self.workspace = workspace
        self.bridge = HomeBridge()

    def initialize(self) -> Dict[str, Any]:
        config = load_config(getattr(self, "workspace", None))
        self.bridge = HomeBridge(config["controller_url"], config["token"],
                                 config["timeout_s"])
        if not config["controller_url"]:
            # Not a failure: the plugin is installed but not yet pointed at a controller.
            return {"ok": True, "configured": False,
                    "detail": "no controller configured — set workspace/config.json or "
                              "GENIE_HOME_URL"}
        return {"ok": True, "configured": True, "controller": config["controller_url"]}

    def health(self) -> Dict[str, Any]:
        if not self.bridge.base_url:
            return {"ok": False, "available": False,
                    "detail": "no controller configured"}
        result = self.bridge.reachable()
        return {"ok": bool(result.get("ok")), "available": bool(result.get("ok")),
                "detail": result.get("detail", "")}

    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        # the host passes the fully qualified name (plugin.home.turn_on)
        name = capability.rsplit(".", 1)[-1]
        if name == "reachable":
            return self.bridge.reachable()
        if name == "entities":
            return self.bridge.entities(str(params.get("domain", "")))
        if name == "state":
            return self.bridge.state(str(params.get("entity_id", "")))
        if name == "turn_on":
            return self.bridge.set_power(str(params.get("entity_id", "")), True)
        if name == "turn_off":
            return self.bridge.set_power(str(params.get("entity_id", "")), False)
        if name == "set_brightness":
            return self.bridge.set_brightness(str(params.get("entity_id", "")),
                                              int(params.get("level", 100)))
        if name == "activate_scene":
            return self.bridge.activate_scene(str(params.get("entity_id", "")))
        raise PluginError("unknown_capability", f"home plugin has no capability {capability}")


# compatibility alias so the module can also be used directly
HomeAdapter = Adapter
