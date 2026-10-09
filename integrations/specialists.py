"""Specialist engine adapters (integrations/specialists.py) — Phase 11B.

GENIE owns identity, memory, missions, routing, permissions and agent coordination.
Specialist engines keep their own mature core and are reachable through one adapter contract.

Design rule from the audit: **wrap, do not rewrite**. ComfyUI's node engine, n8n's workflow
engine and Kronos's model are the reason those projects are good — GENIE does not clone them,
it drives them. Conversely, capabilities GENIE demonstrably implements better (browser,
orchestration, skills) are NOT wrapped here.

Every adapter answers three questions honestly:
    available()     — is the engine actually reachable right now?
    invoke()        — do the work (or explain precisely why it cannot be done)
    conformance()   — self-check report; "pending-live-acceptance" when the engine is absent

An absent engine is never reported as success, and never as a crash.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.logging_setup import get_logger

log = get_logger("integrations.specialists")


@dataclass(frozen=True)
class OperationSpec:
    """A repo-specific operation, expressed in the ENGINE's own terms.

    This is what makes an adapter a capability rather than a generic proxy:
    the operations, their endpoints and their required parameters are declared
    from the real upstream API, so GENIE can validate a request before it ever
    touches the wire and can report precisely what is missing.
    """

    name: str
    path: str
    method: str = "POST"
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    summary: str = ""


@dataclass
class OperationResult:
    ok: bool
    operation: str
    error: str = ""
    missing: List[str] = field(default_factory=list)
    accepted: List[str] = field(default_factory=list)


class SpecialistAdapter:
    """One contract for every external specialist engine."""

    #: canonical capability id (see docs/REPO_UTILIZATION_AUDIT.md capability matrix)
    capability: str = ""
    #: which supplied repository this wraps
    source_repo: str = ""
    name: str = ""
    default_base_url: str = ""
    #: what the engine must be asked to do, in its own terms
    operation: str = ""
    #: repo-specific operations (see OperationSpec). Empty = generic proxy only.
    operations: Dict[str, OperationSpec] = {}

    def __init__(self, base_url: Optional[str] = None, *, timeout: float = 2.0,
                 enabled: bool = True):
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self.enabled = enabled

    # ------------------------------------------------------------------ http
    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                try:
                    return {"ok": True, "status": resp.status, "data": json.loads(body or "{}")}
                except ValueError:
                    return {"ok": True, "status": resp.status, "data": {"raw": body[:2000]}}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
        except urllib.error.URLError as exc:
            return {"ok": False, "status": 0, "error": f"unreachable: {exc.reason}"}
        except OSError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}

    # ------------------------------------------------------------- contract
    def available(self) -> Dict[str, Any]:
        if not self.base_url:
            # no endpoint configured — report it, never crash and never claim success
            return {"capability": self.capability, "engine": self.name, "available": False,
                    "base_url": "", "detail": "no endpoint configured"}
        probe = self._request("GET", self.health_path)
        return {"capability": self.capability, "engine": self.name,
                "available": bool(probe.get("ok")), "base_url": self.base_url,
                "detail": probe.get("error", "") if not probe.get("ok") else "reachable"}

    def invoke(self, requirement: str, params: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
        """Do the work. Never fabricates success when the engine is absent."""
        if not self.enabled:
            return {"ok": False, "capability": self.capability,
                    "error": f"{self.name} adapter is disabled"}
        if not self.base_url:
            return {"ok": False, "capability": self.capability,
                    "error": f"{self.name} has no configured endpoint",
                    "status": "pending-live-acceptance"}
        return self._request("POST", self.invoke_path,
                             {"requirement": requirement, **(params or {})})

    def conformance(self) -> Dict[str, Any]:
        """Self-check. Reports `pending-live-acceptance` when hardware/credentials are absent.

        When the engine IS reachable, the health response is also checked against
        this engine's expected shape, so "reachable" and "actually this engine"
        are reported separately rather than being conflated.
        """
        probe = self.available()
        state = "ready" if probe["available"] else "pending-live-acceptance"
        shape_ok: Optional[bool] = None
        if probe["available"]:
            live = self._request("GET", self.health_path)
            shape_ok = bool(live.get("ok")) and self.health_shape_ok(live.get("data"))
            if not shape_ok:
                state = "shape-mismatch"
        return {"capability": self.capability, "engine": self.name,
                "source_repo": self.source_repo, "base_url": self.base_url,
                "state": state, "available": probe["available"],
                "health_shape_ok": shape_ok,
                "operations": self.supported_operations(),
                "operation": self.operation, "detail": probe.get("detail", "")}

    def describe(self) -> Dict[str, Any]:
        return {"capability": self.capability, "engine": self.name,
                "source_repo": self.source_repo, "endpoint": self.base_url,
                "operation": self.operation}

    # --------------------------------------------- repo-specific operations
    def supported_operations(self) -> List[str]:
        return sorted(self.operations)

    def validate_operation(self, operation: str,
                           params: Optional[Dict[str, Any]] = None) -> OperationResult:
        """Check a request against the engine's real contract before sending it.

        An unknown operation is refused rather than forwarded — a generic proxy
        that forwards anything is not a capability.
        """
        spec = self.operations.get(operation)
        params = params or {}
        if spec is None:
            return OperationResult(
                ok=False, operation=operation,
                error=f"{self.name} does not support operation {operation!r}; "
                      f"supported: {self.supported_operations()}")
        # Presence-based, not truthiness-based: a horizon of 0 or a batch size
        # of 0 is a real value and must not be reported as missing. Only None
        # and blank strings count as absent.
        def _absent(value: Any) -> bool:
            if value is None:
                return True
            return isinstance(value, str) and not value.strip()

        missing = [k for k in spec.required if _absent(params.get(k))]
        accepted = sorted(set(params) & set(spec.required) | set(spec.optional))
        if missing:
            return OperationResult(ok=False, operation=operation,
                                   error=f"missing required parameter(s): {missing}",
                                   missing=missing, accepted=accepted)
        unknown = sorted(set(params) - set(spec.required) - set(spec.optional))
        return OperationResult(ok=True, operation=operation, accepted=accepted,
                               error=("ignored unknown parameter(s): " + str(unknown))
                               if unknown else "")

    def health_shape_ok(self, data: Any) -> bool:
        """Does the health response actually look like this engine?

        Overridden per engine. Default True: an adapter without a known shape
        must not claim a mismatch it cannot justify.
        """
        return True

    # -------------------------------------------------------------- endpoints
    @property
    def health_path(self) -> str:
        return "/healthz"

    @property
    def invoke_path(self) -> str:
        return "/invoke"


class N8nAdapter(SpecialistAdapter):
    """n8n — durable workflow automation + 1500+ service connectors (external service)."""

    capability = "workflow.automation"
    source_repo = "n8n"
    name = "n8n"
    default_base_url = "http://127.0.0.1:5678"
    operation = "trigger/run a workflow by id or name"

    @property
    def health_path(self) -> str:
        return "/healthz"

    @property
    def invoke_path(self) -> str:
        return "/api/v1/workflows/run"

    def invoke(self, requirement, params=None):
        params = dict(params or {})
        workflow = params.pop("workflow_id", "") or params.pop("workflow", "")
        if not workflow:
            return {"ok": False, "capability": self.capability,
                    "error": "n8n needs a workflow_id (or workflow name)"}
        return super().invoke(requirement, {"workflow_id": workflow, **params})

    # n8n public REST API (n8n-master): /api/v1/workflows, /api/v1/executions
    operations = {
        "run_workflow": OperationSpec(
            name="run_workflow", path="/api/v1/workflows/run", method="POST",
            required=("workflow_id",), optional=("payload", "options"),
            summary="Execute an existing workflow by id"),
        "list_workflows": OperationSpec(
            name="list_workflows", path="/api/v1/workflows", method="GET",
            summary="List available workflows"),
        "list_executions": OperationSpec(
            name="list_executions", path="/api/v1/executions", method="GET",
            optional=("workflow_id", "limit"),
            summary="Inspect execution history"),
    }

    def health_shape_ok(self, data: Any) -> bool:
        # n8n /healthz answers {"status":"ok"} when healthy
        return isinstance(data, dict) and str(data.get("status", "")).lower() == "ok"


class ComfyUIAdapter(SpecialistAdapter):
    """ComfyUI — image/video generation. The node engine is the value: wrapped, never cloned."""

    capability = "media.image_video"
    source_repo = "ComfyUI"
    name = "ComfyUI"
    default_base_url = "http://127.0.0.1:8188"
    operation = "queue a prompt/workflow and return generated media"

    @property
    def health_path(self) -> str:
        return "/system_stats"

    @property
    def invoke_path(self) -> str:
        return "/prompt"

    # ComfyUI server API: /prompt (queue), /history/{id}, /view, /object_info
    operations = {
        "queue_prompt": OperationSpec(
            name="queue_prompt", path="/prompt", method="POST",
            required=("prompt",), optional=("client_id", "number"),
            summary="Queue a node-graph prompt and return prompt_id"),
        "history": OperationSpec(
            name="history", path="/history", method="GET",
            optional=("prompt_id",), summary="Read generation outputs"),
        "object_info": OperationSpec(
            name="object_info", path="/object_info", method="GET",
            optional=("node",), summary="Discover available nodes"),
    }

    def health_shape_ok(self, data: Any) -> bool:
        # ComfyUI /system_stats always carries a 'system' block
        return isinstance(data, dict) and "system" in data


class HeyGemAdapter(SpecialistAdapter):
    """HeyGem.ai — digital human / avatar video (external service, GPU-pending)."""

    capability = "avatar.digital_human"
    source_repo = "HeyGem.ai"
    name = "HeyGem"
    default_base_url = "http://127.0.0.1:8383"
    operation = "synthesise an avatar video from audio + a reference avatar"

    @property
    def health_path(self) -> str:
        return "/health"

    @property
    def invoke_path(self) -> str:
        return "/easy/submit"

    # HeyGem.ai: /easy/submit (avatar video), /v1/preview, /v1/play
    operations = {
        "submit_avatar_video": OperationSpec(
            name="submit_avatar_video", path="/easy/submit", method="POST",
            required=("audio_path", "avatar_id"),
            optional=("output_dir", "watermark"),
            summary="Render an avatar video from audio + reference avatar"),
    }


class KronosAdapter(SpecialistAdapter):
    """Kronos — financial candlestick (K-line) foundation model forecasting."""

    capability = "finance.timeseries"
    source_repo = "Kronos"
    name = "Kronos"
    default_base_url = "http://127.0.0.1:8800"
    operation = "forecast K-line series for a symbol/horizon"

    @property
    def health_path(self) -> str:
        return "/health"

    @property
    def invoke_path(self) -> str:
        return "/predict"

    # Kronos: /predict with K-line context; forecasting is numeric and must be
    # calibrated — never a narrative dressed up as a probability.
    operations = {
        "predict": OperationSpec(
            name="predict", path="/predict", method="POST",
            required=("symbol", "horizon"),
            optional=("context", "interval", "model"),
            summary="Forecast a K-line series for a symbol and horizon"),
    }

    def health_shape_ok(self, data: Any) -> bool:
        return isinstance(data, dict) and (
            "status" in data or "model" in data or "ok" in data)


class MiroFishAdapter(SpecialistAdapter):
    """MiroFish — swarm/social simulation producing prediction reports + a digital world."""

    capability = "simulation.social"
    source_repo = "MiroFish"
    name = "MiroFish"
    default_base_url = "http://127.0.0.1:5173"
    operation = "run a swarm simulation from seed material and return a prediction report"

    @property
    def invoke_path(self) -> str:
        return "/api/simulate"

    # MiroFish: seed -> scenario -> simulation -> report. Outputs are narrative;
    # a numeric probability requires calibration, never a narrative.
    operations = {
        "submit_scenario": OperationSpec(
            name="submit_scenario", path="/api/simulate", method="POST",
            required=("scenario",), optional=("seed_material", "rounds"),
            summary="Run a swarm simulation from a scenario"),
        "job_status": OperationSpec(
            name="job_status", path="/api/simulate/status", method="GET",
            required=("job_id",), summary="Poll simulation progress"),
        "job_report": OperationSpec(
            name="job_report", path="/api/simulate/report", method="GET",
            required=("job_id",), summary="Fetch the prediction report"),
    }


class OmniVoiceAdapter(SpecialistAdapter):
    """OmniVoice — specialised multilingual voice synthesis (optional specialist)."""

    capability = "voice.specialized"
    source_repo = "OmniVoice"
    name = "OmniVoice"
    default_base_url = "http://127.0.0.1:8901"
    operation = "synthesise speech in a requested language/voice"

    @property
    def invoke_path(self) -> str:
        return "/tts"

    operations = {
        "synthesize": OperationSpec(
            name="synthesize", path="/tts", method="POST",
            required=("text",), optional=("language", "voice", "speaker"),
            summary="Synthesise speech for text in a language/voice"),
    }


class DecepticonAdapter(SpecialistAdapter):
    """Decepticon — security assessment environment. Gated: only on explicit owner authorisation."""

    capability = "security.assessment_env"
    source_repo = "Decepticon"
    name = "Decepticon"
    default_base_url = ""
    operation = "run an authorised adversarial assessment"

    def invoke(self, requirement, params=None):
        if not (params or {}).get("authorised"):
            return {"ok": False, "capability": self.capability,
                    "error": "security assessment requires explicit owner authorisation "
                             "(authorised=true)",
                    "status": "gated"}
        return super().invoke(requirement, params)

    # Gated by design: every operation requires explicit authorisation.
    # `authorised` is deliberately NOT in `required`: it is enforced by the
    # override below so the refusal carries a security-specific message rather
    # than a generic "missing parameter" (and so the gate cannot be satisfied
    # by accident).
    operations = {
        "assess": OperationSpec(
            name="assess", path="/assess", method="POST",
            required=("scope",),
            optional=("targets", "depth", "authorised"),
            summary="Run an authorised adversarial assessment inside a declared scope"),
    }

    def validate_operation(self, operation, params=None):
        result = super().validate_operation(operation, params)
        # authorisation is checked before anything else, always
        if result.ok and not (params or {}).get("authorised"):
            return OperationResult(ok=False, operation=operation,
                                   error="security assessment requires explicit owner "
                                         "authorisation (authorised=true)")
        return result


#: canonical registry — the capability matrix as code
ADAPTERS: List[SpecialistAdapter] = [
    N8nAdapter(), ComfyUIAdapter(), HeyGemAdapter(), KronosAdapter(),
    MiroFishAdapter(), OmniVoiceAdapter(), DecepticonAdapter(),
]

_BY_CAPABILITY: Dict[str, SpecialistAdapter] = {a.capability: a for a in ADAPTERS}


def get_adapter(capability: str) -> Optional[SpecialistAdapter]:
    return _BY_CAPABILITY.get(capability)


def capabilities() -> List[Dict[str, Any]]:
    return [a.describe() for a in ADAPTERS]


def capability_matrix() -> List[Dict[str, Any]]:
    """Live conformance of every specialist capability — nothing is invisible."""
    return [a.conformance() for a in ADAPTERS]


def invoke(capability: str, requirement: str, params: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    adapter = get_adapter(capability)
    if adapter is None:
        return {"ok": False, "error": f"no specialist adapter for capability '{capability}'",
                "known": sorted(_BY_CAPABILITY)}
    return adapter.invoke(requirement, params)
