"""Phase 1 closure — reusable website-interaction primitives + second-page reuse.

Exercises the general primitives (observe / fill / act / verify / detect_gate)
against an ISOLATED LOCAL test page, then repeats the SAME primitives on a second
(remote) page to prove they are reusable, not site-specific.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_prim_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    from core.config import get_config
    from browser.service import get_browser

    cfg = get_config()
    b = get_browser(workspace_root=cfg.data_dir / cfg.get("computer.workspace", "workspace"))
    local = (REPO / "tests" / "fixtures" / "primitives_test.html").as_uri()

    print("=" * 70)
    print("Phase 1 — reusable website primitives (+ second-page reuse)")
    print("=" * 70)

    # ---- page 1: isolated local page ----
    nav = b.navigate({"url": local, "wait_s": 20, "content_wait_s": 6})
    check("navigate local test page", bool(nav.get("ok")),
          (nav.get("verify") or {}).get("detail", "")[:70])

    obs = b.observe({})
    tags = {str(e.get("tag")) for e in (obs.get("interactive") or [])}
    check("observe returns url/title/controls",
          obs.get("ok") and "Primitives Test" in str(obs.get("title", ""))
          and {"input", "button", "a"} <= tags,
          f"title={obs.get('title')!r} tags={sorted(tags)}")
    check("observe reports no gate on a plain page",
          (obs.get("gate") or {}).get("gate") == "",
          str((obs.get("gate") or {}).get("detail"))[:50])

    fill = b.fill({"selector": "#q", "text": "hello genie"})
    check("fill enters text into a verified editable field",
          bool(fill.get("ok")), (fill.get("verify") or {}).get("detail", "")[:60])

    act = b.act({"selector": "#go", "expect_selector": "#out"})
    check("act activates a control and observes the effect",
          bool(act.get("ok")), (act.get("verify") or {}).get("detail", "")[:70])

    ver = b.verify({"text": "clicked:hello genie"})
    check("verify confirms the expected result appeared",
          bool(ver.get("ok")), (ver.get("verify") or {}).get("detail", "")[:70])

    # ---- page 2: the SAME primitives on a different page (reuse proof) ----
    act2 = b.act({"text": "Example Link"})
    check("act follows a link (navigation verified)",
          bool(act2.get("ok")), (act2.get("verify") or {}).get("detail", "")[:70])

    obs2 = b.observe({})
    check("observe works on the second page",
          obs2.get("ok") and "example" in str(obs2.get("url", "")).lower(),
          str(obs2.get("url"))[:60])

    ver2 = b.verify({"text": "Example Domain", "url_contains": "example.com"})
    check("verify works on the second page (reuse proven)",
          bool(ver2.get("ok")), (ver2.get("verify") or {}).get("detail", "")[:80])

    # ---- gate detection on a sign-in-style page ----
    gate = b.detect_gate({})
    check("detect_gate returns a well-formed result",
          isinstance(gate.get("gate"), str), f"gate={gate.get('gate')!r}")

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
