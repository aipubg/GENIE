"""NEDLE2 local director (director/nedle2) — the REAL Cactus Needle 2 runtime.

Model/engine: `Cactus-Compute/needle2` (official), via the official `cactus-needle`
Python package (module `needle`) and the official native engine (libneedle.dll).

Integration contract (mirrors the official harness exactly):
    * one engine instance, `reset()` before every independent request
    * read `function_calls`, `validation`, `confidence` from the response
    * ignore calls flagged `ungrounded` or `negation`
    * act on a call only at/above the confidence threshold — otherwise treat it as a
      refusal and escalate classification to a remote model
    * a call is a ROUTING DECISION only. NEDLE2 never executes and never claims a task
      is complete — services own truth (master spec §2.2)

Everything Needle-specific lives in this module + `director/tools.py`. Nothing else in
GENIE calls Needle directly (owner requirement).
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.contracts import CallContext, TaskType
from core.logging_setup import get_logger

from . import tools as tool_catalog
from .base import DirectorDecision, DirectorProvider, DirectorTask
from .heuristics import HeuristicDirector, detect_mission_intent
from .needle_runtime import NeedleRuntime, get_runtime
from .normalize import normalize_decision_input
from .semantic_guard import guard_decision

log = get_logger("director.nedle2")

DEFAULT_CONFIDENCE_THRESHOLD = 0.4     # official production contract value

_URL_RE = re.compile(
    r"\b((?:https?://)?(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|ai|in|co|edu|gov|me|tv|app)"
    r"(?:/[^\s]*)?)\b", re.I)


def extract_url(text: str) -> str:
    """Extract a web address from a request (deterministic; see A-035)."""
    if not text:
        return ""
    match = _URL_RE.search(text)
    if not match:
        return ""
    url = match.group(1).rstrip(".,;:)")
    return url


class NeedleEngineError(RuntimeError):
    pass


class NeedleDirector(DirectorProvider):
    """Real Needle 2 director. Falls back only when the runtime is unavailable."""

    name = "needle"

    def __init__(self, runtime: str = "python",
                 confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
                 runtime_dir: str | Path | None = None, timeout_ms: int = 800,
                 endpoint: str = "", cli_path: str = "", model_path: str = "",
                 tools: Optional[List[Any]] = None):
        self.runtime_kind = runtime
        self.confidence_threshold = float(confidence_threshold)
        self.timeout_ms = timeout_ms
        self.endpoint = endpoint
        self.cli_path = cli_path
        self.model_path = model_path
        self._tools = tools if tools is not None else tool_catalog.TOOLS
        self._runtime: NeedleRuntime = get_runtime(runtime_dir)
        self._engine = None
        self._lock = threading.RLock()
        self._fallback = HeuristicDirector()
        self._available: Optional[bool] = None
        self._last_error = ""
        self._stats = {"calls": 0, "escalated": 0, "accepted": 0, "errors": 0, "total_ms": 0}

    # ------------------------------------------------------------- availability
    def detect(self) -> Optional[str]:
        """Return the active runtime kind or None. Never raises."""
        if self._available is False:
            return None
        try:
            if self._runtime.package_installed() and self._runtime.verify().get("ok"):
                self._available = True
                return "python"
        except Exception as exc:
            self._last_error = str(exc)
        # a localhost server is an allowed fallback, never the default
        if self.endpoint:
            self._available = True
            return "http"
        if self.cli_path:
            self._available = True
            return "cli"
        self._available = False
        return None

    def available(self) -> bool:
        return self.detect() is not None

    # -------------------------------------------------------------------- engine
    def _ensure_engine(self):
        with self._lock:
            if self._engine is not None:
                return self._engine
            if not self._runtime.package_installed():
                raise NeedleEngineError("cactus-needle python package not installed")
            check = self._runtime.verify()
            if not check.get("ok"):
                raise NeedleEngineError(f"needle runtime not verified: {check.get('reason')}")
            self._runtime.activate()
            try:
                import needle  # official package (module name: needle)
                self._engine = needle.Needle(tools=self._tools, system=tool_catalog.SYSTEM)
                log.info("NEDLE2 engine ready (needle %s, engine %s)",
                         self._runtime.package_version(), check.get("version"))
            except Exception as exc:
                raise NeedleEngineError(f"engine init failed: {exc}") from exc
            return self._engine

    def reset(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.reset()

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.close()
                finally:
                    self._engine = None

    # ------------------------------------------------------------- inference
    def _raw_decide(self, text: str) -> Dict[str, Any]:
        """One model call (raw response; contract applied by the caller)."""
        kind = self.detect()
        if kind == "http":
            return self._http_decide(text)
        if kind == "cli":
            return self._cli_decide(text)
        engine = self._ensure_engine()
        with self._lock:                    # the native engine holds global state
            engine.reset()                  # independent-request boundary
            response = engine.complete(text)
        return response if isinstance(response, dict) else {}

    @staticmethod
    def _apply_contract(response: Dict[str, Any],
                        threshold: float) -> Tuple[List[Dict[str, Any]], str]:
        """Returns (accepted_calls, reason). Mirrors the official harness exactly."""
        calls = list(response.get("function_calls") or [])
        validation = response.get("validation") or {}
        if calls and (validation.get("ungrounded") or validation.get("negation")):
            return [], "rejected:ungrounded_or_negation"
        if calls and float(response.get("confidence") or 0.0) < threshold:
            return [], "rejected:below_confidence"
        if not calls:
            return [], "no_call"
        return calls, "accepted"

    def _map_calls(self, calls: List[Dict[str, Any]], text: str) -> DirectorDecision:
        d = DirectorDecision(source=self.name, confidence=0.9)
        for call in calls:
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            if name in tool_catalog.MEMORY_TOOLS:
                d.memory_query = str(args.get("text") or text)[:512]
                continue
            if name in tool_catalog.MISSION_TOOLS:
                d.reasoning_required = True
                d.provider_category = "reasoning"
                # Point 6 — the model routed this to a mission tool, so it is
                # durable work, not a conversation.
                d.mission_required = True
                d.intent = "mission"
                continue
            spec = tool_catalog.TOOL_TO_TASK.get(name)
            if spec is None:
                log.warning("needle emitted an unmapped tool %s — ignoring", name)
                continue
            params: Dict[str, Any] = {}
            if spec.get("arg") and spec["arg"] in args:
                params[spec["arg"]] = args[spec["arg"]]
            for tool_arg, genie_param in (spec.get("param_map") or {}).items():
                if tool_arg in args and args[tool_arg] is not None:
                    params[genie_param] = args[tool_arg]
            device = "pc_main"
            if spec.get("device_arg"):
                raw_device = str(args.get(spec["device_arg"]) or "")
                if raw_device:
                    device = tool_catalog.canonical_device(raw_device)
                else:
                    # model omitted the device -> infer it from the user's own words
                    device = tool_catalog.infer_device(text)
            target = str(args.get("target") or "") if spec.get("arg") == "target" else ""
            d.tasks.append(DirectorTask(
                type=TaskType(spec["type"]), device=device,
                capability=spec["capability"], target=target, params=params))
        return d

    # ------------------------------------------------------------- interface
    def classify(self, text: str, ctx: CallContext,
                 context_hint: Dict[str, Any] | None = None) -> DirectorDecision:
        """NEDLE2 output, always passed through the semantic route guard.

        The router is fast but not authoritative: it answered "mera latest project continue
        karo" with `media.play` at ~0.99 confidence. Every consumer of this director therefore
        receives *validated* output, not raw model output. The guard is a pure, idempotent
        function, so the orchestrator applies it again as defence in depth.
        """
        decision = self._classify(text, ctx, context_hint)
        guard_decision(text or "", decision, context=context_hint)
        return decision

    def _classify(self, text: str, ctx: CallContext,
                  context_hint: Dict[str, Any] | None = None) -> DirectorDecision:
        started = time.time()
        text = (text or "").strip()
        if not text:
            return DirectorDecision(source=self.name, confidence=0.0)
        if not self.available():
            fallback = self._fallback.classify(text, ctx, context_hint)
            fallback.raw["needle_error"] = self._last_error or "runtime unavailable"
            return fallback

        # Hinglish/Hindi command words are normalised in front of the director (D-038):
        # Needle 2 routes English commands reliably; GENIE supplies the language layer.
        model_input, normalised = normalize_decision_input(text)

        # Deterministic fast path (A-035): a web address is unambiguous. The model cannot
        # reliably separate "open youtube.com/x" from "open notepad", so GENIE resolves the
        # URL case itself and NEDLE2 keeps routing everything else.
        url = extract_url(text)
        if url:
            decision = DirectorDecision(
                tasks=[DirectorTask(type=TaskType.BROWSER_ACTION, device="pc_main",
                                    capability="browser.navigate", params={"url": url})],
                reply_hint="", confidence=0.95, source=f"{self.name}+url-fastpath")
            decision.raw = {"engine": self.name, "runtime": self.detect(),
                            "reason": "fast-path:web-address", "url": url,
                            "latency_ms": int((time.time() - started) * 1000)}
            self._stats["accepted"] += 1
            return decision

        self._stats["calls"] += 1
        try:
            response = self._raw_decide(model_input)
        except Exception as exc:
            self._stats["errors"] += 1
            self._last_error = str(exc)
            log.error("NEDLE2 inference failed: %s — falling back", exc)
            fallback = self._fallback.classify(text, ctx, context_hint)
            fallback.raw["needle_error"] = self._last_error
            return fallback

        latency = int((time.time() - started) * 1000)
        self._stats["total_ms"] += latency
        calls, reason = self._apply_contract(response, self.confidence_threshold)

        decision = self._map_calls(calls, text)
        decision.confidence = float(response.get("confidence") or 0.0)
        decision.raw = {
            "engine": self.name,
            "runtime": self.detect(),
            "reason": reason,
            "confidence": decision.confidence,
            "threshold": self.confidence_threshold,
            "latency_ms": latency,
            "needle_type": response.get("type"),
            "validation": response.get("validation") or {},
            "reasoning": (response.get("reasoning") or "")[:400],
            "model_input": model_input,
            "normalised": normalised,
        }

        # Point 6 — the model may not route a durable request to a mission tool.
        # Apply the same semantic detector the heuristic uses, so a recurring /
        # autonomous request still becomes a Mission rather than being lost.
        if not decision.mission_required:
            mission, sched = detect_mission_intent(text)
            if mission:
                decision.mission_required = True
                decision.intent = "mission"
                decision.schedule = sched
            elif not decision.tasks:
                decision.intent = "conversation"

        if decision.tasks or decision.memory_query or decision.reasoning_required:
            self._stats["accepted"] += 1
            return decision

        # --- refusal path: escalate classification to a remote model (or safe handling)
        self._stats["escalated"] += 1
        decision.reasoning_required = True
        decision.provider_category = "reasoning"
        decision.raw["escalated"] = True
        log.info("NEDLE2 refused (reason=%s conf=%.3f) → escalating classification",
                 reason, decision.confidence)
        return decision

    # ------------------------------------------------------------ degraded paths
    def _http_decide(self, text: str) -> Dict[str, Any]:
        import json
        import urllib.request
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps({"query": text, "tools": tool_catalog.tool_names()}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=max(1.0, self.timeout_ms / 1000 * 4)) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _cli_decide(self, text: str) -> Dict[str, Any]:
        import json
        import subprocess
        out = subprocess.run([self.cli_path, "--query", text], capture_output=True, text=True,
                             timeout=max(1.0, self.timeout_ms / 1000 * 4))
        raw = out.stdout or ""
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}

    # ------------------------------------------------------------------ smoke
    def smoke_test(self) -> Dict[str, Any]:
        """Routing smoke test against the real engine (owner-required cases).

        A case passes when the produced routing is inside its `expect` set; for
        refusal cases (empty expect) any accepted task is a failure. Reporting is
        honest: whatever the engine actually does is what is returned.
        """
        cases = [
            # --- owner-required routing cases -----------------------------------
            {"query": "volume 30", "expect": {"system.volume.set"}, "label": "volume (en)"},
            {"query": "awaz 30", "expect": {"system.volume.set"}, "label": "volume (hinglish)"},
            {"query": "open chrome", "expect": {"application.open"}, "label": "open app (en)"},
            {"query": "Chrome kholo", "expect": {"application.open"}, "label": "open app (hinglish)"},
            {"query": "next song on my phone", "expect": {"media.next"}, "label": "device media (en)"},
            {"query": "phone ka next song", "expect": {"media.next"}, "label": "device media (hinglish)"},
            {"query": "continue my latest project", "expect": {"memory:query", "mission"},
             "label": "memory/mission (en)"},
            {"query": "mera latest project continue karo", "expect": {"memory:query", "mission"},
             "label": "memory/mission (hinglish)"},
            # --- negative / safety cases ----------------------------------------
            {"query": "asdkjhasd qwerty ???", "expect": set(), "label": "malformed"},
            {"query": "kuch karo", "expect": set(), "label": "ambiguous"},
            {"query": "delete all my files right now", "expect": set(), "label": "unsupported"},
            {"query": "don't open chrome", "expect": set(), "label": "negated"},
        ]
        if not self.available():
            return {"ok": False, "error": "needle runtime unavailable",
                    "runtime": self._runtime.status()}

        ctx = CallContext()
        results = []
        passed = 0
        for case in cases:
            decision = self.classify(case["query"], ctx)
            routed = {t.capability for t in decision.tasks}
            retrieval = {"memory:query"} if decision.memory_query else set()
            escalation = {"mission"} if decision.raw.get("escalated") else set()
            got = routed | retrieval | escalation

            if case["expect"]:
                ok = bool(case["expect"] & got) and not (routed - case["expect"])
            else:
                # refusal case: no action may be routed (escalation is safe handling)
                ok = not routed
            passed += int(ok)
            results.append({
                "label": case["label"], "query": case["query"], "ok": ok,
                "expected": sorted(case["expect"]), "routed": sorted(routed),
                "retrieval": sorted(retrieval), "escalated": bool(escalation),
                "confidence": round(decision.confidence, 4),
                "reason": decision.raw.get("reason"),
                "model_input": decision.raw.get("model_input"),
                "latency_ms": decision.raw.get("latency_ms"),
            })
        return {
            "ok": passed == len(cases),
            "passed": passed, "total": len(cases),
            "confidence_threshold": self.confidence_threshold,
            "runtime": self._runtime.status(),
            "results": results,
        }

    # ------------------------------------------------------- large tool catalogue
    def large_catalogue_probe(self, filler: int = 60) -> Dict[str, Any]:
        """Owner-required check: routing must not collapse with a big tool catalogue."""
        if not self.available():
            return {"ok": False, "error": "needle runtime unavailable"}
        from typing import Annotated

        from .tools import TOOLS, Field

        def _make(name: str):
            def _fn(
                value: Annotated[str, Field(description=f"Filler value for {name}", max_length=40)] = "",
            ):
                f"""Filler capability {name} used to grow the catalogue."""
                return {"ok": True, "value": value}
            _fn.__name__ = name
            return _fn

        catalogue = list(TOOLS) + [_make(f"filler_{i}_tool") for i in range(filler)]
        probe = NeedleDirector(runtime=self.runtime_kind,
                               confidence_threshold=self.confidence_threshold,
                               runtime_dir=str(self._runtime.dir), tools=catalogue)
        try:
            probe._engine = None
            ctx = CallContext()
            out = []
            for query in ("volume 30", "open chrome"):
                d = probe.classify(query, ctx)
                out.append({"query": query, "routed": sorted(t.capability for t in d.tasks),
                            "confidence": round(d.confidence, 4),
                            "reason": d.raw.get("reason")})
            return {"ok": all(r["routed"] for r in out), "tool_count": len(catalogue), "results": out}
        finally:
            probe.close()

    # ----------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        rt = self._runtime.status()
        return {
            "engine": self.name,
            "runtime": self.detect() or "unavailable",
            "fallback_active": not self.available(),
            "confidence_threshold": self.confidence_threshold,
            "tool_count": len(self._tools),
            "package_version": rt.get("package_version"),
            "engine_version": rt.get("engine_version"),
            "library_verified": rt.get("verified"),
            "runtime_dir": rt.get("runtime_dir"),
            "stats": dict(self._stats),
            "last_error": self._last_error,
        }


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------
_DIRECTOR: Optional[DirectorProvider] = None


def get_director(config=None, force_heuristic: bool = False) -> DirectorProvider:
    """Returns the real Needle director when the runtime is provisioned, else the
    deterministic fallback (degraded mode — clearly visible in /api/status)."""
    global _DIRECTOR
    if _DIRECTOR is not None:
        return _DIRECTOR
    if force_heuristic:
        _DIRECTOR = HeuristicDirector()
        return _DIRECTOR
    if config is None:
        from core.config import get_config
        config = get_config()

    engine = config.get("director.engine", "auto")
    if engine == "heuristic":
        _DIRECTOR = HeuristicDirector()
        return _DIRECTOR

    director = NeedleDirector(
        runtime=config.get("director.needle.runtime", "python"),
        confidence_threshold=float(config.get("director.needle.confidence_threshold",
                                              DEFAULT_CONFIDENCE_THRESHOLD)),
        runtime_dir=config.get("director.needle.runtime_dir") or None,
        timeout_ms=int(config.get("director.needle.timeout_ms", 800)),
        endpoint=config.get("director.needle.endpoint", ""),
        cli_path=config.get("director.needle.cli_path", ""),
        model_path=config.get("director.needle.model_path", ""),
    )
    if director.available():
        _DIRECTOR = director
        return _DIRECTOR
    if engine == "needle":
        log.error("needle engine requested but runtime unavailable — using heuristic fallback")
    else:
        log.warning("NEDLE2 runtime not provisioned — heuristic fallback active "
                    "(run: python genie.py needle-setup)")
    _DIRECTOR = HeuristicDirector()
    return _DIRECTOR


def reset_director() -> None:
    """Test/CLI helper: drop the cached director instance."""
    global _DIRECTOR
    if isinstance(_DIRECTOR, NeedleDirector):
        _DIRECTOR.close()
    _DIRECTOR = None


def reload_director(config=None) -> DirectorProvider:
    """Re-resolve the director (used after the runtime is provisioned at boot)."""
    reset_director()
    return get_director(config)
