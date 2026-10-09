"""Prime Agent RLM runtime provider (`integrations/prime_rlm.py`).

ONE GENIE AUTHORITY, MULTIPLE SPECIALIST RUNTIMES.

Prime Agent's RLM (Recursive Language Model) runtime is **preserved**, not rewritten: the original
kernel is vendored unmodified at `vendor/prime_rlm/rlm` (MIT (c) Prime Intellect / Mario Zechner) and
GENIE **hosts** it. The kernel is a subprocess that speaks JSON-lines frames: skill code inside it
calls `rlm.spawn(...)` / `rlm.find_models(...)` and the kernel emits
``{"event":"host_request","id":rid,"data":{...}}``; the host answers with
``{"event":"host_reply","id":rid,...}``. GENIE is that host.

**Prime does not become a second model island.** In real Prime the host owns model management, so by
being the host GENIE keeps it: every child model is resolved from a *capability requirement* through
GENIE's own Provider Gateway (`models/gateway.Gateway.candidates`). Prime never sees credentials,
never chooses a provider, and cannot self-grant a model outside GENIE's candidate list.

Multi-model fan-out: one Prime-style mission declares children with capability classes
(`reasoning.strong`, `coding.strong`, `reviewer.independent`, `cheap.bulk`, ...) and GENIE resolves
each to a concrete `provider/model` selector, so one mission can span different model families.

Classification: ORIGINAL RUNTIME **PRESERVED** (vendored unmodified) + **WRAPPED** (GENIE is host).
Live kernel execution needs the vendored kernel's own deps (mcp, tyro) -> external_optional.
Deterministic proof uses the real host logic against provider doubles (no credentials, no network).
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.contracts import ModelRequirement

try:  # keep importable outside the app
    from core.logging_setup import get_logger
    log = get_logger("integrations.prime_rlm")
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("integrations.prime_rlm")

#: Root of the preserved, unmodified upstream runtime.
VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor" / "prime_rlm"
KERNEL_PACKAGE = VENDOR_DIR / "rlm"

RUNTIME_ID = "prime_rlm"


class PrimeRlmUnavailable(Exception):
    """Raised when the runtime or its authority dependency is missing."""


def kernel_module() -> str:
    """The preserved kernel entrypoint (`python -m rlm.repl`)."""
    return "rlm.repl"


def kernel_available() -> bool:
    """True when the preserved upstream kernel is vendored on disk."""
    return (KERNEL_PACKAGE / "repl.py").is_file()


# --------------------------------------------------------------------------- #
# capability classes -> GENIE ModelRequirement
# --------------------------------------------------------------------------- #
# The brief's capability vocabulary, mapped onto GENIE's ModelRequirement
# (capability + min_quality + context/tool/vision needs). Never a provider name.
CAPABILITY_TABLE: Dict[str, Dict[str, Any]] = {
    "reasoning.strong": {"capability": "reasoning", "min_quality": "strong"},
    "coding.strong": {"capability": "coding", "min_quality": "strong", "needs_tools": True},
    "coding.fast": {"capability": "coding", "min_quality": "medium", "needs_tools": True},
    "research.long_context": {"capability": "reasoning", "min_quality": "medium",
                              "max_input_tokens": 200_000},
    "large_context": {"capability": "reasoning", "min_quality": "medium",
                      "max_input_tokens": 200_000},
    "reviewer.independent": {"capability": "reasoning", "min_quality": "strong"},
    "multimodal": {"capability": "vision", "min_quality": "medium", "needs_vision": True},
    "cheap.bulk": {"capability": "fast", "min_quality": "low"},
}

DEFAULT_CAPABILITY = {"capability": "reasoning", "min_quality": "medium"}


def requirement_for(capability: str, **overrides: Any) -> ModelRequirement:
    """Build a GENIE ModelRequirement from a capability class (never a model name)."""
    base = dict(CAPABILITY_TABLE.get(capability, DEFAULT_CAPABILITY))
    base.update(overrides)
    return ModelRequirement(**base)


@dataclass(frozen=True)
class RLMModel:
    """Model as exposed to the kernel: GENIE-chosen, never kernel-chosen."""
    provider: str
    id: str
    name: str
    selector: str

    def to_dict(self) -> Dict[str, Any]:
        return {"provider": self.provider, "id": self.id,
                "name": self.name, "selector": self.selector}


@dataclass
class ChildSpec:
    """One declared child of a Prime-style mission."""
    name: str
    prompt: str
    capability: str = "reasoning.strong"
    model: Optional[str] = None      # explicit "provider/model" selector (still validated by GENIE)
    thinking: Optional[str] = None


@dataclass
class RlmChild:
    rlm_child_id: str
    name: str
    capability: str
    selector: str
    session_dir: Path
    status: str = "running"
    answer_preview: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"rlm_child_id": self.rlm_child_id, "name": self.name,
                "capability": self.capability, "selector": self.selector,
                "session_dir": str(self.session_dir), "status": self.status,
                "answer_preview": self.answer_preview, "error": self.error,
                "duration_ms": self.duration_ms}


# --------------------------------------------------------------------------- #
# the runtime / host
# --------------------------------------------------------------------------- #
class PrimeRlmRuntime:
    """GENIE-side host for the preserved Prime RLM kernel.

    Authority: model selection goes through GENIE's Provider Gateway. Without a gateway the
    runtime refuses to resolve models rather than inventing one.
    """

    id = RUNTIME_ID

    def __init__(self, gateway=None, *, session_root: Optional[Path] = None):
        self.gateway = gateway
        self.session_root = Path(session_root) if session_root else \
            Path(VENDOR_DIR) / "sessions"
        self._children: Dict[str, RlmChild] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    # ---------------------------------------------------------------- status
    def available(self) -> bool:
        """The preserved kernel is on disk (live spawn additionally needs its deps)."""
        return kernel_available()

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "kernel_present": kernel_available(),
                "kernel_path": str(KERNEL_PACKAGE),
                "kernel_module": kernel_module(),
                "gateway_bound": self.gateway is not None,
                "children": len(self._children), "sessions": len(self._sessions),
                "authority": "GENIE Provider Gateway resolves all child models"}

    # ------------------------------------------------------- model authority
    def models_for(self, capability: str, limit: int = 8) -> List[RLMModel]:
        """GENIE-resolved models for a capability class."""
        if self.gateway is None:
            raise PrimeRlmUnavailable(
                "no GENIE Provider Gateway bound — Prime must not select models itself")
        req = requirement_for(capability)
        specs = list(self.gateway.candidates(req))[: max(1, int(limit))]
        return [RLMModel(provider=s.provider_id, id=s.model_id,
                         name=s.display_name,
                         selector=f"{s.provider_id}/{s.model_id}") for s in specs]

    def resolve_selector(self, capability: str, explicit: Optional[str] = None) -> str:
        """Resolve one child's `provider/model` selector via GENIE.

        An explicit selector is honoured only if GENIE offers it — a runtime cannot self-grant
        a model outside the gateway's candidate list.
        """
        offered = self.models_for(capability)
        if not offered:
            raise PrimeRlmUnavailable(f"no GENIE model available for capability {capability!r}")
        if explicit:
            for m in offered:
                if m.selector == explicit or m.id == explicit:
                    return m.selector
            raise PrimeRlmUnavailable(
                f"model {explicit!r} is not offered by GENIE for {capability!r}")
        return offered[0].selector

    # ---------------------------------------------------------------- fan-out
    def fanout(self, children: List[ChildSpec]) -> List[RlmChild]:
        """Create one child per spec, each with a GENIE-resolved model."""
        out: List[RlmChild] = []
        for spec in children:
            out.append(self._create_child(spec))
        return out

    def _create_child(self, spec: ChildSpec) -> RlmChild:
        selector = self.resolve_selector(spec.capability, spec.model)
        child = RlmChild(
            rlm_child_id=f"child-{uuid.uuid4().hex[:12]}",
            name=spec.name, capability=spec.capability, selector=selector,
            session_dir=self.session_root / spec.name,
        )
        self._children[child.rlm_child_id] = child
        return child

    # ------------------------------------------------------- host protocol
    def handle_frame(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle one kernel frame; return the reply frame (or None if none is due)."""
        if not isinstance(frame, dict) or frame.get("event") != "host_request":
            return None
        rid = frame.get("id")
        data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
        if not isinstance(rid, str):
            return None
        try:
            result = self._dispatch(data)
        except Exception as exc:                      # never fabricate a success
            return {"event": "host_reply", "id": rid, "status": "error", "error": str(exc)}
        return {"event": "host_reply", "id": rid, "status": "ok", "result": result}

    def _dispatch(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rtype = data.get("type")
        if rtype == "rlm.run":
            return self._host_run(data)
        if rtype == "rlm.create_session":
            return self._host_create_session(data)
        if rtype == "rlm.find_models":
            return self._host_find_models(data)
        if rtype == "rlm.list_subagents":
            return {"subagents": [c.to_dict() for c in self._children.values()]}
        if rtype == "rlm.collect":
            return self._host_collect(data)
        raise RuntimeError(f"unsupported host request type: {rtype!r}")

    def _host_run(self, data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = data.get("prompt") or ""
        kwargs = data.get("kwargs") if isinstance(data.get("kwargs"), dict) else {}
        name = kwargs.get("name") or f"child-{uuid.uuid4().hex[:6]}"
        capability = kwargs.get("capability") or "reasoning.strong"
        child = self._create_child(ChildSpec(
            name=name, prompt=str(prompt), capability=capability,
            model=kwargs.get("model"), thinking=kwargs.get("thinking")))
        return {"rlm_child_id": child.rlm_child_id, "name": child.name,
                "session_dir": str(child.session_dir), "model": child.selector}

    def _host_create_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        kwargs = data.get("kwargs") if isinstance(data.get("kwargs"), dict) else {}
        capability = kwargs.get("capability") or "reasoning.strong"
        selector = self.resolve_selector(capability, kwargs.get("model"))
        name = kwargs.get("name") or f"session-{uuid.uuid4().hex[:6]}"
        session_id = f"ses-{uuid.uuid4().hex[:12]}"
        session_file = self.session_root / f"{name}.jsonl"
        self._sessions[session_id] = {"name": name, "selector": selector,
                                      "session_file": str(session_file)}
        return {"active_session_id": session_id, "session_id": session_id, "name": name,
                "session_file": str(session_file), "model": selector}

    def _host_find_models(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # The upstream kernel sends the capability in `query` ("reasoning.strong"),
        # while GENIE's own field name is `capability`. Honour both, otherwise every
        # upstream find_models() call would collapse onto the same default model and
        # multi-model fan-out could never be demonstrated.
        capability = data.get("capability") or data.get("query") or "reasoning.strong"
        limit = int(data.get("limit") or 8)
        return {"models": [m.to_dict() for m in self.models_for(capability, limit)]}

    def _host_collect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        target = data.get("target")
        child = None
        if isinstance(target, str):
            child = self._children.get(target) or next(
                (c for c in self._children.values() if c.name == target), None)
        if child is None:
            raise RuntimeError(f"unknown collect target: {target!r}")
        return {"rlm_child_id": child.rlm_child_id, "session_name": child.name,
                "session_dir": str(child.session_dir), "status": child.status,
                "settled": child.status in ("completed", "error"),
                "answer_preview": child.answer_preview, "error": child.error,
                "duration_ms": child.duration_ms, "tool_use_count": 0,
                "replied_since_task": False}

    # ------------------------------------------------------ live kernel (opt)
    def kernel_command(self) -> List[str]:
        """Command to launch the preserved kernel (external_optional: needs mcp/tyro)."""
        return [sys.executable, "-m", kernel_module()]

    def spawn_kernel(self) -> subprocess.Popen:
        """Launch the vendored kernel as a subprocess speaking the frame protocol."""
        if not kernel_available():
            raise PrimeRlmUnavailable("preserved Prime RLM kernel is not vendored")
        return subprocess.Popen(
            self.kernel_command(), cwd=str(VENDOR_DIR),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def encode_frame(event: Dict[str, Any]) -> bytes:
    """One protocol frame: compact JSON + newline (matches upstream `_send`)."""
    return (json.dumps(event, separators=(",", ":")) + "\n").encode()
