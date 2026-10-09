"""Daemon-side plugin client (plugins/client.py).

Owns one plugin host process: starts it, talks to it over line-delimited JSON, enforces
timeouts, counts crashes, restarts it, and disables a plugin that keeps failing.

Invariant (P0): this module must never turn a broken plugin into a crash -> respawn
loop. A host that dies before it can answer `initialize` has failed deterministically,
so it is counted in a persisted ledger and the circuit breaker stops spawning it.

A plugin crash is a **plugin** failure: the daemon keeps running and reports a structured
error to the caller.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from plugins.sdk import PluginError, PluginManifest, Request, Response

log = get_logger("plugins.client")

DEFAULT_CRASH_LIMIT = 3
STARTUP_TIMEOUT_S = 25.0
HEALTH_TIMEOUT_S = 10.0

# --- P0: no crash -> respawn churn -----------------------------------------
# A host that dies within FAST_FAIL_S of being spawned never reached
# `initialize`. That is a deterministic failure (missing module, broken
# interpreter, bad manifest) - retrying it on every daemon boot is exactly the
# respawn storm this module must never produce. Two consecutive startup
# failures open a persisted circuit breaker with exponential cooldown.
FAST_FAIL_S = 2.0
STARTUP_FAIL_LIMIT = 2
CIRCUIT_BASE_COOLDOWN_S = 300.0
CIRCUIT_MAX_COOLDOWN_S = 7200.0


_LEDGER_LOCK = threading.RLock()


def _ledger_path() -> Path:
    """Mutable, per-installation ledger for deterministic plugin startup failures."""
    try:
        from core import paths                      # single path authority
        return Path(paths.data_dir()) / "plugins" / "startup_circuit.json"
    except Exception:
        return Path(__file__).resolve().parents[1] / "data" / "plugins" / "startup_circuit.json"


def _load_ledger() -> Dict[str, Any]:
    path = _ledger_path()
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        pass
    return {}


def _save_ledger(data: Dict[str, Any]) -> None:
    """Atomic write; a ledger failure must never break plugin startup."""
    path = _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as exc:
        log.debug("plugin startup-circuit ledger not persisted: %s", exc)


@dataclass
class PluginCircuit:
    """Circuit breaker for deterministic (startup) plugin-host failures.

    Only failures that happen *before* the host answers `initialize` are counted
    here. Those are deterministic, so they are persisted across daemon restarts:
    a plugin that cannot possibly start must not spawn a dying process on every
    boot. Runtime crashes keep the existing in-process three-strike rule, because
    those may be transient.
    """
    plugin_id: str = ""
    startup_failures: int = 0
    opens: int = 0
    open_until: float = 0.0
    last_error: str = ""
    last_ts: float = 0.0

    def is_open(self, now: Optional[float] = None) -> bool:
        return self.open_until > (now if now is not None else time.time())

    def cooldown_remaining_s(self) -> float:
        return max(0.0, round(self.open_until - time.time(), 1))

    def record_startup_failure(self, reason: str) -> bool:
        """Record one startup failure. Returns True if the breaker just opened."""
        self.startup_failures += 1
        self.last_error = reason
        self.last_ts = time.time()
        if self.startup_failures < STARTUP_FAIL_LIMIT:
            return False
        self.opens += 1
        cooldown = min(CIRCUIT_BASE_COOLDOWN_S * (2 ** (self.opens - 1)),
                       CIRCUIT_MAX_COOLDOWN_S)
        self.open_until = self.last_ts + cooldown
        return True

    def record_success(self) -> None:
        self.startup_failures = 0
        self.opens = 0
        self.open_until = 0.0
        self.last_error = ""
        self.last_ts = time.time()

    def dirty(self) -> bool:
        return bool(self.startup_failures or self.opens or self.open_until)

    def to_dict(self) -> Dict[str, Any]:
        return {"plugin": self.plugin_id, "startup_failures": self.startup_failures,
                "opens": self.opens, "open": self.is_open(),
                "cooldown_remaining_s": self.cooldown_remaining_s(),
                "last_error": self.last_error, "last_ts": self.last_ts}


@dataclass
class PluginStats:
    starts: int = 0
    restarts: int = 0
    crashes: int = 0
    timeouts: int = 0
    invocations: int = 0
    failures: int = 0
    last_error: str = ""
    last_invoke_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class PluginClient:
    """A supervised plugin host process."""

    def __init__(self, manifest: PluginManifest, directory: str | Path,
                 crash_limit: int = DEFAULT_CRASH_LIMIT, python: str | None = None,
                 repo_root: str | Path | None = None):
        self.manifest = manifest
        self.directory = Path(directory)
        self.crash_limit = crash_limit
        self.python = python or sys.executable
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        self.state = "stopped"            # stopped | running | crashed | disabled
        self.stats = PluginStats()
        self._process: Optional[subprocess.Popen] = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._seq = 0
        self._last_health: Dict[str, Any] = {}
        self._spawned_at = 0.0
        self._stderr: List[str] = []
        self._stderr_reader: Optional[threading.Thread] = None
        self.circuit = PluginCircuit(plugin_id=str(manifest.id))
        self._load_circuit()

    # --------------------------------------------------------- circuit ledger
    def _load_circuit(self) -> None:
        with _LEDGER_LOCK:
            data = _load_ledger().get(str(self.manifest.id)) or {}
        try:
            self.circuit.startup_failures = int(data.get("startup_failures", 0) or 0)
            self.circuit.opens = int(data.get("opens", 0) or 0)
            self.circuit.open_until = float(data.get("open_until", 0.0) or 0.0)
            self.circuit.last_ts = float(data.get("last_ts", 0.0) or 0.0)
            self.circuit.last_error = str(data.get("last_error", "") or "")
        except Exception:
            pass

    def _persist_circuit(self) -> None:
        with _LEDGER_LOCK:
            data = _load_ledger()
            data[str(self.manifest.id)] = self.circuit.to_dict()
            _save_ledger(data)

    def _record_startup_failure(self, reason: str) -> None:
        """Deterministic start failure: count it, and open the breaker if needed."""
        opened = self.circuit.record_startup_failure(reason)
        self._persist_circuit()
        if opened:
            self.state = "disabled"
            log.error("plugin %s circuit OPEN after %s startup failures (%s) - "
                      "no host process will be spawned for %.0fs",
                      self.manifest.id, self.circuit.startup_failures, reason,
                      self.circuit.cooldown_remaining_s())
        else:
            self.state = "crashed"
            log.warning("plugin %s failed to start (%s) - %s/%s before the circuit opens",
                        self.manifest.id, reason, self.circuit.startup_failures,
                        STARTUP_FAIL_LIMIT)

    def _last_stderr(self, limit: int = 600) -> str:
        """What the dying host actually said (bounded, never blocks)."""
        return "\n".join(self._stderr).strip()[-limit:]

    # ------------------------------------------------------------------ start
    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self.state == "disabled":
                return {"ok": False, "error": "plugin is disabled after repeated crashes",
                        "circuit": self.circuit.to_dict()}
            # Circuit breaker: a deterministic startup failure must never spawn
            # another doomed host process. This is the guard against turning a
            # broken plugin into a crash -> respawn loop.
            if self.circuit.is_open():
                self.state = "disabled"
                return {"ok": False,
                        "error": f"plugin {self.manifest.id} circuit open after repeated "
                                 f"startup failures ({self.circuit.last_error}); retry in "
                                 f"{self.circuit.cooldown_remaining_s():.0f}s",
                        "circuit": self.circuit.to_dict()}
            if self.state == "running" and self._process and self._process.poll() is None:
                return {"ok": True, "already_running": True}
            self._spawn()
            response = self._request("initialize", {}, timeout=STARTUP_TIMEOUT_S)
            if not response.ok:
                error = response.error or {}
                reason = error.get("message", "init failed")
                if (time.time() - self._spawned_at) <= FAST_FAIL_S:
                    # died before initialising: deterministic, never retried in a loop
                    noise = self._last_stderr()
                    if noise:
                        reason = f"{reason}: {noise.splitlines()[-1][:300]}"
                    self._record_startup_failure(reason)
                self._terminate()
                return {"ok": False, "error": reason, "detail": error,
                        "circuit": self.circuit.to_dict()}
            self.state = "running"
            self.stats.starts += 1
            if self.circuit.dirty():
                self.circuit.record_success()
                self._persist_circuit()
            log.info("plugin %s started (pid=%s)", self.manifest.id,
                     self._process.pid if self._process else "?")
            return {"ok": True, "result": response.result}

    def _spawn(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        creation = 0x08000000 if sys.platform.startswith("win") else 0   # CREATE_NO_WINDOW
        # Launch the host by SCRIPT PATH - never `python -m plugins.host`.
        # The packaged build runs an embeddable interpreter whose `._pth` file
        # isolates sys.path: PYTHONPATH and the cwd are IGNORED, so `-m` cannot
        # resolve the `plugins` package and every host exits instantly with
        # ModuleNotFoundError. Executing host.py by path lets it anchor sys.path
        # itself (see plugins/host.py) and works in both layouts.
        host_script = Path(__file__).with_name("host.py")
        self._stderr = []
        self._process = subprocess.Popen(
            [self.python, str(host_script), "--plugin", str(self.directory)],
            cwd=str(self.repo_root), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=creation)
        self._spawned_at = time.time()
        self._queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, name=f"plugin-{self.manifest.id}",
                                        daemon=True)
        self._reader.start()
        # Draining stderr matters twice: it diagnoses a dying host, and it stops
        # a chatty host from blocking forever on a full 64 KB pipe.
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name=f"plugin-err-{self.manifest.id}", daemon=True)
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self._queue.put(line)
        self._queue.put("")           # EOF marker

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                if len(self._stderr) < 200:
                    self._stderr.append(line.rstrip())
        except Exception:
            pass

    # -------------------------------------------------------------- requests
    def _request(self, method: str, params: Dict[str, Any], timeout: float) -> Response:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return Response("", False, None,
                                error={"code": "host_not_running",
                                       "message": "plugin host process is not running"})
            self._seq += 1
            request = Request(id=f"{self.manifest.id}-{self._seq}", method=method, params=params)
            try:
                assert self._process.stdin is not None
                self._process.stdin.write(request.to_json() + "\n")
                self._process.stdin.flush()
            except Exception as exc:
                return Response(request.id, False, None,
                                error={"code": "host_write_failed", "message": str(exc)})
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    line = self._queue.get(timeout=max(0.05, deadline - time.time()))
                except queue.Empty:
                    break
                if not line:                      # EOF: the host died
                    self._handle_death("plugin host exited")
                    return Response(request.id, False, None,
                                    error={"code": "plugin_crashed",
                                           "message": "plugin host exited unexpectedly"})
                try:
                    response = Response.from_json(line)
                except Exception:
                    continue                      # ignore malformed noise
                if response.id == request.id or response.id == "":
                    return response
                # responses to other/earlier requests are dropped
            self.stats.timeouts += 1
            self._handle_death(f"plugin timed out after {timeout:.1f}s")
            return Response(request.id, False, None,
                            error={"code": "plugin_timeout",
                                   "message": f"{method} exceeded {timeout:.1f}s"})

    def _handle_death(self, reason: str) -> None:
        """The host process died or hung: count it and disable if it keeps happening."""
        self.stats.last_error = reason
        self.stats.crashes += 1
        self._terminate()
        if self.stats.crashes >= self.crash_limit:
            self.state = "disabled"
            log.error("plugin %s disabled after %s failures (%s)",
                      self.manifest.id, self.stats.crashes, reason)
        else:
            self.state = "crashed"
            log.warning("plugin %s failed (%s) — %s/%s before disabling",
                        self.manifest.id, reason, self.stats.crashes, self.crash_limit)

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    # --------------------------------------------------------------- public API
    def invoke(self, capability: str, params: Dict[str, Any] | None = None,
               timeout_ms: int | None = None) -> Dict[str, Any]:
        """Invoke a capability. Always returns a dict with `ok` and structured errors."""
        if self.state == "disabled":
            return {"ok": False, "available": False, "error_code": "plugin_disabled",
                    "error": f"plugin {self.manifest.id} is disabled after repeated crashes"}
        if self.state != "running":
            started = self.start()
            if not started.get("ok"):
                return {"ok": False, "available": False, "error_code": "plugin_unavailable",
                        "error": started.get("error", "plugin not running"),
                        "detail": started.get("detail")}

        spec = self.manifest.spec_for(capability)
        timeout = (timeout_ms or (spec.timeout_ms if spec else 15_000)) / 1000.0
        started_at = time.time()
        response = self._request("invoke", {"capability": capability, "params": params or {}},
                                 timeout=timeout)
        self.stats.invocations += 1
        self.stats.last_invoke_ms = int((time.time() - started_at) * 1000)
        if response.ok:
            if isinstance(response.result, dict):
                result = dict(response.result)
                result.setdefault("plugin", self.manifest.id)
                result.setdefault("latency_ms", self.stats.last_invoke_ms)
                return result
            return {"ok": True, "value": response.result, "plugin": self.manifest.id}
        self.stats.failures += 1
        error = response.error or {}
        return {"ok": False, "available": error.get("code") not in ("unavailable", "plugin_unavailable"),
                "error_code": error.get("code", "plugin_error"),
                "error": error.get("message", "plugin invocation failed"),
                "detail": error.get("detail"), "capability": capability,
                "plugin": self.manifest.id, "latency_ms": self.stats.last_invoke_ms}

    def health(self, timeout_s: float = HEALTH_TIMEOUT_S) -> Dict[str, Any]:
        if self.state == "disabled":
            return {"ok": False, "available": False, "state": "disabled",
                    "detail": "disabled after repeated crashes"}
        if self.state != "running":
            started = self.start()
            if not started.get("ok"):
                return {"ok": False, "available": False, "state": self.state,
                        "detail": started.get("error", "not running")}
        response = self._request("health", {}, timeout=timeout_s)
        if not response.ok:
            error = response.error or {}
            if error.get("code") in ("plugin_timeout", "plugin_crashed", "host_not_running"):
                return {"ok": False, "available": False, "state": self.state,
                        "detail": error.get("message")}
            # The transport worked but the plugin answered negatively (e.g.
            # "no controller configured"). Relabelling that as "health check
            # failed" hides the real reason, so pass the adapter's answer through.
            result = response.result if isinstance(response.result, dict) else {}
            payload = {"ok": False, "available": bool(result.get("available", False)),
                       "state": self.state,
                       "detail": result.get("detail") or error.get("message")
                       or "health check failed"}
            for key, value in result.items():
                payload.setdefault(key, value)
            return payload
        self._last_health = response.result or {}
        return {"state": self.state, **self._last_health}

    def restart(self) -> Dict[str, Any]:
        with self._lock:
            self._terminate()
            if self.state != "disabled":
                self.state = "stopped"
            self.stats.restarts += 1
        if self.state == "disabled":
            return {"ok": False, "error": "plugin is disabled"}
        return self.start()

    def enable(self) -> Dict[str, Any]:
        with self._lock:
            self.state = "stopped"
            self.stats.crashes = 0
            self.circuit.record_success()   # explicit re-enable closes the breaker
            self._persist_circuit()
        return self.start()

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._request("shutdown", {}, timeout=5.0)
            self._terminate()
            if self.state != "disabled":
                self.state = "stopped"

    def status(self) -> Dict[str, Any]:
        running = bool(self._process and self._process.poll() is None)
        return {
            "plugin": self.manifest.id, "name": self.manifest.name,
            "version": self.manifest.version, "state": self.state, "running": running,
            "pid": self._process.pid if running and self._process else None,
            "capabilities": self.manifest.capability_names(),
            "permissions": self.manifest.permissions,
            "health": self._last_health or None,
            "stats": self.stats.to_dict(),
            "circuit": self.circuit.to_dict(),
        }
