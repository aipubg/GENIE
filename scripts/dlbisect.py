"""Pytest plugin: probe the download after the Nth prior browser test (RC items 4-8).

The download test is #10 in collection order, so contamination must come from one
of the 9 tests before it. This attaches to the SAME browser singleton the tests
use (get_browser is process-wide) and, after the Nth test, captures full state
and attempts the download.

    DLBISECT_N=4 DLBISECT_OUT=artifacts/dl_n4.json \\
        pytest tests/e2e/test_browser_phase4.py -m real_machine -p dlbisect

Captures, per the owner's list: page URL, target id, browser target id, tab count,
document visibility/readyState, and at click time the element's existence,
connectedness, href, disabled state, rect, visibility, pointer-events, opacity,
z-index, document.elementFromPoint result, active element and scroll position.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

N = int(os.environ.get("DLBISECT_N", "0"))
OUT = Path(os.environ.get("DLBISECT_OUT", "artifacts/dlbisect.json"))

_seen = 0
_done = False

ELEMENT_PROBE = """(() => {
  const el = document.querySelector('#download-link');
  if (!el) return {exists: false};
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  return {
    exists: true,
    connected: el.isConnected,
    tag: el.tagName,
    href: el.getAttribute('href'),
    downloadAttr: el.hasAttribute('download'),
    disabled: !!el.disabled,
    rect: {x: r.left, y: r.top, w: r.width, h: r.height},
    center: {x, y},
    display: cs.display,
    visibility: cs.visibility,
    opacity: cs.opacity,
    pointerEvents: cs.pointerEvents,
    zIndex: cs.zIndex,
    hitTag: hit ? hit.tagName : null,
    hitId: hit ? hit.id : null,
    hitIsElement: !!hit && (hit === el || el.contains(hit) || hit.contains(el)),
    docVisibility: document.visibilityState,
    readyState: document.readyState,
    activeElement: document.activeElement ? document.activeElement.tagName : null,
    scrollY: window.scrollY,
    url: location.href,
  };
})()"""


def _write(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n[dlbisect] wrote {OUT}")


def _probe(item) -> None:
    from browser.service import get_browser
    from browser import cdp

    b = get_browser(port=9333)
    payload: dict = {"after_test": item.name if item else None, "n": N}
    try:
        # the real download test navigates to the lab page first
        lab = getattr(b, "lab_url", None)
        if lab:
            b.handle("browser.navigate", {"url": lab, "wait_s": 30})
        client = b._connect_page()
        payload["url"] = str(client.evaluate("location.href") or "")
        payload["element"] = client.evaluate(ELEMENT_PROBE)
        try:
            payload["tabs"] = len(cdp.page_targets(b.port))
        except Exception as exc:  # noqa: BLE001
            payload["tabs"] = f"error: {exc}"
        try:
            payload["browser_ws"] = b._browser_ws_url()[:80]
        except Exception as exc:  # noqa: BLE001
            payload["browser_ws"] = f"error: {exc}"

        payload["experiments"] = _experiments(b, client)
        target_dir = Path(tempfile.mkdtemp(prefix="dlbisect-")) / "downloads"
        res = b.handle("browser.download", {"selector": "#download-link",
                                            "directory": str(target_dir),
                                            "timeout_s": 20})
        payload["download_ok"] = res.get("ok")
        payload["download_error"] = res.get("error")
        payload["download_verify"] = res.get("verify")
    except Exception as exc:  # noqa: BLE001
        payload["probe_error"] = f"{type(exc).__name__}: {exc}"
    _write(payload)



def _vis(cli) -> str:
    try:
        return str(cli.evaluate("document.visibilityState") or "?")
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _try_download(b) -> dict:
    d = Path(tempfile.mkdtemp(prefix="dlbisect-x-")) / "downloads"
    try:
        r = b.handle("browser.download", {"selector": "#download-link",
                                          "directory": str(d), "timeout_s": 15})
        return {"ok": r.get("ok"), "error": r.get("error")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _experiments(b, client) -> dict:
    """Item 6: separate page/target, CDP-session and profile contamination."""
    from browser import cdp
    out = {}

    # baseline on the current target
    out["A_current_target"] = {"visibility": _vis(client),
                               "download": _try_download(b)}

    # B: same target, call Page.bringToFront, re-check visibility
    try:
        client.send("Page.bringToFront", timeout=5)
        time.sleep(0.5)
        out["B_bring_to_front"] = {"visibility": _vis(client),
                                   "download": _try_download(b)}
    except Exception as exc:  # noqa: BLE001
        out["B_bring_to_front"] = {"error": f"{type(exc).__name__}: {exc}"}

    # C: fresh CDP connection to the same target
    try:
        b._client = None
        fresh = b._connect_page()
        out["C_fresh_cdp_session"] = {"visibility": _vis(fresh),
                                      "download": _try_download(b)}
    except Exception as exc:  # noqa: BLE001
        out["C_fresh_cdp_session"] = {"error": f"{type(exc).__name__}: {exc}"}

    # D: brand-new tab (new target) in the same browser/profile
    try:
        newp = cdp.new_page(b.port, getattr(b, "lab_url", "about:blank") or "about:blank")
        time.sleep(1.0)
        pages = cdp.page_targets(b.port)
        lab = getattr(b, "lab_url", None)
        cli = cdp.CDPClient(pages[-1]["webSocketDebuggerUrl"]) if pages else None
        if cli and lab:
            cli.send("Page.enable")
            cli.send("Page.navigate", {"url": lab})
            time.sleep(1.5)
        out["D_new_tab"] = {"visibility": _vis(cli) if cli else "no client",
                            "download": _try_download(b) if cli else "skipped"}
    except Exception as exc:  # noqa: BLE001
        out["D_new_tab"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def pytest_runtest_teardown(item, nextitem):
    global _seen, _done
    if _done or N <= 0:
        return
    if not item.get_closest_marker("real_machine"):
        return
    _seen += 1
    if _seen == N:
        _done = True
        _probe(item)


def pytest_sessionfinish(session, exitstatus):
    if not _done and N == 0:
        _probe(None)
