"""Prime Kernel Launcher — starts the ACTUAL vendored Prime RLM kernel.

This module makes Prime LIVE by running the *preserved upstream kernel* as a real
subprocess and having GENIE act as its host:

    GENIE Mission
      -> Provider Gateway (GENIE authority; provider doubles are fine)
        -> integrations/prime_rlm.py  (the GENIE host)
          -> subprocess: python -m rlm.repl  (cwd = vendor/prime_rlm)
            -> upstream Prime RLM kernel
              -> host_request frames over stdin/stdout (JSON lines)
              -> GENIE answers host_reply frames

There is deliberately **no** synthetic `PrimeAdapter`. The previous revision imported
`integrations.prime_adapter`, a module that never existed; that path is removed. The real
integration is `integrations/prime_rlm.py` and the real runtime is `vendor/prime_rlm/rlm`.

Frame contract (verified against the vendored kernel):
    kernel -> host (stdout): {"event":"host_request","id":rid,"data":{...,"type":"rlm.find_models"}}
    host   -> kernel (stdin): {"type":"host_reply","id":rid,"data":{"status":"ok","result":{...}}}
    kernel startup frame   : {"event":"ready","protocol":3,"python":"3.13.x"}
    host -> kernel request : {"type":"execute","id":cell,"code":...} / {"type":"shutdown"}

Provider DOUBLES are used (no paid credentials, no network). The Prime process itself is real.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from integrations.prime_rlm import PrimeRlmRuntime, encode_frame

try:  # keep importable outside the app
    from core.contracts import ModelRequirement
except Exception:  # pragma: no cover
    ModelRequirement = Any


class KernelState(Enum):
    UNINITIALIZED = "uninitialized"
    CHECKING_DEPS = "checking_deps"
    PROVISIONING = "provisioning"
    READY = "ready"
    BLOCKED = "blocked"
    RUNNING = "running"
    FAILED = "failed"


@dataclass(frozen=True)
class KernelDepStatus:
    name: str
    available: bool
    version: Optional[str] = None
    blocker: Optional[str] = None


@dataclass(frozen=True)
class KernelLaunchResult:
    success: bool
    state: KernelState
    runtime: Any = None                  # the GENIE host: PrimeRlmRuntime
    process: Any = None                  # the real kernel subprocess (Popen)
    pid: Optional[int] = None
    startup_frame: Optional[str] = None  # the "ready" frame the real kernel emitted
    blockers: tuple = ()
    dependencies: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state.value,
            "pid": self.pid,
            "startup_frame": self.startup_frame,
            "blockers": list(self.blockers),
            "dependencies": [
                {"name": d.name, "available": d.available, "version": d.version}
                for d in self.dependencies
            ],
        }


# Required Python packages for the Prime kernel (its own deps).
_PRIME_DEPS = [
    ("mcp", "mcp"),
    ("tyro", "tyro"),
]


def _check_dep(module_name: str, pip_name: str) -> KernelDepStatus:
    try:
        import importlib
        mod = importlib.import_module(module_name)
        return KernelDepStatus(name=pip_name, available=True,
                               version=getattr(mod, "__version__", "unknown"))
    except ImportError:
        return KernelDepStatus(name=pip_name, available=False,
                               blocker=f"pip install {pip_name}")


def check_prime_dependencies() -> List[KernelDepStatus]:
    return [_check_dep(mod, pip) for mod, pip in _PRIME_DEPS]


def provision_prime_deps(python_exe: Optional[str] = None) -> List[KernelDepStatus]:
    if python_exe is None:
        python_exe = sys.executable
    results = []
    for module_name, pip_name in _PRIME_DEPS:
        status = _check_dep(module_name, pip_name)
        if not status.available:
            try:
                subprocess.run([python_exe, "-m", "pip", "install", pip_name],
                               capture_output=True, text=True, timeout=120)
                status = _check_dep(module_name, pip_name)
            except Exception:
                pass
        results.append(status)
    return results


class _DoubleSpec:
    """One deterministic provider/model selection (no credentials, no network)."""

    def __init__(self, provider_id: str, model_id: str, display_name: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.display_name = display_name


class DoubleGateway:
    """Deterministic provider doubles used for live-kernel proof.

    Resolves by *capability*, mirroring GENIE's Provider Gateway: different capability
    classes land on different provider/model families so multi-model fan-out is provable.
    """

    #: capability -> (provider, model, display name)
    TABLE = {
        "reasoning": ("double-alpha", "mock-reasoning-v1", "Double Reasoning"),
        "coding": ("double-beta", "mock-coding-v1", "Double Coding"),
        "vision": ("double-gamma", "mock-vision-v1", "Double Vision"),
        "fast": ("double-delta", "mock-fast-v1", "Double Fast"),
    }

    def candidates(self, req: Any):
        capability = getattr(req, "capability", "") or "reasoning"
        provider_id, model_id, display = self.TABLE.get(
            capability, ("double-alpha", "mock-reasoning-v1", "Double Reasoning"))
        return [_DoubleSpec(provider_id, model_id, display)]


def kernel_reply_frame(host_reply: Dict[str, Any]) -> Dict[str, Any]:
    """Translate GENIE's host reply into the frame shape the kernel expects on stdin.

    GENIE host produces: {"event":"host_reply","id":rid,"status":"ok","result":{...}}
    The kernel consumes : {"type":"host_reply","id":rid,"data":{"status":"ok","result":{...}}}
    """
    if host_reply.get("status") == "ok":
        data = {"status": "ok", "result": host_reply.get("result")}
    else:
        data = {"status": "error", "error": host_reply.get("error")}
    return {"type": "host_reply", "id": host_reply.get("id"), "data": data}


class PrimeKernelLauncher:
    """Launches the ACTUAL vendored Prime RLM kernel with GENIE as host."""

    def __init__(self, auto_provision: bool = True, gateway: Any = None):
        self._state = KernelState.UNINITIALIZED
        self._auto_provision = auto_provision
        self._gateway = gateway if gateway is not None else DoubleGateway()
        self._deps: List[KernelDepStatus] = []
        self._blockers: List[str] = []
        self._runtime: Optional[PrimeRlmRuntime] = None
        self._proc: Optional[subprocess.Popen] = None

    @property
    def state(self) -> KernelState:
        return self._state

    @property
    def runtime(self) -> Optional[PrimeRlmRuntime]:
        return self._runtime

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._proc

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    def audit(self) -> List[KernelDepStatus]:
        self._state = KernelState.CHECKING_DEPS
        self._deps = check_prime_dependencies()
        self._blockers = [d.blocker for d in self._deps if not d.available and d.blocker]
        self._state = KernelState.BLOCKED if self._blockers else KernelState.READY
        return self._deps

    def launch(self) -> KernelLaunchResult:
        """Start the real kernel subprocess and complete the startup handshake."""
        self.audit()

        if self._auto_provision and self._state == KernelState.BLOCKED:
            self._state = KernelState.PROVISIONING
            self._deps = provision_prime_deps()
            self._blockers = [d.blocker for d in self._deps if not d.available and d.blocker]
            if not self._blockers:
                self._state = KernelState.READY

        if self._state == KernelState.BLOCKED:
            return KernelLaunchResult(
                success=False, state=KernelState.BLOCKED,
                blockers=tuple(self._blockers), dependencies=tuple(self._deps))

        try:
            self._runtime = PrimeRlmRuntime(gateway=self._gateway)
            if not self._runtime.available():
                self._state = KernelState.FAILED
                return KernelLaunchResult(
                    success=False, state=KernelState.FAILED,
                    blockers=("vendored Prime RLM kernel is not present on disk",),
                    dependencies=tuple(self._deps))

            self._proc = self._runtime.spawn_kernel()
            raw = self._proc.stdout.readline()
            startup = raw.decode(errors="replace").strip() if raw else ""

            # The ready frame is the kernel proving it started and speaks the protocol.
            frame = json.loads(startup) if startup else {}
            if frame.get("event") != "ready":
                self._state = KernelState.FAILED
                return KernelLaunchResult(
                    success=False, state=KernelState.FAILED,
                    pid=self.pid, startup_frame=startup or None,
                    blockers=(f"kernel did not emit a ready frame: {startup[:200]!r}",),
                    dependencies=tuple(self._deps))

            self._state = KernelState.RUNNING
            return KernelLaunchResult(
                success=True, state=KernelState.RUNNING, runtime=self._runtime,
                process=self._proc, pid=self.pid, startup_frame=startup,
                dependencies=tuple(self._deps))
        except Exception as exc:
            self._state = KernelState.FAILED
            return KernelLaunchResult(
                success=False, state=KernelState.FAILED,
                blockers=(f"Launch error: {exc}",), dependencies=tuple(self._deps))

    # ------------------------------------------------------------------ drive
    def execute(self, code: str, cell_id: str = "cell-1",
                timeout_s: float = 25.0) -> Dict[str, Any]:
        """Run one cell inside the REAL kernel, answering its host_requests as GENIE."""
        if self._proc is None or self._runtime is None:
            return {"ok": False, "error": "kernel is not running"}
        proc, runtime = self._proc, self._runtime

        proc.stdin.write(encode_frame({"type": "execute", "id": cell_id, "code": code}))
        proc.stdin.flush()

        events: List[Dict[str, Any]] = []
        stdout_text: List[str] = []
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            raw = proc.stdout.readline()
            if not raw:
                break
            raw = raw.decode(errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            events.append(ev)
            if ev.get("event") == "host_request":
                reply = runtime.handle_frame(ev)      # GENIE answers (gateway authority)
                proc.stdin.write(encode_frame(kernel_reply_frame(reply)))
                proc.stdin.flush()
            elif ev.get("event") == "stdout":
                stdout_text.append(ev.get("text") or "")
            elif ev.get("event") == "done" and ev.get("id") == cell_id:
                break
        return {"ok": True, "events": events, "stdout": "".join(stdout_text)}

    def shutdown(self) -> None:
        """Close stdin; the kernel exits on EOF (verified upstream behaviour)."""
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=15)
        except Exception:
            self._proc.kill()
            try:
                self._proc.wait(timeout=10)
            except Exception:
                pass
        self._proc = None
        self._state = KernelState.READY

    # ----------------------------------------------------------- live proof
    def prove_round_trip(self) -> Dict[str, Any]:
        """Drive the REAL kernel: one mission, two children, two gateway selections.

        Child A requests `reasoning.strong`, child B requests `coding.strong`; GENIE's
        gateway resolves each to a distinct provider/model, both inside the real kernel.
        """
        result = self.launch()
        if not result.success:
            return {"proof": "failed", "reason": "kernel launch failed",
                    "details": result.to_dict()}
        try:
            code = (
                "import rlm\n"
                "a = await rlm.find_models('reasoning.strong')\n"
                "b = await rlm.find_models('coding.strong')\n"
                "print('CHILD_A', [m.selector for m in a])\n"
                "print('CHILD_B', [m.selector for m in b])\n"
            )
            run = self.execute(code, cell_id="proof-1")
            text = run.get("stdout", "")
            selectors: Dict[str, List[str]] = {}
            for line in text.splitlines():
                body = line.strip()
                if body.startswith("CHILD_A"):
                    selectors["child_a"] = json.loads(
                        body[len("CHILD_A"):].strip().replace("'", '"'))
                elif body.startswith("CHILD_B"):
                    selectors["child_b"] = json.loads(
                        body[len("CHILD_B"):].strip().replace("'", '"'))
            distinct = bool(
                selectors.get("child_a") and selectors.get("child_b")
                and selectors["child_a"] != selectors["child_b"])
            return {
                "kernel_pid": result.pid,
                "startup_frame": result.startup_frame,
                "kernel_stdout": text.strip(),
                "gateway_selection_a": selectors.get("child_a"),
                "gateway_selection_b": selectors.get("child_b"),
                "distinct_selections": distinct,
                "round_trip_complete": bool(
                    selectors.get("child_a") and selectors.get("child_b")),
            }
        finally:
            self.shutdown()
