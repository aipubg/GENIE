"""Diagnostic: what does Chrome actually report for a download?

Instead of guessing from files on disk, this listens to Chrome's own CDP events
(Browser.downloadWillBegin / Browser.downloadProgress) and prints the state and
file path Chrome reports. Run it twice to compare a clean browser against one
that has already downloaded — which is the difference between the passing and
failing runs.

    python scripts/diag_browser_download.py [--downloads N]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve() -> str:
    handler = functools.partial(QuietHandler, directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", type=int, default=1,
                    help="how many downloads to trigger in this run")
    ap.add_argument("--warmup", type=int, default=0,
                    help="navigations to perform first, mimicking the earlier tests "
                         "that drive the same browser before the download runs")
    args = ap.parse_args()

    lab = serve()
    from browser.service import get_browser
    from browser import cdp

    tmp = Path(tempfile.mkdtemp(prefix="genie-dl-diag-"))
    b = get_browser(port=9333, workspace_root=tmp)
    try:
        res = b.navigate({"url": f"{lab}/browser_lab.html", "wait_s": 30})
        if not res.get("ok"):
            print("browser unavailable:", res.get("verify"))
            return 2

        # browser-level CDP so download events are visible
        browser_ws = b._browser_ws_url()
        cli = cdp.CDPClient(browser_ws)
        # Mimic the real suite: by the time the download test runs, ~21 other tests
        # have navigated this same page/tab around.
        ok_nav = 0
        for w in range(args.warmup):
            for u in (f"{lab}/browser_lab.html", f"{lab}/test_site/index.html",
                      f"{lab}/test_site/about.html"):
                try:
                    if b.handle("browser.navigate", {"url": u, "wait_s": 15}).get("ok"):
                        ok_nav += 1
                except Exception as exc:
                    # Chrome/CDP drops the socket under sustained navigation
                    # (observed: WinError 10053). Record it, do not crash.
                    print(f"  warmup nav failed: {type(exc).__name__}: {exc}")
        if args.warmup:
            print(f"(warmup: {ok_nav}/{args.warmup * 3} navigations succeeded)")

        # The real test navigates to the lab page immediately before downloading.
        b.handle("browser.navigate", {"url": f"{lab}/browser_lab.html", "wait_s": 30})

        for i in range(args.downloads):
            target = tmp / f"dl{i}"
            target.mkdir(parents=True, exist_ok=True)
            cli.send("Browser.setDownloadBehavior",
                     {"behavior": "allow", "downloadPath": str(target),
                      "eventsEnabled": True}, timeout=10)
            print(f"\n--- download #{i + 1} -> {target}")
            try:
                out = b.handle("browser.download",
                               {"selector": "#download-link",
                                "directory": str(target), "timeout_s": 20})
            except Exception as exc:
                out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print("  handler  :", json.dumps({k: out.get(k) for k in
                                              ("ok", "error", "path", "filename")},
                                             ensure_ascii=False))
            print("  verify   :", out.get("verify"))
            for ev in cli.events(clear=True):
                m = ev.get("method", "")
                if "download" in m.lower():
                    print("  CDP event:", m, json.dumps(ev.get("params", {}),
                                                        ensure_ascii=False)[:300])
            time.sleep(1)
        return 0
    finally:
        try:
            b.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
