"""Opt-in real browser and Laya checks, using a disposable owned browser profile."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from browser import service as browser_service
    from computer.service import ComputerService
    from core.contracts import CallContext
    from core.db import Database
    from computer.tool_bridge import execute
    root = Path(tempfile.mkdtemp(prefix="genie-connectivity-"))
    computer = ComputerService(db=Database(root / "test.db"), workspace_root=str(root / "workspace"), data_dir=str(root))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    browser = browser_service.BrowserService(port=port, workspace_root=computer.executor.workspace.root, headless=True)
    browser_service._BROWSER = browser
    try:
        start = time.monotonic()
        result = execute(computer, CallContext(), "search_web", {"query": "OpenAI ChatGPT official"}, threading.Event())
        report = {"search_ok": result.get("ok"), "seconds": round(time.monotonic() - start, 2),
                  "error_code": result.get("error_code"), "error": result.get("error"),
                  "source_url": result.get("source_url")}
        print(json.dumps(report, ensure_ascii=True, indent=2))
        assert result.get("ok"), result
    finally:
        browser.shutdown()
        computer.desktop_awareness.stop()


if __name__ == "__main__":
    main()
