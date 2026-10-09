"""RC13 frontend freeze gate (directive §12 + §13).

1. Re-captures ONLY the pages affected by the last fixes — the humanValue()/
   humanList() split plus the Memory (`hits`) and Workspace contract fixes:
   memory, computer, security.
2. Runs one global smoke over every operational route:
     - route returns 200
     - no console JS exception / page error
     - rendered NORMAL-mode text has no "[object Object]", "undefined", "NaN"
       and no raw epoch timestamp

Advanced-only rows are hidden in Normal mode, so innerText is the right surface
to scan (it excludes hidden elements).

The daemon must be running:  python genie.py daemon
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "ui" / "screenshots"
BASE = "http://127.0.0.1:8787"
REF_W, REF_H = 1536, 1024

# Pages directly affected by these last fixes (§12).
RECAPTURE = ["memory", "computer", "security"]

# Every operational shell + Home (§13).
ROUTES = ["/ui/home.html", "/ui/chat.html", "/ui/missions.html", "/ui/agents.html",
          "/ui/computer.html", "/ui/skills.html", "/ui/devices.html",
          "/ui/memory.html", "/ui/knowledge.html", "/ui/media.html",
          "/ui/forecast.html", "/ui/security.html", "/ui/settings.html"]

BANNED = ["[object Object]", "undefined", "NaN"]
EPOCH_RE = re.compile(r"\b1[6-9]\d{11}\b")      # raw epoch ms, e.g. 1789817079202


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": REF_W, "height": REF_H},
                                  device_scale_factor=1)

        # ---- §12 targeted re-capture --------------------------------------
        print("== targeted re-capture (§12) ==")
        for name in RECAPTURE:
            page = ctx.new_page()
            page.goto(f"{BASE}/ui/{name}.html", wait_until="networkidle", timeout=20000)
            page.wait_for_timeout(1500)
            target = OUT / f"{name}.png"
            page.screenshot(path=str(target))
            print(f"  captured  {target.name}")
            page.close()

        # ---- §13 global smoke ---------------------------------------------
        print("\n== global smoke (§13) ==")
        for r in ROUTES:
            page = ctx.new_page()
            errors: list[str] = []

            def on_console(msg, _errors=errors):
                if msg.type == "error":
                    _errors.append(f"console.error: {msg.text}")

            def on_pageerror(exc, _errors=errors):
                _errors.append(f"pageerror: {exc}")

            page.on("console", on_console)
            page.on("pageerror", on_pageerror)

            try:
                resp = page.goto(BASE + r, wait_until="networkidle", timeout=20000)
                status = resp.status if resp else "?"
            except Exception as exc:
                failures.append(f"{r}: navigation error {exc}")
                print(f"  FAIL  {r}  navigation error: {exc}")
                page.close()
                continue

            page.wait_for_timeout(1200)
            text = page.inner_text("body")
            page.close()

            problems = []
            if status != 200:
                problems.append(f"status={status}")
            for bad in BANNED:
                if bad in text:
                    problems.append(f"contains {bad!r}")
            m = EPOCH_RE.search(text)
            if m:
                problems.append(f"raw epoch {m.group(0)}")
            if errors:
                problems.append("js: " + "; ".join(errors[:3]))

            if problems:
                failures.append(f"{r}: " + ", ".join(problems))
                print(f"  FAIL  {r}  " + ", ".join(problems))
            else:
                print(f"  OK    {r}  (200, clean)")

        browser.close()

    print()
    if failures:
        print(f"GATE FAILED — {len(failures)} problem(s):")
        for f in failures:
            print("  - " + f)
        return 1
    print(f"GATE PASSED — {len(ROUTES)} routes clean, {len(RECAPTURE)} pages re-captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
