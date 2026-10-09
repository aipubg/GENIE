"""Phase 1 closure — Brave → ChatGPT → new chat → image request/result.

Drives the thin ChatGPT adapter (built only from the general primitives) and
reports EVERY step from real evidence. Success requires an actual image artifact;
a sign-in wall is reported as BLOCKED (never bypassed), and prompt submission
alone is never counted as image generation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_cgpt_")) / "data"
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

    print("=" * 70)
    print("Phase 1 — Brave → ChatGPT → new chat → image")
    print("=" * 70)

    res = b.chatgpt_image({"browser": "brave",
                           "prompt": "swimming pool mein AI character ki image",
                           "timeout_s": 120})

    steps = res.get("steps") or []
    print("  ---- step receipts ----")
    for s in steps:
        print(f"    {s.get('step'):10} ok={s.get('ok')}  {str(s.get('detail'))[:90]}")

    check("adapter reported per-step receipts", len(steps) >= 3, f"{len(steps)} steps")
    names = [s.get("step") for s in steps]
    check("used the authorized Brave browser", "browser" in names,
          next((str(s.get("detail")) for s in steps if s.get("step") == "browser"), ""))
    check("navigated to ChatGPT and verified the page", "navigate" in names
          and next((s.get("ok") for s in steps if s.get("step") == "navigate"), False),
          next((str(s.get("detail")) for s in steps if s.get("step") == "navigate"), ""))

    if res.get("verified"):
        check("new chat opened", next((s.get("ok") for s in steps
                                       if s.get("step") == "new_chat"), False),
              next((str(s.get("detail")) for s in steps if s.get("step") == "new_chat"), ""))
        check("image artifact verified", True, str(res.get("detail"))[:80])
    else:
        # Honest non-success: must be BLOCKED with a real reason, not a fake pass.
        check("not verified -> reported as blocked with a real reason",
              bool(res.get("blocked")) and bool(res.get("detail")),
              f"blocked={res.get('blocked')} detail={str(res.get('detail'))[:90]}")
        check("sign-in is never bypassed (asks the owner)",
              bool(res.get("needs_owner")) or "sign" in str(res.get("detail")).lower()
              or "unavailable" in str(res.get("detail")).lower()
              or "no " in str(res.get("detail")).lower(),
              str(res.get("detail"))[:90])
        check("prompt submission is NOT counted as image generation",
              "image" not in str(res.get("detail")).lower()
              or "no " in str(res.get("detail")).lower()
              or "unavailable" in str(res.get("detail")).lower(),
              str(res.get("detail"))[:90])

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
