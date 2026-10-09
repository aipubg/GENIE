"""Plugin host process (plugins/host.py).

Runs ONE plugin in its own process, outside the GENIE daemon:

    GENIE daemon --(line-delimited JSON over stdio)--> plugin host --> plugin adapter

Consequences:
  * a crashing plugin kills the host process, not GENIE
  * a hanging plugin is killed by the client's timeout
  * the adapter cannot touch the daemon's databases or in-process state

Run directly:
    python -m plugins.host --plugin plugins/installed/media
    python -m plugins.host --plugin plugins/installed/media --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# allow `python plugins/host.py` as well as `python -m plugins.host`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.sdk import (  # noqa: E402
    PluginAdapter, PluginError, PluginManifest, Request, Response, load_manifest,
)


def load_adapter(directory: Path, manifest: PluginManifest) -> PluginAdapter:
    """Import the plugin's adapter module and instantiate its Adapter class."""
    adapter_path = directory / manifest.entry
    if not adapter_path.exists():
        raise PluginError("adapter_missing", f"{manifest.entry} not found in {directory}")
    spec = importlib.util.spec_from_file_location(f"genie_plugin_{manifest.id}", adapter_path)
    if spec is None or spec.loader is None:
        raise PluginError("adapter_unloadable", f"cannot load {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    adapter_cls = getattr(module, "Adapter", None)
    if adapter_cls is None:
        raise PluginError("adapter_invalid", "adapter.py must define an `Adapter` class")
    workspace = directory / "workspace"
    workspace.mkdir(exist_ok=True)
    return adapter_cls(manifest, workspace)


class PluginHost:
    """Serves one plugin over stdio until shutdown or EOF."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.manifest = load_manifest(self.directory)
        self.adapter: Optional[PluginAdapter] = None
        self.initialized = False
        self.stats = {"requests": 0, "errors": 0, "started_at": time.time()}

    # -------------------------------------------------------------- lifecycle
    def initialize(self) -> Dict[str, Any]:
        self.adapter = load_adapter(self.directory, self.manifest)
        result = self.adapter.initialize() or {}
        self.initialized = bool(result.get("ok", True))
        return {"ok": self.initialized, "plugin": self.manifest.id,
                "version": self.manifest.version, "detail": result}

    def health(self) -> Dict[str, Any]:
        if self.adapter is None:
            return {"ok": False, "available": False, "detail": "adapter not loaded"}
        try:
            result = self.adapter.health() or {}
            result.setdefault("ok", True)
            return result
        except Exception as exc:
            return {"ok": False, "available": False, "detail": f"health check failed: {exc}"}

    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.adapter is None:
            raise PluginError("not_initialized", "plugin was not initialized")
        if self.manifest.spec_for(capability) is None:
            raise PluginError("unknown_capability",
                              f"{self.manifest.id} does not declare {capability}")
        result = self.adapter.invoke(capability, params or {})
        if not isinstance(result, dict):
            result = {"ok": bool(result), "value": result}
        result.setdefault("ok", False)
        result.setdefault("capability", capability)
        result.setdefault("plugin", self.manifest.id)
        return result

    # ------------------------------------------------------------------ serve
    def handle(self, request: Request) -> Response:
        started = time.time()
        self.stats["requests"] += 1
        try:
            if request.method == "initialize":
                payload = self.initialize()
            elif request.method == "health":
                payload = self.health()
            elif request.method == "invoke":
                # A capability that reports ok=false is a SUCCESSFUL protocol exchange: the
                # plugin ran and reported a negative outcome. Only protocol/adapter failures
                # (exceptions) produce a failed Response. Mixing the two loses the real reason.
                payload = self.invoke(str(request.params.get("capability", "")),
                                      request.params.get("params") or {})
                return Response(request.id, True, payload,
                                duration_ms=int((time.time() - started) * 1000))
            elif request.method == "manifest":
                payload = self.manifest.to_dict()
            elif request.method == "shutdown":
                if self.adapter is not None:
                    self.adapter.shutdown()
                return Response(request.id, True, {"ok": True},
                                duration_ms=int((time.time() - started) * 1000))
            elif request.method == "ping":
                payload = {"ok": True, "pong": True}
            else:
                raise PluginError("unknown_method", f"unsupported method {request.method!r}")
            return Response(request.id, bool(payload.get("ok", True)), payload,
                            duration_ms=int((time.time() - started) * 1000))
        except PluginError as exc:
            self.stats["errors"] += 1
            return Response(request.id, False, None, error=exc.to_dict(),
                            duration_ms=int((time.time() - started) * 1000))
        except Exception as exc:                     # never leak a raw traceback as success
            self.stats["errors"] += 1
            return Response(request.id, False, None, error={
                "code": "plugin_exception", "message": str(exc),
                "detail": traceback.format_exc(limit=3),
            }, duration_ms=int((time.time() - started) * 1000))

    def serve_stdio(self) -> int:
        # initialize eagerly so a broken plugin fails fast and visibly
        init = self.handle(Request(id="init", method="initialize"))
        sys.stdout.write(init.to_json() + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = Request.from_json(line)
            except Exception as exc:
                sys.stdout.write(Response("", False, None,
                                          error={"code": "bad_request",
                                                 "message": str(exc)}).to_json() + "\n")
                sys.stdout.flush()
                continue
            response = self.handle(request)
            sys.stdout.write(response.to_json() + "\n")
            sys.stdout.flush()
            if request.method == "shutdown":
                break
        return 0

    def self_test(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {"plugin": self.manifest.id, "steps": {}}
        try:
            report["steps"]["initialize"] = self.initialize()
            report["steps"]["health"] = self.health()
            for spec in self.manifest.capabilities:
                capability = f"plugin.{self.manifest.id}.{spec.name}"
                try:
                    result = self.invoke(capability, {})
                    report["steps"][spec.name] = {
                        "ok": bool(result.get("ok")),
                        "available": result.get("available", True),
                        "detail": str(result.get("detail", ""))[:160],
                    }
                except PluginError as exc:
                    report["steps"][spec.name] = {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            report["error"] = str(exc)
        return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="genie-plugin-host")
    parser.add_argument("--plugin", required=True, help="plugin directory")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    directory = Path(args.plugin).resolve()
    try:
        host = PluginHost(directory)
    except PluginError as exc:
        payload = {"ok": False, "error": exc.to_dict()}
        print(json.dumps(payload) if args.json else f"plugin load failed: {exc.message}")
        return 2

    if args.self_test:
        report = host.self_test()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("steps", {}).get("initialize", {}).get("ok") else 1
    return host.serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
