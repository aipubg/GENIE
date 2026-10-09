"""
Real YouTube action through GENIE's existing browser/CDP authority.

Deterministic flow (no fragile homepage selector):
    navigate to YouTube search results
    wait for DOM readiness
    extract candidate results via DOM evaluation
    pick a sensible match
    open it
    attempt playback
    VERIFY playback from the media element (paused + currentTime advances)

Prints a JSON evidence block. Never fabricates success.
"""
from __future__ import annotations

import json
import sys
import time
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

QUERY_DEFAULT = "Barsaat trending song"

JS_RESULTS = """
(() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a#video-title, a.yt-simple-endpoint[href^="/watch"], ytd-video-renderer a#thumbnail').forEach(a => {
    const href = a.getAttribute('href') || '';
    const title = (a.getAttribute('title') || a.textContent || '').trim();
    if (!href.startsWith('/watch')) return;
    if (seen.has(href)) return;
    seen.add(href);
    out.push({href, title});
  });
  return {count: out.length, items: out.slice(0, 12),
          url: location.href, title: document.title,
          bodyLen: (document.body && document.body.innerText || '').length};
})()
"""

JS_VIDEO = """
(() => {
  const v = document.querySelector('video');
  if (!v) return {present: false, url: location.href, title: document.title};
  return {present: true, paused: v.paused, currentTime: v.currentTime,
          readyState: v.readyState, duration: v.duration,
          muted: v.muted, url: location.href, title: document.title};
})()
"""

JS_PLAY = """
(() => {
  const v = document.querySelector('video');
  if (!v) return {ok:false, reason:'no video element'};
  try { v.muted = false; } catch(e) {}
  try { v.play(); } catch(e) { return {ok:false, reason:String(e)}; }
  return {ok:true, paused: v.paused};
})()
"""


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else QUERY_DEFAULT
    from browser.service import get_browser
    from core.config import get_config

    cfg = get_config()
    b = get_browser(workspace_root=cfg.data_dir / cfg.get("computer.workspace", "workspace"))

    ev: dict = {"query": query}

    st = b.status()
    ev["browser"] = {"pid": st.get("pid"), "connected": st.get("connected"),
                     "browser": st.get("browser"), "exe": st.get("exe")}

    search_url = "https://www.youtube.com/results?search_query=" + \
        query.replace(" ", "+")
    ev["search_url"] = search_url

    nav = b.navigate({"url": search_url, "wait_s": 30, "content_wait_s": 15})
    ev["navigate"] = {"ok": nav.get("ok"), "url": nav.get("url"),
                      "title": nav.get("title"),
                      "verify": nav.get("verify")}

    # DOM readiness: poll until real results exist.
    results = None
    for _ in range(20):
        try:
            client = b._connect_page()
            results = client.evaluate(JS_RESULTS) or {}
        except Exception as exc:
            results = {"error": str(exc)}
        if results.get("count"):
            break
        time.sleep(1.5)
    ev["search_results"] = results

    items = (results or {}).get("items") or []
    pick = None
    for it in items:
        if "barsaat" in (it.get("title", "") + it.get("href", "")).lower():
            pick = it
            break
    if pick is None and items:
        pick = items[0]
    ev["selected"] = pick

    if not pick:
        ev["playback"] = {"verified": False,
                          "detail": "no search result found"}
        print(json.dumps(ev, ensure_ascii=False, indent=2))
        return 1

    watch_url = "https://www.youtube.com" + pick["href"]
    ev["watch_url"] = watch_url
    nav2 = b.navigate({"url": watch_url, "wait_s": 30, "content_wait_s": 15})
    ev["open_video"] = {"ok": nav2.get("ok"), "url": nav2.get("url"),
                        "title": nav2.get("title"), "verify": nav2.get("verify")}

    # Playback verification: pause/play then prove currentTime advances.
    v1 = v2 = None
    for _ in range(15):
        try:
            client = b._connect_page()
            v1 = client.evaluate(JS_VIDEO) or {}
        except Exception as exc:
            v1 = {"error": str(exc)}
        if v1.get("present"):
            break
        time.sleep(1.5)

    play_attempt = None
    if v1 and v1.get("present") and v1.get("paused"):
        try:
            client = b._connect_page()
            play_attempt = client.evaluate(JS_PLAY)
        except Exception as exc:
            play_attempt = {"ok": False, "reason": str(exc)}
        time.sleep(2.0)

    t1 = None
    for _ in range(10):
        try:
            client = b._connect_page()
            s = client.evaluate(JS_VIDEO) or {}
        except Exception as exc:
            s = {"error": str(exc)}
        if s.get("present"):
            t1 = s
            break
        time.sleep(1.5)
    time.sleep(3.0)
    try:
        client = b._connect_page()
        t2 = client.evaluate(JS_VIDEO) or {}
    except Exception as exc:
        t2 = {"error": str(exc)}

    ct1 = (t1 or {}).get("currentTime")
    ct2 = (t2 or {}).get("currentTime")
    advanced = (ct1 is not None and ct2 is not None and ct2 > ct1)
    paused = (t2 or {}).get("paused")
    verified = bool(advanced and paused is False)

    ev["playback"] = {
        "first_observation": t1,
        "second_observation": t2,
        "play_attempt": play_attempt,
        "currentTime_advanced": advanced,
        "verified": verified,
        "detail": (f"currentTime {ct1} -> {ct2}, paused={paused}"
                   if ct1 is not None else "no media element observed"),
    }

    try:
        st2 = b.status()
        ev["computer_preview"] = {"pages": st2.get("pages"),
                                  "connected": st2.get("connected"),
                                  "state": st2.get("state")}
    except Exception:
        pass

    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
