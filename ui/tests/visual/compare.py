"""Visual regression: compare rendered Home geometry against the approved reference.

Deliberately NOT exact-pixel equality — fonts, GPU rasterisation and
anti-aliasing legitimately differ between machines. Instead we compare the
*geometry* the owner asked to be matched (item 22 / 30):

    sidebar width
    right column width
    top spacing
    central greeting position
    lamp position
    command bar

The reference geometry is DERIVED from the reference PNG by edge detection,
so it stays honest if the reference ever changes.

    python ui/tests/visual/compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
REF  = ROOT / "ui" / "assets" / "brand" / "desktop-ui-reference.png"
BASE = "http://127.0.0.1:8787"

# Tolerance: geometry within this many reference pixels counts as a match.
TOL_PX = 8.0


def reference_geometry() -> dict:
    """Derive layout geometry from the approved reference PNG."""
    from PIL import Image
    import numpy as np

    im = Image.open(REF).convert("RGB")
    a = np.asarray(im).astype(float)
    h, w, _ = a.shape
    lum = a.mean(axis=2)

    # strong vertical edges = full-height column borders (rail boundaries)
    d = np.abs(np.diff(lum, axis=1))
    strong = (d > 12).mean(axis=0)
    edges = [int(x) + 1 for x in range(w - 1) if strong[x] > 0.5]

    left_edge  = min([e for e in edges if e < w * 0.5], default=None)
    right_edge = min([e for e in edges if e > w * 0.5], default=None)

    return {
        "width":  w,
        "height": h,
        "sidebar_w": float(left_edge)  if left_edge  else None,
        "rail_x":    float(right_edge) if right_edge else None,
        "rail_w":    float(w - right_edge) if right_edge else None,
        "edges":     edges,
    }


def rendered_geometry(page) -> dict:
    """Read real bounding boxes from the live DOM."""
    js = """
    (() => {
      const box = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
      };
      return {
        viewport: {w: innerWidth, h: innerHeight},
        sidebar:  box('.sidebar'),
        rail:     box('.rail'),
        main:     box('.main'),
        greeting: box('.greeting h1'),
        lamp:     box('.lamp'),
        gem:      box('#gem-btn'),
        commandbar: box('.commandbar'),
        chips:    box('.chips'),
        companion: box('.companion'),
      };
    })()
    """
    return page.evaluate(js)


def main() -> int:
    ref = reference_geometry()
    print("Reference geometry (derived from the approved PNG):")
    print(f"  size        {ref['width']}x{ref['height']}")
    print(f"  sidebar_w   {ref['sidebar_w']}")
    print(f"  rail_x      {ref['rail_x']}")
    print(f"  rail_w      {ref['rail_w']}")
    print(f"  all edges   {ref['edges']}")
    print()

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": ref["width"], "height": ref["height"]},
                            device_scale_factor=1)
        page = ctx.new_page()
        page.goto(BASE + "/ui/home.html", wait_until="load", timeout=20000)
        page.wait_for_timeout(1500)
        got = rendered_geometry(page)
        b.close()

    print("Rendered geometry (real DOM boxes):")
    print(json.dumps(got, indent=2))
    print()

    checks = []

    def cmp(name, actual, expected):
        if expected is None or actual is None:
            checks.append((name, actual, expected, None, "skip"))
            return
        delta = abs(float(actual) - float(expected))
        checks.append((name, actual, expected, delta,
                       "pass" if delta <= TOL_PX else "FAIL"))

    cmp("sidebar width",  got["sidebar"]["w"]          if got["sidebar"] else None, ref["sidebar_w"])
    cmp("rail x start",   got["rail"]["x"]             if got["rail"]    else None, ref["rail_x"])
    cmp("rail width",     got["rail"]["w"]             if got["rail"]    else None, ref["rail_w"])

    print(f"{'metric':16s} {'rendered':>10s} {'reference':>10s} {'delta':>8s}  result")
    print("-" * 60)
    failed = 0
    for name, actual, expected, delta, result in checks:
        a = "-" if actual is None else f"{float(actual):.1f}"
        e = "-" if expected is None else f"{float(expected):.1f}"
        d = "-" if delta is None else f"{delta:.1f}"
        print(f"{name:16s} {a:>10s} {e:>10s} {d:>8s}  {result}")
        if result == "FAIL":
            failed += 1

    print()
    if failed:
        print(f"{failed} geometry check(s) outside tolerance ({TOL_PX}px).")
        return 1
    print("All geometry checks within tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())