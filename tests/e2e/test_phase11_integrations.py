"""Phase 11B — specialist adapter conformance.

Adapters must be honest: an engine that is not running is reported as
`pending-live-acceptance`, never as success and never as a crash. Here we prove both halves
with a **real local HTTP server** (genuine round-trip, not a stubbed return value) and with a
dead port.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from integrations.specialists import (
    ADAPTERS, ComfyUIAdapter, DecepticonAdapter, N8nAdapter, SpecialistAdapter,
    capability_matrix, capabilities, get_adapter, invoke,
)


class _Handler(BaseHTTPRequestHandler):
    """Minimal stand-in for a specialist engine — real sockets, real JSON."""

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/system_stats"):
            # ComfyUI's real /system_stats carries a 'system' block. The adapter
            # checks for it, so a double that wants to look like ComfyUI must
            # actually answer in that shape.
            self._send(200, {"status": "ok",
                             "system": {"comfyui_version": "test-double"}})
        elif self.path.endswith(("/healthz", "/health")):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        self._send(200, {"ok": True, "echo": payload, "engine": "test-engine"})


@pytest.fixture(scope="module")
def engine():
    """A real HTTP specialist engine on a free port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# --------------------------------------------------------------- live round-trip
def test_adapter_really_talks_to_an_engine_over_http(engine):
    a = ComfyUIAdapter(base_url=engine)
    assert a.available()["available"] is True

    out = a.invoke("generate a sunset over mountains", {"steps": 4})
    assert out["ok"] is True, out
    # the engine genuinely received GENIE's requirement
    assert out["data"]["echo"]["requirement"] == "generate a sunset over mountains"
    assert out["data"]["echo"]["steps"] == 4
    assert out["data"]["engine"] == "test-engine"

    conf = a.conformance()
    assert conf["state"] == "ready" and conf["source_repo"] == "ComfyUI"
    # the declared operations are reported, and the health response really is
    # in this engine's shape (not merely "some server answered")
    assert conf["health_shape_ok"] is True
    assert "queue_prompt" in conf["operations"]


def test_a_server_that_is_not_the_engine_is_reported_as_shape_mismatch():
    """Reachable is not the same as 'actually this engine'.

    The old contract called any reachable HTTP server 'ready'. A specialist
    adapter must say when the answer does not look like the engine it wraps.
    """
    class _WrongHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"anything": "else"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _WrongHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        a = ComfyUIAdapter(base_url=base)
        conf = a.conformance()
        assert conf["available"] is True, "the server IS reachable"
        assert conf["state"] == "shape-mismatch", conf
        assert conf["health_shape_ok"] is False
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------- honesty when engine absent
def test_absent_engine_is_pending_never_a_fake_success():
    a = ComfyUIAdapter(base_url="http://127.0.0.1:9")  # port 9: nothing listens
    assert a.available()["available"] is False
    out = a.invoke("make an image")
    assert out["ok"] is False
    # Exact wording depends on the environment: with no proxy a dead endpoint is refused
    # ("unreachable"); behind a proxy it may time out or return an HTTP error. What must always
    # hold is: never a fake success, and a real error reported.
    error = out.get("error", "")
    assert error, "an absent engine must report why it failed"
    assert any(word in error for word in ("unreachable", "HTTP", "timed out", "refused")), error
    conf = a.conformance()
    assert conf["state"] == "pending-live-acceptance", conf
    assert conf["available"] is False


def test_adapter_without_endpoint_reports_pending_not_crash():
    a = DecepticonAdapter()  # no default endpoint on purpose
    assert a.base_url == ""
    out = a.invoke("run an assessment", {"authorised": True})
    assert out["ok"] is False
    assert out["status"] == "pending-live-acceptance"


# ------------------------------------------------------------------- gating
def test_security_adapter_refuses_without_explicit_authorisation():
    a = DecepticonAdapter(base_url="http://127.0.0.1:9")
    out = a.invoke("scan this host")
    assert out["ok"] is False and out["status"] == "gated"
    assert "authorisation" in out["error"]


def test_workflow_adapter_requires_a_workflow_id(engine):
    a = N8nAdapter(base_url=engine)
    missing = a.invoke("sync my CRM")
    assert missing["ok"] is False and "workflow_id" in missing["error"]
    ok = a.invoke("sync my CRM", {"workflow_id": "wf-42"})
    assert ok["ok"] is True
    assert ok["data"]["echo"]["workflow_id"] == "wf-42"


# ------------------------------------------------------------------ registry
def test_every_specialist_capability_is_registered_and_visible():
    caps = capabilities()
    assert {c["capability"] for c in caps} == {
        "workflow.automation", "media.image_video", "avatar.digital_human",
        "finance.timeseries", "simulation.social", "voice.specialized",
        "security.assessment_env"}
    # every adapter names the repo it came from — nothing is anonymous
    assert all(c["source_repo"] for c in caps)
    matrix = capability_matrix()
    assert len(matrix) == len(ADAPTERS)
    assert all(m["state"] in ("ready", "pending-live-acceptance") for m in matrix)


def test_unknown_capability_is_refused_not_swallowed():
    out = invoke("media.do_everything", "x")
    assert out["ok"] is False and "known" in out
    assert "workflow.automation" in out["known"]


def test_get_adapter_matches_capability():
    assert isinstance(get_adapter("media.image_video"), ComfyUIAdapter)
    assert get_adapter("nonexistent") is None


def test_base_contract_is_subclassed_not_duplicated():
    for a in ADAPTERS:
        assert isinstance(a, SpecialistAdapter)
        assert a.capability and a.source_repo and a.operation
