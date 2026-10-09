"""Capture REAL Chromium screenshots of the GENIE locked UI.

These are rendered by an actual browser engine, never DOM serialization.

    python ui/tests/visual/capture.py            # capture everything
    python ui/tests/visual/capture.py --only home-idle

The daemon must be running:  python genie.py daemon
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT  = Path(__file__).resolve().parents[3]      # E:/G3/GENIE-worktrees/...
OUT   = ROOT / "ui" / "screenshots"
BASE  = "http://127.0.0.1:8787"

# Reference viewport of the approved design: 1536x1024.
REF_W, REF_H = 1536, 1024

SHOTS = [
    # name,              url,                    viewport,        action
    ("home-idle",        "/ui/home.html",        (REF_W, REF_H), None),
    ("home-companion",   "/ui/home.html",        (REF_W, REF_H), "companion-idle"),
    ("home-listening",   "/ui/home.html",        (REF_W, REF_H), "voice-listening"),
    ("home-thinking",    "/ui/home.html",        (REF_W, REF_H), "voice-thinking"),
    ("home-speaking",    "/ui/home.html",        (REF_W, REF_H), "voice-speaking"),
    ("home-narrow",      "/ui/home.html",        (1100, 820),    None),
    ("home-narrower",    "/ui/home.html",        (860,  760),    None),
    ("home-quality-low", "/ui/home.html?quality=LOW", (REF_W, REF_H), None),
    ("home-reduced-motion", "/ui/home.html",     (REF_W, REF_H), "reduced-motion"),
    ("home-offline",     "/ui/home.html",        (REF_W, REF_H), "offline"),
]

# Operational pages (item 24) — captured at the same reference viewport.
OPS_PAGES = ["chat", "missions", "agents", "computer", "skills", "devices",
             "memory", "knowledge", "media", "forecast", "security", "settings"]
SHOTS += [(p, f"/ui/{p}.html", (REF_W, REF_H), None) for p in OPS_PAGES]

# Compact floating mode (item 40) — captured at its real window size.
SHOTS += [
    ("compact-idle", "/ui/compact.html", (320, 420), None),
    ("compact-listening", "/ui/compact.html", (320, 420), "voice-listening"),
]


def act(page, action):
    """Drive a UI state through the page's own public hooks.

    These set VISUAL state only. They never fabricate backend mission/agent
    data; offline genuinely blocks the backend so the UI shows its real
    degraded state.
    """
    if action is None:
        return
    if action == "companion-idle":
        page.evaluate("window.__genieCompanion && window.__genieCompanion.setState('idle')")
    elif action == "voice-listening":
        page.evaluate("window.__genieHome && window.__genieHome.setVoiceMode('listening')")
        page.evaluate("document.documentElement.style.setProperty('--voice-level','0.55')")
    elif action == "voice-thinking":
        page.evaluate("window.__genieHome && window.__genieHome.setVoiceMode('processing')")
        page.evaluate("window.__genieCompanion && window.__genieCompanion.setState('thinking')")
    elif action == "voice-speaking":
        page.evaluate("window.__genieHome && window.__genieHome.setVoiceMode('speaking')")
        page.evaluate("window.__genieCompanion && window.__genieCompanion.setState('speaking')")
    elif action == "reduced-motion":
        page.emulate_media(reduced_motion="reduce")
    elif action == "offline":
        # Genuinely cut the backend so the UI renders its true offline state.
        page.route("**/api/**", lambda route: route.abort())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="capture a single shot by name")
    ap.add_argument("--out",  default=str(OUT))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    shots = [s for s in SHOTS if args.only in (None, s[0])]
    if not shots:
        print(f"no shot named {args.only!r}", file=sys.stderr)
        return 2

    made = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, url, (w, h), action in shots:
            ctx = browser.new_context(viewport={"width": w, "height": h},
                                      device_scale_factor=1)
            page = ctx.new_page()
            if action == "reduced-motion":
                ctx_reduced = browser.new_context(
                    viewport={"width": w, "height": h},
                    reduced_motion="reduce")
                page = ctx_reduced.new_page()

            try:
                page.goto(BASE + url, wait_until="networkidle", timeout=20000)
            except Exception:
                page.goto(BASE + url, wait_until="load", timeout=20000)

            act(page, action)
            page.wait_for_timeout(1600)      # let startup/emergence settle

            target = out / f"{name}.png"
            page.screenshot(path=str(target))
            made.append(target)
            print(f"  captured  {target}")
            ctx.close()

        browser.close()

    print(f"\n{len(made)} screenshot(s) in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())