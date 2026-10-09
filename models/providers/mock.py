"""Offline deterministic provider (models/providers/mock).

Used when no API key exists yet, in tests, and as the last-resort degraded-mode
provider. It lets the whole GENIE pipeline run end-to-end without network access.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.contracts import Completion, ModelSpec

from .base import ProviderAdapter


class MockAdapter(ProviderAdapter):
    protocol = "mock"

    def complete(self, model: ModelSpec, messages: List[Dict[str, str]], *,
                 max_tokens: int = 1024, temperature: float = 0.3,
                 tools: Optional[List[Dict[str, Any]]] = None, tool_choice=None) -> Completion:
        started = time.time()
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        text = (f"[mock:{model.model_id}] Demo model active hai; yeh real AI jawab nahi hai. "
                "Settings mein working provider/model aur valid API key configure karein. "
                "Is demo response ne koi task execute nahi kiya.")
        tin = self._tokens("".join(m.get("content", "") for m in messages))
        tout = self._tokens(text)
        return Completion(text=text, model=model.model_id, provider_id=model.provider_id,
                          input_tokens=tin, output_tokens=tout, cost_usd=0.0,
                          latency_ms=int((time.time() - started) * 1000))
