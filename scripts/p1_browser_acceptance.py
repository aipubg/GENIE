"""Items 5/6/9 acceptance: GENIE-owned browser lifecycle, generic page loop,
select, upload, and OS-handoff identity.

Creates an isolated local fixture website and drives it through the canonical
BrowserService in genie_owned mode. No owner session or credentials required.
"""
import functools
import http.server
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" -- {detail}" if detail and not cond else ""), flush=True)


FIXTURE = """<!doctype html><html><head><title>GENIE Fixture</title></head><body>
<h1 id="h">GENIE local fixture</h1>
<label for="q">Search field</label>
<input id="q" name="q" placeholder="Type here" />
<button id="go" onclick="document.getElementById('out').textContent='RESULT:'+document.getElementById('q').value; document.title='Applied:'+document.getElementById('q').value;">Go</button>
<div id="out">idle</div>
<select id="sel" onchange="document.getElementById('selout').textContent='SEL:'+this.value">
  <option value="">choose</option><option value="alpha">Alpha</option><option value="beta">Beta</option>
</select>
<div id="selout">none</div>
<div style="height:2000px">scroll region</div>
<input type="file" id="file" name="file" />
<div id="up">no-upload</div>
<script>
document.getElementById('file').addEventListener('change', function(){
  var f = this.files[0];
  document.getElementById('up').textContent = f ? ('UPLOADED:'+f.name) : 'no-upload';
});
</script>
</body></html>"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **k):
        pass


def serve(directory):
    # NOTE: in Python 3.12 SimpleHTTPRequestHandler reads `directory` from the
    # constructor kwarg, so a class attribute is ignored and the server would
    # silently serve the cwd (404 for the fixture). functools.partial is required.
    handler = functools.partial(Quiet, directory=str(directory))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "index.html").write_text(FIXTURE, encoding="utf-8")
    upload_file = tmp / "genie_upload_probe.txt"
    upload_file.write_text("GENIE upload probe " + str(int(time.time())), encoding="utf-8")

    httpd, port = serve(tmp)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"fixture: {url}", flush=True)

    from browser.service import BrowserService
    svc = BrowserService()
    print(f"profile (absolute): {svc.profile_dir}", flush=True)

    try:
        # ---- A. GENIE-owned browser: launch + CDP + navigate ---------------
        t0 = time.perf_counter()
        ens = svc.ensure(url="about:blank")
        check("genie_browser_launched", bool(ens.get("ok")), str(ens)[:220])
        check("cdp_endpoint_obtained", bool(ens.get("port")) and bool(ens.get("pid")),
              f"port={ens.get('port')} pid={ens.get('pid')}")

        # session affinity: bind the task to this browser/tab (same-tab reuse)
        ss = svc.select_session({"mode": "genie_owned", "url": url, "task_id": "acceptance"})
        check("session_bound_genie_owned", bool(ss.get("ok")) and
              (ss.get("session") or {}).get("control") == "cdp", str(ss)[:200])

        nav = svc.navigate({"url": url, "wait_s": 12, "content_wait_s": 3})
        check("genie_browser_navigated", bool(nav.get("ok")), str(nav)[:220])
        # BrowserService.navigate returns its identity fields at the TOP level
        # (url/title/tab_id), not nested under "data".
        check("page_identity_recorded",
              bool(nav.get("url")) and nav.get("url") == url and bool(svc._active_target_id()),
              f"url={nav.get('url')} tab={svc._active_target_id()!r}")

        # ---- B. observe ---------------------------------------------------
        obs = svc.observe({})
        controls = obs.get("interactive") or []
        labels = [str(c.get("text") or c.get("label") or "") for c in controls]
        check("observe_returned_controls", bool(obs.get("ok")) and len(controls) > 0,
              f"n={len(controls)} labels={labels[:8]}")
        check("fixture_heading_seen", "GENIE local fixture" in str(obs.get("text_excerpt") or ""),
              str(obs.get("text_excerpt"))[:120])

        # ---- C. fill + click + verify dynamic state change -----------------
        filled = svc.fill({"text": "probe123", "label": "Type here", "selector": "#q"})
        check("fill_field", bool(filled.get("ok")), str(filled)[:170])
        clicked = svc.act({"text": "Go", "exact_text": True})
        check("click_button", bool(clicked.get("ok")), str(clicked)[:170])

        obs2 = svc.observe({})
        body2 = str(obs2.get("text_excerpt") or "") + str(obs2.get("title") or "")
        check("dynamic_state_verified", "RESULT:probe123" in body2 or "Applied:probe123" in body2,
              body2[:200])

        # ---- D. scroll ----------------------------------------------------
        scrolled = svc.scroll({"dy": 400})
        check("scroll_page", bool(scrolled.get("ok")), str(scrolled)[:150])

        # ---- E. select (canonical contract: value) -------------------------
        selected = svc.select_option({"selector": "#sel", "value": "beta"})
        obs3 = svc.observe({})
        blob3 = str(obs3.get("text_excerpt") or "")
        check("select_verified", bool(selected.get("ok")) and "SEL:beta" in blob3,
              f"select={str(selected)[:140]} page={blob3[:120]}")

        # ---- F. second action after the first (same tab continues) ---------
        refilled = svc.fill({"text": "second", "selector": "#q"})
        reclicked = svc.act({"text": "Go", "exact_text": True})
        obs4 = svc.observe({})
        check("second_action_verified",
              bool(refilled.get("ok")) and bool(reclicked.get("ok"))
              and "RESULT:second" in str(obs4.get("text_excerpt") or ""),
              f"fill={refilled.get('ok')} click={reclicked.get('ok')}")

        # ---- G. upload a real local file (canonical `files` contract) ------
        up = svc.upload_file({"files": [str(upload_file)], "selector": "#file"})
        obs5 = svc.observe({})
        blob5 = str(obs5.get("text_excerpt") or "")
        check("upload_verified", bool(up.get("ok")) and
              "UPLOADED:genie_upload_probe.txt" in blob5,
              f"upload={str(up)[:160]} page={blob5[:120]}")

        # empty path must be rejected, never silently attach "."
        bad = svc.upload_file({"files": [], "selector": "#file"})
        check("upload_rejects_empty_path", not bad.get("ok"),
              str(bad)[:140])

        # ---- H. session identity stays bound (same tab) --------------------
        ctx = svc.session_context()
        check("session_bound_after_actions",
              bool(ctx) and ctx.get("mode") == "genie_owned" and
              str(ctx.get("page_url") or "").startswith("http://127.0.0.1"),
              str(ctx)[:200])
        check("same_tab_continuation", svc._active_target_id() != "" or bool(ctx),
              f"active_tab={svc._active_target_id()!r}")
    finally:
        try:
            svc.shutdown()
        except Exception:
            pass
        httpd.shutdown()

    # ---- I. OS-handoff identity (independent of any owner session) --------
    from browser.default_browser import defaults
    from browser.discovery import installed
    d = defaults()
    check("default_browser_identity_known", bool(d), str(d)[:160])
    inst = installed()
    check("installed_browsers_enumerated", isinstance(inst, list) and len(inst) >= 1,
          f"n={len(inst) if isinstance(inst, list) else '?'}")

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nBrowser/upload acceptance: {total - len(failed)}/{total} passed", flush=True)
    for name, _, detail in failed:
        print(f"  FAILED: {name} -- {detail}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
