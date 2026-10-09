"""Read-only production bridge probe; never logs private control text or sends input."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(window, window_id=0):
    from computer.service import ComputerService
    from computer.tool_bridge import execute
    from computer import windows_api
    from core.contracts import CallContext
    from core.db import Database
    from security.trust import TrustService

    with tempfile.TemporaryDirectory(prefix="genie-uia-probe-") as directory:
        db = Database(Path(directory) / "test.db")
        service = ComputerService(db=db, trust=TrustService(db), data_dir=directory,
                                  workspace_root=str(Path(directory) / "workspace"))
        started = time.monotonic()
        try:
            result = execute(service, CallContext(), "desktop_controls", {"window": window, "window_id": window_id}, threading.Event())
            data = result.get("data") or {}
            candidates = [{"hwnd": w.hwnd, "class": w.class_name, "pid": w.pid,
                           "minimized": w.minimized, "rect": w.rect}
                          for w in windows_api.list_windows() if w.title == window]
            report = {"window": window, "candidates": candidates, "ok": result.get("ok"), "verified": result.get("verified"),
                      "error_code": result.get("error_code"), "error": result.get("error"),
                      "elapsed_s": round(time.monotonic() - started, 2), "count": data.get("count", 0),
                      "types": sorted({e["control_type"] for e in data.get("elements", [])}),
                      "scope": "real window, direct shared bridge; read only, no private text retained"}
            (ROOT / "artifacts" / "window-controls-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
        finally:
            service.desktop_awareness.stop()
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", required=True)
    parser.add_argument("--window-id", type=int, default=0)
    args = parser.parse_args()
    main(args.window, args.window_id)
