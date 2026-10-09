"""Provider Gateway (models/gateway) — contract C5.

Selection order: policy (hard) -> capability -> health -> cost/priority -> latency.
Automatic failover with a provider-agnostic mission snapshot; every attempt is cost-accounted.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from core.contracts import (
    CallContext, Completion, EventType, ModelRequirement, ModelSpec, ProviderError, now_ms,
)
from core.events import get_bus
from core.logging_setup import get_logger

from . import failures
from . import provider_http
from .eligibility import EligibilityTracker
from .health import HealthMonitor
from .providers.base import get_adapter
from .registry import ModelRegistry

log = get_logger("models.gateway")


class BudgetExceeded(Exception):
    pass


# Failure classes that describe ONE model rather than the provider/account.
# A 403 "paid plan required" or a 404 "unknown model" says nothing about the
# other models stored under the same provider, so these must only cool down the
# offending (provider, model) pair - see §5 model-level failover.
_MODEL_SCOPED_KINDS = frozenset({
    "permission_denied",
    "model_unavailable",
    "malformed",
})


class Gateway:
    def __init__(self, registry: ModelRegistry, vault, policy, db,
                 health: Optional[HealthMonitor] = None,
                 eligibility: Optional[EligibilityTracker] = None,
                 max_attempts: int = 3,
                 allow_mock_fallback: bool = True):
        self.registry = registry
        self.vault = vault
        self.policy = policy
        self.db = db
        self.health = health or HealthMonitor(db)
        self.eligibility = eligibility or EligibilityTracker(db)
        self.max_attempts = max_attempts
        # Keep offline/demo mode available to tests and explicit local workflows;
        # the production lifecycle passes the product configuration (off by default).
        self.allow_mock_fallback = bool(allow_mock_fallback)
        self._bus = get_bus()
        # Last selection, for Advanced routing diagnostics only (Point 1.6).
        self._last_selection: Dict[str, Any] = {}

    # ----------------------------------------------------------------- select
    def candidates(self, req: ModelRequirement) -> List[ModelSpec]:
        specs = [s for s in self.registry.models() if s.supports(req)]
        if not self.allow_mock_fallback:
            specs = [s for s in specs if s.protocol != "mock"]
        if req.allowed_provider_ids is not None:
            specs = [s for s in specs if s.provider_id in req.allowed_provider_ids]
        specs = [s for s in specs if self.policy.check(s.provider_id, req.data_class, req.needs_vision)]
        specs = [s for s in specs if self.health.available(s.provider_id, s.model_id)]
        # Point 1 — runtime eligibility: a provider in a hard-ineligible state
        # (quota exhausted / auth required) or inside a cooldown (rate limited /
        # temporarily unavailable) is skipped here. This is runtime-only and
        # never touches the owner's saved configuration.
        specs = [s for s in specs if self.eligibility.is_eligible(s.provider_id)]
        # offline mode (§14.2): when nothing remote is reachable, drop remote providers here so
        # we never burn attempts (and latency) on endpoints that cannot answer. Local and mock
        # providers remain, so GENIE still works offline.
        # §11 — a generic internet probe (TCP 1.1.1.1:443) is DIAGNOSTICS ONLY.
        # It says nothing about whether a configured provider can answer, and
        # that host is filtered on many networks. Using it as the authority made
        # a Ready provider with a stored key vanish, so Chat reported
        # "offline/degraded mode" while the provider was perfectly usable.
        # Provider health is decided by actually attempting the providers below.
        try:
            from core.hardening import get_offline_status
            offline = get_offline_status(self.registry)
            if offline.get("offline"):
                log.info("generic network probe reports offline - ignoring for "
                         "routing (diagnostic only); %d candidate(s) still "
                         "attempted", len(specs))
        except Exception as exc:
            log.debug("offline probe unavailable, routing normally: %s", exc)
        # a provider without credentials cannot serve — skip it instead of burning attempts
        specs = [s for s in specs
                 if not self._requires_credential(s.provider_id)
                 or self.vault.resolve(self.registry.secret_ref(s.provider_id))]
        specs.sort(key=lambda s: (s.priority, s.cost_in_per_1k + s.cost_out_per_1k))
        return specs

    def vision_capable(self, req: Optional[ModelRequirement] = None) -> List[ModelSpec]:
        """List models that can serve vision, WITHOUT the data-class policy filter.

        Used only by the bounded visual fallback (B04/E31): the owner has
        already consented to remote visual processing, so the provider must be
        discoverable before the per-request approval names it. Policy is still
        enforced on the real request via a bounded request scope.
        """
        probe = req or ModelRequirement(capability="vision", needs_vision=True)
        specs = [s for s in self.registry.models() if s.supports(probe)]
        specs = [s for s in specs if s.protocol != "mock"]
        specs = [s for s in specs if self.health.available(s.provider_id, s.model_id)]
        specs = [s for s in specs if self.eligibility.is_eligible(s.provider_id)]
        specs = [s for s in specs
                 if not self._requires_credential(s.provider_id)
                 or self.vault.resolve(self.registry.secret_ref(s.provider_id))]
        specs.sort(key=lambda s: (s.priority, s.cost_in_per_1k + s.cost_out_per_1k))
        return specs

    # ---------------------------------------------------------------- complete
    def complete(self, ctx: CallContext, req: ModelRequirement,
                 messages: List[Dict[str, str]], *, tools: Optional[List[Dict]] = None,
                 max_tokens: int = 1024, temperature: float = 0.3,
                 preferred_model=None, tool_choice=None) -> Completion:
        cands = self.candidates(req)
        if preferred_model:
            cands.sort(key=lambda spec: (spec.provider_id, spec.model_id) != preferred_model)
        if not cands and req.allowed_provider_ids is not None:
            raise ProviderError("No available model at the authorized provider")
        if not cands:
            if not self.allow_mock_fallback:
                raise ProviderError(
                    f"no real model available for {req.capability}; check configured provider status")
            # Explicit offline/developer mode only: mock says it is not a real answer.
            cands = [s for s in self.registry.models() if s.protocol == "mock"]
            if not cands:
                raise ProviderError("no model available for requirement")
            log.warning("no live provider for %s — degraded mock mode", req.capability)

        errors: List[str] = []
        for attempt, spec in enumerate(cands[: self.max_attempts], start=1):
            if not self._within_budget(ctx, req, spec):
                raise BudgetExceeded(f"budget exceeded for {spec.provider_id}/{spec.model_id}")
            secret = self.vault.resolve(self.registry.secret_ref(spec.provider_id)) \
                if spec.protocol != "mock" else None
            adapter = get_adapter(spec.protocol,
                                  base_url=self.registry.base_url(spec.provider_id),
                                  secret=secret)
            try:
                choice = {"tool_choice": tool_choice} if tool_choice and "tools" in spec.capabilities else {}
                result = adapter.complete(spec, messages, max_tokens=max_tokens,
                                          temperature=temperature, tools=tools, **choice)
                self.health.record_success(spec.provider_id, spec.model_id, result.latency_ms)
                # Point 1.4 — a successful call returns the provider to the
                # eligible pool automatically (no manual re-enable needed).
                self.eligibility.record_success(spec.provider_id)
                self._account(ctx, spec, result)
                self._record_selection(req, spec, attempt, errors)
                if attempt > 1:
                    self._bus.publish(EventType.PROVIDER_FAILOVER, {
                        "to_provider": spec.provider_id, "to_model": spec.model_id,
                        "attempt": attempt, "errors": errors}, trace_id=ctx.trace_id)
                return result
            except Exception as exc:
                # Classify so failover behaves per failure class (spec §8) and so
                # callers can tell "retry" from "never replay a partial stream".
                kind = failures.classify(
                    exc, status=getattr(exc, "code", None), body=str(exc))
                detail = failures.describe(kind)
                errors.append(f"{spec.provider_id}/{spec.model_id}: "
                              f"[{kind.value}] {exc}")
                self.health.record_failure(spec.provider_id, spec.model_id,
                                           f"{kind.value}: {exc}")
                # §5 — model-level failover. The per-(provider, model) breaker in
                # `health` already removes THIS model from routing. Failure
                # classes that are a property of ONE model (a paid-only model
                # returning 403, a 404 unknown model, a malformed body) must NOT
                # mark the whole provider ineligible: doing so dropped every
                # other saved model of that provider and pushed Chat into
                # offline/degraded mode on the next turn.
                if kind in _MODEL_SCOPED_KINDS:
                    log.warning("model-scoped failure on %s/%s [%s] - "
                                "provider stays eligible, model cooled down",
                                spec.provider_id, spec.model_id, kind.value)
                else:
                    # Point 1.3/1.4 — provider-wide classes: transient failures
                    # cool down; quota/auth stay ineligible until a positive
                    # signal. The owner's config is never changed.
                    self.eligibility.record_outcome(spec.provider_id, kind,
                                                    f"{kind.value}: {exc}")
                self._bus.publish(EventType.PROVIDER_ERROR, {
                    "provider_id": spec.provider_id, "model_id": spec.model_id,
                    "attempt": attempt, **detail,
                    "message": str(exc)[:400],
                    "runtime_status": self.eligibility.status(spec.provider_id)["status"],
                }, trace_id=ctx.trace_id)
                log.warning("provider attempt %s failed [%s]: %s", attempt, kind.value, exc)
                # A failure that already emitted output must not be replayed as
                # if nothing happened; surface it instead of silently retrying.
                if failures.has_committed_output(kind):
                    raise ProviderError(
                        f"partial output already produced by "
                        f"{spec.provider_id}/{spec.model_id}: {exc}") from exc
                if not failures.failover_ok(kind):
                    raise ProviderError(
                        f"{spec.provider_id}/{spec.model_id}: {exc}") from exc
                continue

        raise ProviderError("; ".join(errors) or "provider failed")

    # ----------------------------------------------------------------- stream
    def stream(self, ctx: CallContext, req: ModelRequirement,
               messages: List[Dict[str, str]], *, tools: Optional[List[Dict]] = None,
               max_tokens: int = 1024, temperature: float = 0.3):
        """Yield (delta, meta) tuples as the provider produces them.

        Real streaming: deltas come from the provider's SSE stream. If a
        provider cannot stream, the adapter's default yields the whole reply as
        ONE delta — never split artificially. The caller can distinguish the two
        by counting deltas; GENIE never claims token-by-token when it got one.
        """
        cands = self.candidates(req)
        if not cands and req.allowed_provider_ids is not None:
            raise ProviderError("No available model at the authorized provider")
        if not cands:
            if not self.allow_mock_fallback:
                raise ProviderError(
                    f"no real model available for {req.capability}; check configured provider status")
            cands = [s for s in self.registry.models() if s.protocol == "mock"]
            if not cands:
                raise ProviderError("no model available for requirement")
            log.warning("no live provider for %s — degraded mock mode (stream)",
                        req.capability)

        errors: List[str] = []
        for attempt, spec in enumerate(cands[: self.max_attempts], start=1):
            if not self._within_budget(ctx, req, spec):
                raise BudgetExceeded(f"budget exceeded for {spec.provider_id}/{spec.model_id}")
            secret = self.vault.resolve(self.registry.secret_ref(spec.provider_id)) \
                if spec.protocol != "mock" else None
            adapter = get_adapter(spec.protocol,
                                  base_url=self.registry.base_url(spec.provider_id),
                                  secret=secret)
            meta = {"provider_id": spec.provider_id, "model_id": spec.model_id,
                    "attempt": attempt}
            try:
                for delta in adapter.stream_complete(
                        spec, messages, max_tokens=max_tokens,
                        temperature=temperature, tools=tools):
                    yield delta, meta
                self.health.record_success(spec.provider_id, spec.model_id, 0)
                self.eligibility.record_success(spec.provider_id)
                self._record_selection(req, spec, attempt, errors)
                return
            except Exception as exc:
                errors.append(f"{spec.provider_id}/{spec.model_id}: {exc}")
                self.health.record_failure(spec.provider_id, spec.model_id, str(exc))
                kind = failures.classify(exc, status=getattr(exc, "code", None),
                                          body=str(exc))
                # §5 — same model-level rule as complete(): a model-specific
                # failure cools down that model only, so the next saved model of
                # the same provider is still tried and Chat stays remote.
                if kind.value not in _MODEL_SCOPED_KINDS:
                    self.eligibility.record_outcome(spec.provider_id, kind,
                                                    f"{kind.value}: {exc}")
                else:
                    log.warning("stream: model-scoped failure %s/%s [%s] - "
                                "provider stays eligible",
                                spec.provider_id, spec.model_id, kind.value)
                log.warning("stream attempt %s failed: %s", attempt, exc)
                continue

        raise ProviderError("; ".join(errors) or "provider stream failed")

    # ------------------------------------------------------------------- test
    def test_connection(self, provider_id: str, model_id: str | None = None) -> Dict[str, Any]:
        prov = self.registry.provider(provider_id)
        if not prov:
            return {"ok": False, "error": "unknown provider"}
        model_id = model_id or (prov.get("models") or [{}])[0].get("model_id", "")
        # No model discovered yet: probe the endpoint instead of guessing one.
        # Guessing produced a 404 against the owner's gateway ("model does not
        # exist") even though the endpoint and credential were both fine.
        if not model_id:
            return self._probe_provider(provider_id, prov)
        spec = self.registry.model(provider_id, model_id)
        if not spec:
            # No saved model yet. The owner can still legitimately press Test as
            # soon as a key is entered, so answer the only question that matters
            # at that point: is the endpoint reachable and does it accept the
            # stored credential?
            return self._probe_provider(provider_id, prov)
        secret = self.vault.resolve(prov.get("secret_ref", "")) if spec.protocol != "mock" else None
        if self._requires_credential(provider_id) and not secret:
            return {"ok": False, "error": "no API key stored in vault"}
        if spec.protocol != "mock" and not prov.get("base_url"):
            return {"ok": False, "error": "base_url not configured"}
        adapter = get_adapter(spec.protocol, base_url=prov.get("base_url", ""), secret=secret)
        result = adapter.test_connection(spec)
        if result.get("ok"):
            # A successful Test returns the provider to the eligible pool
            # (Point 1.4) without touching the owner's saved configuration.
            self.eligibility.record_success(provider_id)
        return {"ok": result.get("ok", False), "provider_id": provider_id,
                "model_id": model_id, **result}

    def _probe_provider(self, provider_id: str, prov: Dict[str, Any]) -> Dict[str, Any]:
        """Connectivity + credential probe for a provider with no saved model.

        Uses the model-list endpoint, which is the same call discovery makes, so
        the answer is consistent with what the owner sees when they rediscover.
        Never raises: the caller gets an honest error_code instead.
        """
        base = (prov or {}).get("base_url") or ""
        if not base:
            return {"ok": False, "provider_id": provider_id, "model_id": "",
                    "error": "base_url not configured",
                    "error_code": "endpoint_unreachable"}
        if self._requires_credential(provider_id):
            ref = (prov or {}).get("secret_ref", "")
            stored = ""
            if ref and self.vault:
                try:
                    stored = self.vault.resolve(ref) or ""
                except Exception:
                    stored = ""
            if not stored:
                return {"ok": False, "provider_id": provider_id, "model_id": "",
                        "error": "no API key stored in vault",
                        "error_code": "authentication_failed"}
        out = self.discover_models(provider_id=provider_id,
                                   protocol=(prov or {}).get("protocol", "openai_chat"))
        if out.get("ok"):
            return {"ok": True, "provider_id": provider_id, "model_id": "",
                    "detail": f"reachable — {out.get('count', 0)} model(s) available",
                    "via": "model_list_probe"}
        return {"ok": False, "provider_id": provider_id, "model_id": "",
                "error": out.get("error") or "connection failed",
                "error_code": out.get("error_code") or "discovery_failed"}

    # ----------------------------------------------------------------- budget
    def discover_models(self, provider_id: str = "",
                        base_url: str = "", secret: str = "",
                        protocol: str = "openai_chat",
                        discovery_url: str = "",
                        headers: Optional[Dict[str, str]] = None,
                        timeout: float = 20.0,
                        auth_scheme: str = "bearer") -> Dict[str, Any]:
        """Enumerate the models a provider actually exposes.

        Used by the Add Provider flow: after Test Connection succeeds the owner
        can pull the real model list instead of typing ids by hand.

        Discovery is BEST EFFORT. Some providers do not expose a model-list
        endpoint, some require a different path, and some gate it. When it
        fails we say why and the UI falls back to manual entry — a provider is
        never rejected merely because discovery is unavailable.

        For OpenAI-compatible providers the canonical endpoint is
        `{base_url}/models` with `Authorization: Bearer <key>`. A provider may
        override it with `discovery_url` (or a `models_path` convention) when
        its layout differs.
        """
        prov = self.registry.provider(provider_id) if provider_id else None
        if provider_id and not prov and not base_url:
            return {"ok": False, "supported": False,
                    "error": "unknown provider", "models": []}

        # Point 0 — a saved custom provider carries its Advanced settings in the
        # authoritative provider record. Explicit call arguments win; the stored
        # record is the durable fallback, so Rediscover / Test Connection reuse
        # exactly what the owner saved (auth scheme, discovery path, timeout,
        # custom headers) across a relaunch. The API key is never among these —
        # it stays in the Vault and is resolved separately below.
        if prov:
            if not discovery_url:
                discovery_url = str(prov.get("discovery_url") or "")
            stored_headers = prov.get("headers")
            if not headers and isinstance(stored_headers, dict) and stored_headers:
                headers = {str(k): str(v) for k, v in stored_headers.items()}
            if (auth_scheme or "bearer").strip().lower() == "bearer":
                auth_scheme = str(prov.get("auth_scheme") or auth_scheme or "bearer")
            try:
                stored_timeout = float(prov.get("timeout") or 0)
                if stored_timeout > 0 and float(timeout) == 20.0:
                    timeout = stored_timeout
            except (TypeError, ValueError):
                pass

        # §10 — URL normalisation lives in one place so we can never emit
        # /v1/v1/models, /chat/completions/models or //models.
        resolved_base = (base_url or (prov or {}).get("base_url", "") or "")
        url = provider_http.models_url(resolved_base, discovery_url)
        if not url or url == "/models":
            return {"ok": False, "supported": False,
                    "error": "base_url not configured", "models": []}

        resolved_secret = secret
        if not resolved_secret and prov:
            ref = prov.get("secret_ref", "")
            if ref and self.vault:
                try:
                    resolved_secret = self.vault.resolve(ref) or ""
                except Exception:
                    resolved_secret = ""

        # §12 — the same HTTP implementation as Test and inference, so a
        # provider that passes Detect Models cannot later fail because the
        # inference path built its request differently.
        extra_headers = {str(k): str(v) for k, v in (headers or {}).items()}

        # §9 — automatic auth negotiation. Bearer first; if the endpoint
        # rejects it, probe the common API-key conventions. Only ever against
        # the owner-configured origin.
        res = provider_http.negotiate_auth(
            url, secret=resolved_secret, preferred=auth_scheme,
            headers=extra_headers, timeout=timeout)

        status_code = int(res.get("status") or 0)
        body_bytes = res.get("body") or b""

        # §8 — describe what was actually sent so an auth failure can be
        # diagnosed honestly instead of blaming the key. Header NAMES only.
        req_info = {
            "url": url,
            "auth_scheme": res.get("auth_scheme") or (auth_scheme or "bearer"),
            "auth_attempts": res.get("auth_attempts") or [],
            "header_names": (res.get("request") or {}).get("header_names", []),
            "timeout": float(timeout),
            "final_url": res.get("final_url") or url,
            "redirect_chain": res.get("redirect_chain") or [],
            "http_status": status_code,
            "content_type": res.get("content_type") or "",
        }
        # The scheme that actually worked, so callers can persist it (§9).
        worked_scheme = res.get("auth_scheme") or ""

        if status_code == 0:
            return {"ok": False, "supported": True, "url": url,
                    "error_code": "endpoint_unreachable",
                    "error": f"Endpoint unreachable ({res.get('error') or 'network error'})",
                    "request": req_info, "models": []}

        if status_code in (401, 403):
            snippet = provider_http.safe_error_snippet(body_bytes)
            # Do NOT claim the key is wrong: a WAF/bot block returns 403 too.
            return {"ok": False, "supported": True, "url": url,
                    "http_status": status_code,
                    "error_code": "authentication_failed",
                    "error": f"Endpoint rejected the request (HTTP {status_code}). "
                             f"{snippet}".strip(),
                    "request": req_info, "models": []}
        if status_code >= 400:
            snippet = provider_http.safe_error_snippet(body_bytes)
            return {"ok": False, "supported": True, "url": url,
                    "http_status": status_code,
                    "error_code": "discovery_failed",
                    "error": f"Model discovery failed (HTTP {status_code}). "
                             f"{snippet}".strip(),
                    "request": req_info, "models": []}

        try:
            payload = json.loads(body_bytes or b"")
        except Exception:
            return {"ok": False, "supported": True, "url": url,
                    "error_code": "invalid_response",
                    "error": "Provider returned a non-JSON model list.",
                    "auth_scheme": worked_scheme,
                    "request": req_info, "models": []}

        # OpenAI-compatible: {"data": [{"id": "..."}...]}
        # Some providers return a bare list, or {"models": [...]}.
        raw: List[Any] = []
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                raw = payload["data"]
            elif isinstance(payload.get("models"), list):
                raw = payload["models"]
        elif isinstance(payload, list):
            raw = payload

        # Keep the endpoint's own metadata where it supplies it. GENIE must not
        # infer "reasoning"/"coding"/"vision" from a model name (§4): capability
        # is taken from metadata only, otherwise left unknown.
        models: List[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                mid, label, meta = item, item, {}
            elif isinstance(item, dict):
                mid = str(item.get("id") or item.get("model_id") or
                          item.get("model") or item.get("name") or "").strip()
                label = str(item.get("display_name") or item.get("name") or
                            mid).strip()
                meta = {
                    "owned_by": item.get("owned_by") or "",
                    "modality": item.get("modality") or "",
                    "access_tier": item.get("access_tier") or "",
                    "capabilities": item.get("capabilities")
                        if isinstance(item.get("capabilities"), dict) else {},
                    "default": bool(item.get("default") or item.get("is_default")),
                    "preferred": bool(item.get("preferred") or item.get("recommended")),
                }
            else:
                continue
            if mid:
                models.append({"model_id": mid, "display_name": label or mid,
                               **meta})

        if not models:
            return {"ok": False, "supported": True, "url": url,
                    "error_code": "no_models",
                    "error": "No models returned by this endpoint.",
                    "auth_scheme": worked_scheme,
                    "request": req_info, "models": []}

        return {"ok": True, "supported": True, "url": url,
                "auth_scheme": worked_scheme,
                "request": req_info, "count": len(models), "models": models}

    def _requires_credential(self, provider_id: str) -> bool:
        provider = self.registry.provider(provider_id) or {}
        if provider.get("protocol") == "mock":
            return False
        # An explicit empty secret_ref opts a configured endpoint into anonymous
        # access. Default remote providers still require their vault credential.
        return bool(provider.get("secret_ref", "required"))

    def _within_budget(self, ctx: CallContext, req: ModelRequirement, spec: ModelSpec) -> bool:
        cap = min(req.budget_usd, self.policy.max_cost(spec.provider_id))
        row = self.db.query_one("SELECT cost_usd FROM missions WHERE mission_id=?",
                                (ctx.mission_id or "",))
        spent = row["cost_usd"] if row else 0.0
        return spent <= cap

    def _account(self, ctx: CallContext, spec: ModelSpec, c: Completion) -> None:
        if ctx.mission_id:
            self.db.execute("UPDATE missions SET cost_usd = cost_usd + ? WHERE mission_id=?",
                            (c.cost_usd, ctx.mission_id))
        self.db.execute(
            "INSERT INTO provider_state(provider_id, model_id, total_calls, total_cost)"
            " VALUES(?,?,1,?) ON CONFLICT(provider_id, model_id) DO UPDATE SET"
            " total_calls = total_calls + 1, total_cost = total_cost + excluded.total_cost",
            (spec.provider_id, spec.model_id, c.cost_usd))
        log.info("llm call provider=%s model=%s in=%s out=%s cost=%.6f latency=%sms",
                 spec.provider_id, spec.model_id, c.input_tokens, c.output_tokens,
                 c.cost_usd, c.latency_ms)

    def _record_selection(self, req: ModelRequirement, spec: ModelSpec,
                          attempt: int, errors: List[str]) -> None:
        """Store the most recent routing decision for Advanced diagnostics (1.6)."""
        self._last_selection = {
            "capability": req.capability,
            "needs_tools": req.needs_tools,
            "needs_vision": req.needs_vision,
            "selected_provider": spec.provider_id,
            "selected_model": spec.model_id,
            "attempt": attempt,
            "rejected": [e for e in errors],
            "ts_ms": now_ms(),
        }

    def last_selection(self) -> Dict[str, Any]:
        return dict(self._last_selection)
