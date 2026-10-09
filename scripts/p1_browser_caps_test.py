"""Phase 1 corrective — exercise the new browser capabilities for real.

Uses the existing browser/CDP authority (no second browser system):
  * browser.media.play  -> search YouTube + VERIFY playback (paused=false, ct advances)
  * browser.fullscreen  -> enter fullscreen + VERIFY document.fullscreenElement
  * requested browser   -> "brave" honoured; an uninstalled browser is BLOCKED, not substituted
  * navigate failure    -> reported truthfully
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_bcaps_")) / "data"
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

    print("=" * 68)
    print("Phase 1 — browser capabilities (real CDP)")
    print("=" * 68)

    # 1) requested browser that does not exist -> BLOCKED, never substituted
    r = b.navigate({"url": "https://example.com", "browser": "notarealbrowser"})
    check("uninstalled browser is BLOCKED (no silent substitute)",
          r.get("blocked") is True and not r.get("ok"),
          f"blocked={r.get('blocked')} detail={str(r.get('detail'))[:60]}")

    # 2) navigate to a URL that cannot resolve -> truthful failure
    r = b.navigate({"url": "https://this-domain-should-not-exist-xyz123.invalid",
                    "wait_s": 12, "content_wait_s": 4})
    check("unreachable navigation fails truthfully",
          not r.get("ok"), f"ok={r.get('ok')} err={str(r.get('error') or r.get('detail'))[:60]}")

    # 3) media search + verified playback (the demonstrated flow)
    mp = b.media_play({"service": "youtube", "query": "Barsaat"})
    check("browser.media.play verifies playback",
          bool(mp.get("verified")),
          f"verified={mp.get('verified')} detail={str(mp.get('detail'))[:70]}")
    check("media.play reports real media observations",
          mp.get("currentTime_first") is not None and mp.get("paused") is False,
          f"ct {mp.get('currentTime_first')} -> {mp.get('currentTime_second')} "
          f"paused={mp.get('paused')}")

    # 4) fullscreen against the current video context, verified
    fs = b.fullscreen({})
    check("browser.fullscreen verifies fullscreen",
          bool(fs.get("verified")), f"detail={str(fs.get('detail'))[:70]}")

    print("=" * 68)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
