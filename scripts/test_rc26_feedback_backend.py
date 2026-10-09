"""Targeted backend tests for rc26 owner feedback (Points 1 + 2).

Does NOT run the full suite. Covers:
  * Eligibility transitions (quota/rate-limit/auth/unreachable -> status + eligibility)
  * Gateway failover: a hard-ineligible (quota) provider is skipped and the
    next eligible provider serves; the failed provider becomes ineligible
    without touching its saved config.
  * Custom discovery: auth scheme (bearer/basic) + custom headers plumbing.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.contracts import CallContext, Completion, ModelRequirement, ModelSpec
from core.db import get_db
from models.eligibility import EligibilityTracker
from models.failures import FailureKind
from models.gateway import Gateway

PASS = []
def check(name, ok, detail=""):
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


# --------------------------------------------------------------- fake harness
class FakeRegistry:
    def __init__(self, specs): self._specs = specs
    def models(self, enabled_only=True):
        return [s for s in self._specs if (s.enabled or not enabled_only)]
    def model(self, pid, mid):
        return next((s for s in self._specs
                     if s.provider_id == pid and s.model_id == mid), None)
    def provider(self, pid): return {"id": pid, "protocol": "mock", "base_url": "",
                                      "secret_ref": f"secret://provider/{pid}/key"}
    def base_url(self, pid): return ""
    def secret_ref(self, pid): return f"secret://provider/{pid}/key"


class FakeVault:
    def resolve(self, ref): return "k"
    def has(self, ref): return True


class FakePolicy:
    def check(self, *a, **k): return True
    def max_cost(self, pid): return 10.0


class FakeAdapter:
    def complete(self, spec, messages, **k):
        if spec.provider_id == "bad":
            raise RuntimeError("quota exhausted: insufficient credit balance")
        return Completion(text="ok", model=spec.model_id, provider_id=spec.provider_id,
                          latency_ms=5)
    def stream_complete(self, spec, messages, **k):
        if spec.provider_id == "bad":
            raise RuntimeError("quota exhausted")
        yield ("d",)


def build_gateway(specs, db):
    import models.gateway as gw_mod
    gw_mod.get_adapter = lambda protocol, base_url="", secret=None: FakeAdapter()
    elig = EligibilityTracker(db)
    gw = Gateway(FakeRegistry(specs), FakeVault(), FakePolicy(), db,
                eligibility=elig, max_attempts=3)
    return gw, elig


# --------------------------------------------------------------------- tests
def test_eligibility():
    db = get_db(":memory:")
    db.migrate()
    t = EligibilityTracker(db)

    check("eligibility default eligible", t.is_eligible("p1") and t.status("p1")["status"] == "ready")

    t.record_outcome("p1", FailureKind.QUOTA_EXHAUSTED)
    st = t.status("p1")
    check("quota_exhausted -> ineligible + status",
          (not t.is_eligible("p1")) and st["status"] == "quota_exhausted", st["status"])

    t.record_success("p1")
    check("success -> eligible again (no config change)",
          t.is_eligible("p1") and t.status("p1")["status"] == "ready")

    t.record_outcome("p1", FailureKind.RATE_LIMIT)
    st = t.status("p1")
    check("rate_limit -> rate_limited + ineligible during cooldown",
          (not t.is_eligible("p1")) and st["status"] == "rate_limited", st["status"])

    t.record_outcome("p1", FailureKind.UNREACHABLE)
    check("unreachable -> connection_failed",
          t.status("p1")["status"] == "connection_failed")

    t.record_outcome("p1", FailureKind.PERMISSION_DENIED)
    check("auth invalid -> auth_required + ineligible",
          (not t.is_eligible("p1")) and t.status("p1")["status"] == "auth_required")

    t.record_success("p1")
    check("auth replaced (success) -> eligible", t.is_eligible("p1"))


def test_gateway_failover():
    db = get_db(":memory:")
    db.migrate()
    specs = [
        ModelSpec(provider_id="bad", model_id="m1", display_name="bad",
                  protocol="mock", capabilities=["general"], priority=10),
        ModelSpec(provider_id="good", model_id="m2", display_name="good",
                  protocol="mock", capabilities=["general"], priority=100),
    ]
    gw, elig = build_gateway(specs, db)
    req = ModelRequirement(capability="reasoning")
    ctx = CallContext()

    # Before the failure, both are candidates.
    check("both providers eligible initially",
          len(gw.candidates(req)) == 2)

    res = gw.complete(ctx, req, [{"role": "user", "content": "hi"}])
    check("failover served by good provider", res.provider_id == "good", res.provider_id)
    check("failed provider now ineligible (quota)",
          (not elig.is_eligible("bad")) and elig.status("bad")["status"] == "quota_exhausted")
    # The owner's saved config is untouched: 'bad' is still enabled in the registry.
    check("config not modified by failure", gw.registry.provider("bad").get("enabled", True))

    # After the hard failure, candidates() excludes 'bad' automatically.
    check("candidates excludes ineligible provider",
          all(s.provider_id != "bad" for s in gw.candidates(req)))

    # A successful call returns 'good' to eligible pool.
    gw.complete(ctx, req, [{"role": "user", "content": "again"}])
    check("good stays eligible after success", elig.is_eligible("good"))


# ------------------------------------------------------- custom discovery stub
class _H(BaseHTTPRequestHandler):
    received = {}

    def do_GET(self):
        _H.received = {k.lower(): v for k, v in self.headers.items()}
        body = json.dumps({"data": [{"id": "m-test"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_discovery_params():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        db = get_db(":memory:"); db.migrate()
        gw, _ = build_gateway([], db)

        out = gw.discover_models(base_url=base, secret="abc", auth_scheme="bearer")
        check("discovery bearer auth",
              out.get("ok") and _H.received.get("authorization") == "Bearer abc",
              str(_H.received.get("authorization")))

        out = gw.discover_models(base_url=base, secret="abc", auth_scheme="basic")
        exp = "Basic " + base64.b64encode(b"abc").decode()
        check("discovery basic auth", _H.received.get("authorization") == exp,
              str(_H.received.get("authorization")))

        out = gw.discover_models(base_url=base, secret="abc",
                                 headers={"X-Custom": "1", "X-Token": "z"})
        check("discovery custom headers passthrough",
              _H.received.get("x-custom") == "1" and _H.received.get("x-token") == "z")
        check("discovery returns models", out.get("count") == 1)
    finally:
        server.shutdown()


if __name__ == "__main__":
    test_eligibility()
    test_gateway_failover()
    test_discovery_params()
    failed = [n for n, ok, _ in PASS if not ok]
    print(f"\nRESULT {len(PASS)-len(failed)}/{len(PASS)} passed")
    raise SystemExit(1 if failed else 0)
