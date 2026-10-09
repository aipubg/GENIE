"""Verify that the post-fix ops pages still render correctly and that the
Devices disclosure, when expanded, shows the grouped capability chips
(rather than the old raw comma-separated dump).  Writes to ui/screenshots/."""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[3] / "ui" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8787"
REF_W, REF_H = 1536, 1024

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": REF_W, "height": REF_H},
                              device_scale_factor=1)

    # 1. Devices — expanded disclosure (the real visual of fix #2)
    page = ctx.new_page()
    page.goto(BASE + "/ui/devices.html", wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(1200)
    # Open the only details on the page.
    page.evaluate("document.querySelectorAll('details.details').forEach(d => d.open = true)")
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "devices-expanded.png"))
    print("captured devices-expanded.png")
    page.close()

    # 2. Agents — regression check on the humanList() sweep fix.
    page = ctx.new_page()
    page.goto(BASE + "/ui/agents.html", wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "agents.png"))
    print("captured agents.png")
    page.close()

    # 3. Security — regression check on humanList() for reported_by.
    page = ctx.new_page()
    page.goto(BASE + "/ui/security.html", wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "security.png"))
    print("captured security.png")
    page.close()

    # 4. Smoke-test all ops routes return 200 (and Home too).
    routes = ["/", "/ui/home.html", "/ui/chat.html", "/ui/missions.html",
              "/ui/agents.html", "/ui/computer.html", "/ui/skills.html",
              "/ui/devices.html", "/ui/memory.html", "/ui/knowledge.html",
              "/ui/media.html", "/ui/forecast.html", "/ui/security.html",
              "/ui/settings.html", "/ui/compact.html"]
    for r in routes:
        try:
            resp = ctx.request.fetch(BASE + r)
            ok = "OK" if resp.status == 200 else f"FAIL({resp.status})"
            print(f"  {ok:8s}  {r}")
        except Exception as e:
            print(f"  ERR      {r}  {e}")

    browser.close()
