"""
Controlled SLOW OpenAI-compatible endpoint for the responsiveness acceptance
test (§11). Deliberately delays before answering so we can prove a slow
provider never freezes the WPF window.

    GET  /v1/models              -> one model
    POST /v1/chat/completions    -> sleeps DELAY seconds, then answers

Usage:  python scripts/slow_provider_server.py [port] [delay_seconds]
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
LOG = r"C:\Users\ghostt\AppData\Local\Temp\slow_provider.log"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        log(f"GET {self.path}")
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [
                {"id": "slow-model", "display_name": "Slow Model",
                 "owned_by": "test"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        log(f"POST {self.path} <- {raw[:120]!r}")
        if self.path.startswith("/v1/chat/completions"):
            # §5 model-level failover: this model always answers 403
            # permission_denied, so the router must fall through to another
            # saved model of the SAME provider rather than declaring the whole
            # provider dead.
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            if body.get("model") == "premium-model":
                log("  -> 403 permission_denied (paid plan required)")
                self._send(403, {"error": {
                    "message": "This premium model requires an active paid plan "
                               "or real deposited balance.",
                    "type": "permission_error", "code": "permission_denied"}})
                return
            log(f"  sleeping {DELAY}s ...")
            time.sleep(DELAY)
            log("  answering")
            self._send(200, {
                "id": "chatcmpl-slow",
                "object": "chat.completion",
                "choices": [{"index": 0,
                             "message": {"role": "assistant",
                                         "content": "slow hello"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                          "total_tokens": 3},
            })
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    log(f"=== slow provider server on {PORT}, delay={DELAY}s ===")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
