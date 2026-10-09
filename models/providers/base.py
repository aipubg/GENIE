"""Provider adapter interface (models/providers/base) — part of the Provider SDK.

Any new protocol (OpenAI chat, OpenAI responses, Anthropic, Gemini native, ...) is added
by dropping a new adapter here. No other module changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.contracts import Completion, ModelSpec, ProviderError


class ProviderAdapter:
    protocol = "base"

    def __init__(self, base_url: str = "", secret: str | None = None,
                 timeout_s: float = 30.0, auth_scheme: str = "bearer"):
        self.base_url = (base_url or "").rstrip("/")
        self.secret = secret
        self.timeout_s = timeout_s
        # §9 — the scheme that succeeded during Test/Discover is reused for
        # inference, so a provider never "passes discovery then fails to call".
        self.auth_scheme = (auth_scheme or "bearer").strip().lower()

    # ------------------------------------------------------------------ calls
    def complete(self, model: ModelSpec, messages: List[Dict[str, str]],
                 *, max_tokens: int = 1024, temperature: float = 0.3,
                 tools: Optional[List[Dict[str, Any]]] = None, tool_choice=None) -> Completion:
        raise NotImplementedError

    def stream_complete(self, model: ModelSpec, messages: List[Dict[str, str]], *,
                        max_tokens: int = 1024, temperature: float = 0.3,
                        tools: Optional[List[Dict[str, Any]]] = None):
        """Yield text deltas. Default: one delta containing the whole reply.

        Adapters that support true streaming override this. The default is
        deliberately a SINGLE delta — it never splits a finished reply into
        fake chunks to imitate token streaming.
        """
        result = self.complete(model, messages, max_tokens=max_tokens,
                               temperature=temperature, tools=tools)
        if result.text:
            yield result.text

    def test_connection(self, model: ModelSpec) -> Dict[str, Any]:
        """Cheap health probe. Must never throw; returns a result dict."""
        try:
            c = self.complete(model, [{"role": "user", "content": "ping"}], max_tokens=16)
            return {"ok": True, "latency_ms": c.latency_ms, "model": model.model_id}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _tokens(text: str) -> int:
        # A-008: cheap estimate (4 chars/token). Real usage is taken from the API when provided.
        return max(1, len(text) // 4)

    @staticmethod
    def _cost(model: ModelSpec, tin: int, tout: int) -> float:
        return round(tin / 1000 * model.cost_in_per_1k + tout / 1000 * model.cost_out_per_1k, 6)


def get_adapter(protocol: str, base_url: str = "", secret: str | None = None,
                auth_scheme: str = "bearer") -> ProviderAdapter:
    from .openai_compat import OpenAICompatAdapter, OpenAIResponsesAdapter
    from .mock import MockAdapter
    table = {
        "openai_chat": OpenAICompatAdapter,
        "openai_responses": OpenAIResponsesAdapter,
        "mock": MockAdapter,
    }
    cls = table.get(protocol)
    if cls is None:
        # A-009: unknown protocol degrades to OpenAI-compatible instead of failing hard
        cls = OpenAICompatAdapter
    try:
        return cls(base_url=base_url, secret=secret, auth_scheme=auth_scheme)
    except TypeError:
        # Adapters that do not know about auth_scheme keep working unchanged.
        return cls(base_url=base_url, secret=secret)
