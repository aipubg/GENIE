"""MCP runtime / client (`integrations/mcp_runtime.py`).

Connection *supervision* lives in `integrations/mcp_supervision.py` (Strix donor). This is the
*runtime*: connect to an MCP server, discover its tools, and call them — every call goes through a
supervised session, so reconnects, quarantine and death are handled and in-flight calls are never
left hanging.

The transport is **pluggable**. A real deployment supplies an `McpTransport` that speaks the MCP
wire protocol (stdio / streamable-HTTP / SSE); `transport_for()` adapts it to the callable the
supervisor expects. Tests supply an in-memory fake, so the runtime logic (negotiate -> list -> call,
with the supervised state machine) is fully exercised **without a live MCP server**.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from integrations.mcp_supervision import (
    DEAD, QUARANTINED, SupervisedMcpSession,
)

try:  # keep usable without the app logging stack
    from core.logging_setup import get_logger
    log = get_logger("integrations.mcp_runtime")
except Exception:  # pragma: no cover - only when run outside the app
    import logging
    log = logging.getLogger("integrations.mcp_runtime")

MCP_METHOD_INITIALIZE = "initialize"
MCP_METHOD_LIST_TOOLS = "tools/list"
MCP_METHOD_CALL_TOOL = "tools/call"


class McpTransport(abc.ABC):
    """Wire-protocol adapter for one MCP server.

    A real implementation speaks stdio / streamable-HTTP / SSE. The runtime only needs these three
    methods; `transport_for()` turns them into the callable the supervisor drives.
    """

    @abc.abstractmethod
    def initialize(self) -> Dict[str, Any]:
        """Return server capabilities, e.g. ``{"serverInfo": {...}, "capabilities": {"tools": {}}}``."""

    @abc.abstractmethod
    def list_tools(self) -> Dict[str, Any]:
        """Return ``{"tools": [{"name", "description", "inputSchema"}, ...]}``."""

    @abc.abstractmethod
    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Return an MCP ``tools/call`` result: ``{"content": [...], "isError": bool, ...}``."""

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release the connection."""


def transport_for(server: McpTransport) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    """Adapt an `McpTransport` to the `Callable[[method, params], result]` the supervisor expects."""

    def _dispatch(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if method == MCP_METHOD_INITIALIZE:
            return {"ok": True, "result": server.initialize()}
        if method == MCP_METHOD_LIST_TOOLS:
            return {"ok": True, "result": server.list_tools()}
        if method == MCP_METHOD_CALL_TOOL:
            try:
                return {"ok": True, "result": server.call_tool(
                    params.get("name", ""), params.get("arguments") or {})}
            except Exception as exc:  # tool raised -> transport failure, never a fabricated result
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "error": f"unsupported MCP method: {method}"}

    return _dispatch


@dataclass
class McpTool:
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    annotations: Dict[str, Any] = field(default_factory=dict)


class McpRuntime:
    """High-level MCP client: negotiate, list and call tools through a supervised session."""

    def __init__(self, server_id: str,
                 transport: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                 *, connect: Optional[Callable[[], bool]] = None,
                 failure_threshold: int = 3, dead_after: int = 6,
                 retry_delay_s: float = 2.0, max_concurrent: int = 4):
        self.server_id = str(server_id)
        self._session = SupervisedMcpSession(
            server_id, transport=transport, connect=connect,
            failure_threshold=failure_threshold, dead_after=dead_after,
            retry_delay_s=retry_delay_s, max_concurrent=max_concurrent)
        self._server_info: Optional[Dict[str, Any]] = None
        self._tools: Optional[List[McpTool]] = None

    # ----------------------------------------------------------- lifecycle
    def initialize(self) -> Dict[str, Any]:
        out = self._session.call(MCP_METHOD_INITIALIZE)
        if out.get("ok"):
            self._server_info = out.get("result")
        return out

    def health(self) -> Dict[str, Any]:
        return self._session.status()

    def reconnect(self) -> Dict[str, Any]:
        return self._session.reconnect()

    @property
    def state(self) -> str:
        return self._session.state

    @property
    def server_info(self) -> Optional[Dict[str, Any]]:
        return self._server_info

    # --------------------------------------------------------------- tools
    def list_tools(self, *, force: bool = False) -> List[McpTool]:
        if self._tools is not None and not force:
            return self._tools
        out = self._session.call(MCP_METHOD_LIST_TOOLS)
        if not out.get("ok"):
            return []
        raw = (out.get("result") or {}).get("tools", [])
        self._tools = [
            McpTool(name=t.get("name", ""), description=t.get("description", ""),
                   input_schema=t.get("inputSchema") or {},
                   annotations=t.get("annotations") or {})
            for t in raw
        ]
        return self._tools

    def get_tool(self, name: str) -> Optional[McpTool]:
        for tool in self.list_tools():
            if tool.name == name:
                return tool
        return None

    # --------------------------------------------------------------- call
    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = self._session.call(MCP_METHOD_CALL_TOOL,
                                 {"name": name, "arguments": arguments or {}})
        if not out.get("ok"):
            return {"ok": False, "tool": name, "session": out.get("session"),
                    "error": out.get("error"), "retry_after_s": out.get("retry_after_s")}
        result = out.get("result") or {}
        return {"ok": True, "tool": name, "session": out.get("session"),
                "call_id": out.get("call_id"),
                "content": result.get("content", []),
                "structured": result.get("structuredContent"),
                "is_error": bool(result.get("isError")),
                "meta": result.get("_meta")}

    def reset_cache(self) -> None:
        self._tools = None
