"""OpenAI-compatible provider adapters (models/providers/openai_compat).

Supports any endpoint of the form https://provider.example/v1 — including custom
OpenAI-compatible gateways — plus the newer /responses style API.

Assumption A-010: transport uses urllib (stdlib) so GENIE keeps zero third-party
runtime dependencies and stays light on low-end PCs.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from core.contracts import Completion, ModelSpec, ProviderError
from core.tool_protocol import compatibility_messages, normalize_completion

from .base import ProviderAdapter


class OpenAICompatAdapter(ProviderAdapter):
    protocol = "openai_chat"

    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # §12 — inference goes through the SAME shared HTTP implementation as
        # Test Connection and model discovery. Otherwise a provider can pass
        # discovery and then fail only because this path built its request
        # differently (different User-Agent, different auth header).
        from .. import provider_http
        data = json.dumps(payload).encode("utf-8")
        res = provider_http.request(
            url, method="POST", secret=self.secret or "",
            auth_scheme=getattr(self, "auth_scheme", "bearer"),
            body=data, timeout=self.timeout_s)
        status = int(res.get("status") or 0)
        raw = (res.get("body") or b"").decode("utf-8", "replace")
        if status == 0:
            raise ProviderError(f"Provider transport failure [{res.get('error_code', 'TRANSPORT_UNAVAILABLE')}]")
        if status >= 400:
            raise ProviderError(f"HTTP {status} from provider: "
                                f"{raw[:400]}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"invalid JSON from provider: {raw[:200]}") from exc

    def complete(self, model: ModelSpec, messages: List[Dict[str, str]], *,
                 max_tokens: int = 1024, temperature: float = 0.3,
                 tools: Optional[List[Dict[str, Any]]] = None, tool_choice=None) -> Completion:
        if not self.base_url:
            raise ProviderError("base_url not configured for provider")
        started = time.time()
        from .. import provider_http
        url = provider_http.chat_url(self.base_url)
        native = bool(tools and "tools" in model.capabilities)
        wire_messages = compatibility_messages(messages, tools) if tools and not native else [
            {key: value for key, value in message.items() if key != "response_items"} for message in messages]
        payload: Dict[str, Any] = {
            "model": model.model_id,
            "messages": wire_messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, model.max_output or max_tokens),
            "stream": False,
        }
        if native:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        data = self._post(url, payload)
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from exc
        usage = data.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or self._tokens(json.dumps(wire_messages)))
        tout = int(usage.get("completion_tokens") or self._tokens(text))
        completion = Completion(text=text, model=model.model_id, provider_id=model.provider_id,
                          input_tokens=tin, output_tokens=tout,
                          cost_usd=self._cost(model, tin, tout),
                          latency_ms=int((time.time() - started) * 1000), raw=data)
        return normalize_completion(completion, tools, message.get("tool_calls"))


    @staticmethod
    def _single_completion_text(raw: str) -> str:
        """Pull the assistant text out of a NON-streaming completion body."""
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return ""
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return ""

    def stream_complete(self, model: ModelSpec, messages: List[Dict[str, str]], *,
                        max_tokens: int = 1024, temperature: float = 0.3,
                        tools: Optional[List[Dict[str, Any]]] = None):
        """Yield text deltas as they arrive (real SSE streaming).

        This is genuine token streaming from the provider — the deltas are
        whatever the model actually emits. If the provider cannot stream, the
        caller falls back to one complete response; nothing is synthesised by
        splitting a finished reply into fake chunks.
        """
        if not self.base_url:
            raise ProviderError("base_url not configured for provider")
        if tools:
            raise ProviderError("Use complete with the tool-turn dispatcher; text streaming cannot carry tool events")
        from .. import provider_http
        url = provider_http.chat_url(self.base_url)
        payload: Dict[str, Any] = {
            "model": model.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, model.max_output or max_tokens),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        # §12 — same User-Agent / auth construction as the non-streaming path.
        from .. import provider_http
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream",
                   "User-Agent": provider_http.USER_AGENT}
        headers.update(provider_http.build_auth_headers(
            self.secret or "", getattr(self, "auth_scheme", "bearer")))
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            produced = False
            collected: List[str] = []
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    collected.append(line)
                    if not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body in ("", "[DONE]"):
                        continue
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = chunk["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, AttributeError):
                        continue
                    if delta:
                        produced = True
                        yield delta
            # Some OpenAI-compatible endpoints ignore stream:true and answer with
            # a single plain JSON completion. The SSE loop then finds no `data:`
            # frame and yields nothing — which surfaced as an EMPTY chat reply.
            # Fall back to that one complete response, emitted as a single delta
            # (never split into fake token chunks).
            if not produced:
                text = self._single_completion_text("\n".join(collected))
                if text:
                    yield text
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise ProviderError(f"HTTP {exc.code} from provider: {body}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"stream error: {exc}") from exc


class OpenAIResponsesAdapter(OpenAICompatAdapter):
    """Newer /responses endpoint (input/output item style)."""

    protocol = "openai_responses"

    def complete(self, model: ModelSpec, messages: List[Dict[str, str]], *,
                 max_tokens: int = 1024, temperature: float = 0.3,
                 tools: Optional[List[Dict[str, Any]]] = None, tool_choice=None) -> Completion:
        if not self.base_url:
            raise ProviderError("base_url not configured for provider")
        started = time.time()
        from .. import provider_http
        url = provider_http.normalize_base_url(self.base_url) + "/responses"
        native = bool(tools and "tools" in model.capabilities)
        wire_messages = compatibility_messages(messages, tools) if tools and not native else messages
        items = []
        for message in wire_messages:
            if message.get("tool_calls"):
                # Preserve reasoning items needed by reasoning-model continuations.
                items.extend(message.get("response_items", []))
                for call in message["tool_calls"]:
                    items.append({"type": "function_call", "call_id": call["id"],
                                  "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
            elif message.get("role") == "tool":
                items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
            else:
                items.append({"role": message["role"], "content": message.get("content") or ""})
        payload: Dict[str, Any] = {
            "model": model.model_id,
            "input": items,
            "max_output_tokens": min(max_tokens, model.max_output or max_tokens),
        }
        if native:
            payload["tools"] = [{"type": "function", **tool["function"], "strict": False} for tool in tools]
            if tool_choice:
                payload["tool_choice"] = tool_choice
        data = self._post(url, payload)
        text = ""
        for item in data.get("output", []):
            for chunk in item.get("content", []) or []:
                if chunk.get("type") in ("output_text", "text"):
                    text += chunk.get("text", "")
        if not text:
            text = data.get("output_text", "")
        usage = data.get("usage") or {}
        tin = int(usage.get("input_tokens") or self._tokens(json.dumps(items)))
        tout = int(usage.get("output_tokens") or self._tokens(text))
        completion = Completion(text=text, model=model.model_id, provider_id=model.provider_id,
                          input_tokens=tin, output_tokens=tout,
                          cost_usd=self._cost(model, tin, tout),
                          latency_ms=int((time.time() - started) * 1000), raw=data)
        calls = [{"type": "function", "id": item.get("call_id"), "function": {
            "name": item.get("name"), "arguments": item.get("arguments")}}
            for item in data.get("output", []) if item.get("type") == "function_call"]
        return normalize_completion(completion, tools, calls)

    def stream_complete(self, model, messages, **kwargs):
        # Responses must not accidentally issue a Chat Completions request.
        result = self.complete(model, messages, **kwargs)
        if result.tool_calls or result.tool_error:
            raise ProviderError("Structured tools require the tool-turn dispatcher")
        if result.text:
            yield result.text
