"""Minimal OpenAI-compatible server used to prove the Provider Gateway really
calls the selected provider + model (rc14 acceptance, section 24).

It behaves like an OpenAI-compatible endpoint well enough for:
    POST /v1/chat/completions
    GET  /v1/models

Every request is recorded to <statefile> so a test can assert which model the
gateway actually used, and which Authorization header it sent (without ever
logging the secret into the repo).

Usage:
    python tests/fixtures/openai_compat_server.py <port> <statefile>
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("artifacts/openai_stub_state.json")
_lock = threading.Lock()


def _record(entry: dict) -> None:
    with _lock:
        existing = []
        if STATE.exists():
            try:
                existing = json.loads(STATE.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(entry)
        STATE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep the test output clean
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {
                "object": "list",
                "data": [
                    {"id": "stub-model", "object": "model", "owned_by": "genie-test"},
                ],
            })
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        auth = self.headers.get("Authorization") or ""
        _record({
            "path": self.path,
            "model": payload.get("model"),
            "messages": payload.get("messages"),
            # record only that a bearer token was present, never the value
            "auth_scheme": auth.split(" ")[0] if auth else None,
            "auth_present": bool(auth),
        })
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._json(200, {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "stub-model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "STUB_REPLY"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        else:
            self._json(404, {"error": {"message": "not found"}})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text("[]", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"openai-compat stub listening on http://127.0.0.1:{port}  state={STATE}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
