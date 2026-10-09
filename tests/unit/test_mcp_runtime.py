"""Tests for integrations/mcp_runtime.py — MCP runtime over a supervised session.

Uses an in-memory `FakeMcpTransport` (no live MCP server). Exercises negotiate -> list -> call,
caching, MCP-level errors (transport-ok but tool-error), the supervised quarantine/refusal path,
and death via exhausted reconnects.
"""
from __future__ import annotations

from integrations.mcp_runtime import (
    McpRuntime, McpTool, McpTransport, transport_for,
)
from integrations.mcp_supervision import QUARANTINED, DEAD


class FakeMcpTransport(McpTransport):
    def __init__(self, server_name="fake", tools=None, fail_all=False, raise_on=None):
        self.server_name = server_name
        self._tools = dict(tools or {})
        self.fail_all = fail_all
        self.raise_on = set(raise_on or [])
        self.list_calls = 0
        self.call_calls = 0

    def initialize(self):
        return {"serverInfo": {"name": self.server_name},
                "capabilities": {"tools": {}}, "protocolVersion": "2025-06-18"}

    def list_tools(self):
        self.list_calls += 1
        return {"tools": [
            {"name": n, "description": t.get("description", ""),
             "inputSchema": t.get("inputSchema", {})}
            for n, t in self._tools.items()
        ]}

    def call_tool(self, name, arguments):
        self.call_calls += 1
        # A *transport* failure is signalled by raising (so `transport_for` reports ok:false);
        # returning an MCP result with isError:true is a *tool* error (transport still ok).
        if self.fail_all:
            raise RuntimeError("simulated transport failure")
        if name in self.raise_on:
            raise RuntimeError(f"explode {name}")
        tool = self._tools.get(name)
        if tool is None:
            # MCP-level error: transport succeeded, but the tool itself reports failure.
            return {"isError": True, "content": [{"type": "text", "text": f"unknown tool {name}"}]}
        return {"content": [{"type": "text", "text": str(tool["fn"](arguments))}],
                "isError": False}


def _runtime(server_name="srv", tools=None, fail_all=False, raise_on=None,
             connect=None, **kw):
    fake = FakeMcpTransport(server_name=server_name, tools=tools,
                             fail_all=fail_all, raise_on=raise_on)
    return McpRuntime(server_name, transport_for(fake), connect=connect, **kw), fake


def _two_tools():
    return {
        "add": {"description": "add two numbers", "fn": lambda a: a["a"] + a["b"]},
        "echo": {"description": "echo", "fn": lambda a: a.get("text", "")},
    }


# ----------------------------------------------------------- negotiate / list
def test_initialize_records_server_info():
    rt, _ = _runtime()
    out = rt.initialize()
    assert out["ok"] is True
    assert rt.server_info["serverInfo"]["name"] == "srv"
    assert rt.state not in (QUARANTINED, DEAD)


def test_list_tools_returns_typed_tools():
    rt, _ = _runtime(tools=_two_tools())
    rt.initialize()
    tools = rt.list_tools()
    assert {t.name for t in tools} == {"add", "echo"}
    add = rt.get_tool("add")
    assert isinstance(add, McpTool)
    assert add.description == "add two numbers"
    assert rt.get_tool("missing") is None


def test_list_tools_is_cached():
    rt, fake = _runtime(tools=_two_tools())
    rt.initialize()
    rt.list_tools()
    rt.list_tools()               # second call hits cache
    assert fake.list_calls == 1
    rt.list_tools(force=True)     # forced re-fetch
    assert fake.list_calls == 2


# --------------------------------------------------------------- call
def test_call_tool_returns_content():
    rt, _ = _runtime(tools=_two_tools())
    rt.initialize()
    out = rt.call_tool("add", {"a": 2, "b": 3})
    assert out["ok"] is True
    assert out["is_error"] is False
    assert out["content"][0]["text"] == "5"
    assert out["session"] not in (QUARANTINED, DEAD)


def test_mcp_level_error_is_ok_but_flagged():
    """Transport succeeded but the tool itself errored (isError) — distinct from a transport failure."""
    rt, _ = _runtime(tools=_two_tools())
    rt.initialize()
    out = rt.call_tool("nope", {})
    assert out["ok"] is True
    assert out["is_error"] is True


def test_call_tool_unknown_tool_is_mcp_error_not_fabrication():
    rt, fake = _runtime(tools=_two_tools())
    rt.initialize()
    out = rt.call_tool("ghost", {})
    assert out["ok"] is True
    assert out["is_error"] is True
    assert fake.call_calls >= 1


def test_transport_exception_is_caught_not_fabricated():
    rt, _ = _runtime(tools=_two_tools(), raise_on={"add"})
    rt.initialize()
    out = rt.call_tool("add", {"a": 1, "b": 1})
    assert out["ok"] is False
    assert "explode" in (out.get("error") or "")


# ----------------------------------------------------- supervised refusal / death
def test_repeated_transport_failures_quarantine_then_refuse():
    rt, fake = _runtime(tools=_two_tools(), fail_all=True,
                         failure_threshold=3, retry_delay_s=0)
    rt.initialize()
    for _ in range(3):
        rt.call_tool("x", {})          # every call fails -> 3 failures -> quarantined
    assert rt.state == QUARANTINED
    # a 4th call is REFUSED (no transport attempt) once quarantined
    before = fake.call_calls
    out = rt.call_tool("x", {})
    assert out["ok"] is False
    assert out["session"] == QUARANTINED
    assert fake.call_calls == before, "quarantined session must not invoke the transport"


def test_death_after_exhausted_reconnects():
    rt, _ = _runtime(tools=_two_tools(), fail_all=True, connect=lambda: False,
                     failure_threshold=3, dead_after=2, retry_delay_s=0)
    rt.initialize()
    for _ in range(3):
        rt.call_tool("x", {})          # -> quarantined
    assert rt.reconnect()["status"] == QUARANTINED
    assert rt.reconnect()["status"] == DEAD   # 2nd failed reconnect -> dead
    assert rt.state == DEAD
    out = rt.call_tool("add", {"a": 1, "b": 2})
    assert out["ok"] is False
    assert out["session"] == DEAD


# ----------------------------------------------------------- observability
def test_health_reports_session_state():
    rt, _ = _runtime(tools=_two_tools())
    rt.initialize()
    rt.call_tool("add", {"a": 1, "b": 1})
    status = rt.health()
    assert status["state"] == rt.state
    assert status["usable"] is True
    assert status["calls"] >= 2


def test_raw_callable_transport_is_accepted_directly():
    """Pluggability: a plain callable (no McpTransport subclass) also works."""
    def raw(method, params):
        if method == "initialize":
            return {"ok": True, "result": {"serverInfo": {"name": "raw"}}}
        if method == "tools/list":
            return {"ok": True, "result": {"tools": [{"name": "ping", "description": "p"}]}}
        if method == "tools/call":
            return {"ok": True, "result": {"content": [{"type": "text", "text": "pong"}]}}
        return {"ok": False, "error": f"no {method}"}

    rt = McpRuntime("raw", raw)
    assert rt.initialize()["ok"] is True
    assert {t.name for t in rt.list_tools()} == {"ping"}
    assert rt.call_tool("ping")["content"][0]["text"] == "pong"
