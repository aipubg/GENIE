"""Opt-in real CDP/ComputerService/Chat bridge acceptance on a local fixture.

No cloud model, owner profile, personal content or external account is used.
The scripted model validates orchestration, not natural-language model quality.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = b'''<!doctype html><html><body>
<button onclick="document.getElementById('panel').hidden=false;this.setAttribute('aria-expanded','true')" aria-expanded="false">Open panel</button>
<section id="panel" hidden>Panel opened successfully</section>
<input aria-label="Draft" placeholder="Draft">
<input aria-label="Draft" hidden>
<div contenteditable="true" aria-label="Rich draft">Old contents</div>
<input aria-label="Ambiguous"><input aria-label="Ambiguous">
<button aria-label="Accessible action" onclick="document.getElementById('label-result').textContent='Accessible action verified'">Decorative glyph</button>
<input type="button" aria-label="Input action" value="Go" onclick="document.getElementById('label-result').textContent='Input action verified'">
<p id="label-result"></p>
<button onclick="document.getElementById('sent').textContent='Dummy submission verified'">Send fixture</button><p id="sent"></p>
<button>Do nothing</button><button>Duplicate</button><button>Duplicate</button>
<a href="/destination">Destination</a>
</body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><title>Bridge destination</title><body>Destination reached</body></html>" if self.path == "/destination" else PAGE
        if self.path == "/password":
            body = b'<html><body><input type="password" value="fixture-secret-do-not-export"></body></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main(output=None):
    from browser import service as browser_service
    from computer.service import ComputerService
    from computer.tool_bridge import execute
    from core.contracts import CallContext, ModelRequirement, Completion
    from core.tool_protocol import normalize_completion
    from computer.tool_bridge import OPENAI_TOOLS
    from core.db import Database
    from core.tool_dialogue import run
    from security.trust import TrustService

    root = Path(tempfile.mkdtemp(prefix="genie-browser-bridge-"))
    db = Database(root / "test.db")
    trust = TrustService(db)
    computer = ComputerService(db=db, trust=trust, workspace_root=str(root / "workspace"), data_dir=str(root))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    browser = browser_service.BrowserService(port=port, workspace_root=computer.executor.workspace.root, headless=True)
    browser_service._BROWSER = browser
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ctx, cancel = CallContext(session_id="bridge-fixture"), threading.Event()
    receipts = []
    started = time.monotonic()

    def call(name, args):
        receipt = execute(computer, ctx, name, args, cancel)
        receipts.append({"tool": name, "ok": receipt.get("ok"), "verified": receipt.get("verified"),
                         "error_code": receipt.get("error_code"), "detail": receipt.get("detail")})
        return receipt

    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert call("read_web_page", {"url": url})["ok"]
        menu = call("browser_click", {"text": "Open panel"})
        assert menu["ok"] and menu["verified"] and menu["data"]["state_changed"]
        assert call("browser_fill", {"label": "Draft", "text": "local test only"})["verified"]
        assert call("browser_fill", {"label": "Draft", "text": "replacement exact"})["data"]["value"] == "replacement exact"
        assert call("browser_fill", {"label": "Rich draft", "text": "Exact rich draft"})["data"]["value"] == "Exact rich draft"
        assert not call("browser_fill", {"label": "Ambiguous", "text": "Must not type"})["ok"]
        observed = call("browser_observe", {})
        assert any(item["text"] == "Accessible action" for item in observed["data"]["interactive"])
        assert call("browser_click", {"text": "Accessible action"})["verified"]
        assert call("browser_click", {"text": "Input action"})["verified"]
        confirmations = []
        def approve_fixture(_ctx, summary, _cancel):
            assert url in summary and "Send fixture" in summary
            confirmations.append(summary)
            return {"ok": True}
        computer.approvals = SimpleNamespace(request=approve_fixture)
        assert call("browser_click", {"text": "Send fixture"})["verified"]
        assert len(confirmations) == 1
        noop = call("browser_click", {"text": "Do nothing"})
        assert not noop["ok"] and noop["error_code"] == "browser_action_unverified"
        ambiguous = call("browser_click", {"text": "Duplicate"})
        assert not ambiguous["ok"] and not ambiguous["data"]["input_dispatched"]

        class ScriptedGateway:
            def __init__(self):
                self.count = 0

            def complete(self, _ctx, _requirement, messages, **_kwargs):
                self.count += 1
                if self.count == 1:
                    return normalize_completion(Completion(text=json.dumps({"tool": "browser_click", "args": {"text": "Destination"}}),
                                                           model="fixture", provider_id="fixture"), OPENAI_TOOLS)
                receipt = json.loads(messages[-1]["content"])
                receipts.append({"tool": "browser_click_via_chat", "ok": receipt["ok"], "verified": receipt["verified"]})
                assert receipt["ok"] and receipt["verified"]
                assert receipt["data"]["url_after"] == url + "destination"
                return Completion(text="Destination verified.", model="fixture", provider_id="fixture")

        reply = run(ScriptedGateway(), computer, ctx, ModelRequirement(), [], cancel)
        assert reply == "Destination verified."
        call("read_web_page", {"url": url + "password"})
        assert "fixture-secret-do-not-export" not in json.dumps(call("browser_observe", {}))
        report = {"ok": True, "scope": "real headless browser, real owner grants, scripted Chat model; no GUI or Voice acceptance",
                  "seconds": round(time.monotonic() - started, 2), "receipts": receipts}
        destination = output or Path(__file__).resolve().parents[1] / "artifacts" / "browser_bridge_acceptance.json"
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        browser.shutdown()
        computer.desktop_awareness.stop()
        server.shutdown()
        server.server_close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    main(parser.parse_args().output)
