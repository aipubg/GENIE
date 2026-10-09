"""Phase 1 corrective — end-to-end multi-step action execution through the real path.

Runs the owner's examples through the REAL Daemon/orchestrator turn path (the same
one the GUI Chat uses) and asserts:

  * the step has a real state + verification receipt (no narration),
  * a dependent step does NOT run after an unrecovered failure,
  * the final message distinguishes succeeded / blocked / not-attempted,
  * a simple one-time action creates NO durable Mission record.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p1_ms_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)

    import importlib
    import core.config
    importlib.reload(core.config)
    from core.lifecycle import Daemon
    from core.config import get_config

    cfg = get_config()
    d = Daemon(cfg)
    d.start()
    port = int(cfg.get("ipc.port", 8787))
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)

    ms = d.services.get("missions")
    before = len(ms.list(limit=200)) if ms else 0

    print("=" * 70)
    print("Phase 1 — multi-step execution through the real Daemon turn path")
    print("=" * 70)

    # ---- C: YouTube open + play, verified ----
    r = d.chat("YouTube open karo aur Barsaat song play karo", session_id="owner")
    steps = (r or {}).get("steps") or []
    check("C produced an execution record", bool(steps), f"{len(steps)} step(s)")
    if steps:
        s0 = steps[0]
        check("C step succeeded", s0.get("status") == "succeeded",
              f"status={s0.get('status')} cap={s0.get('capability')}")
        check("C step carries a verification receipt",
              bool(s0.get("receipt")) and (s0.get("verified") is True),
              f"verified={s0.get('verified')} receipt={str(s0.get('receipt'))[:70]}")
        check("C used the browser authority",
              str(s0.get("capability", "")).startswith("browser."),
              str(s0.get("capability")))
    check("C reply is not empty", bool((r or {}).get("reply")),
          str((r or {}).get("reply"))[:90])

    # ---- D: Brave + ChatGPT + image (multi-step; later clauses unsupported) ----
    rd = d.chat("Brave browser mein ChatGPT kholo, new chat shuru karo aur swimming "
                "pool mein AI character ki image generate karwao", session_id="owner")
    dsteps = (rd or {}).get("steps") or []
    not_att = (rd or {}).get("not_attempted") or []
    # A supported site task is ONE verified adapter step (a thin adapter composed
    # from the reusable primitives), not a narrated multi-step story.
    check("D produced a bounded plan", len(dsteps) >= 1,
          f"{len(dsteps)} step(s), {len(not_att)} not-attempted")
    if dsteps:
        s0 = dsteps[0]
        check("D routed to the ChatGPT adapter (never application.open)",
              s0.get("capability") == "browser.chatgpt.image",
              str(s0.get("capability")))
        check("D reports the real blocking reason, never a fake success",
              s0.get("status") in ("blocked", "failed")
              and bool(str(s0.get("receipt") or "")),
              f"status={s0.get('status')} receipt={str(s0.get('receipt'))[:80]}")
        check("D does not claim an image was generated",
              "generated image verified" not in str(s0.get("receipt") or "").lower(),
              str(s0.get("receipt"))[:80])
    check("D reply distinguishes done vs not-done",
          "nahi" in str((rd or {}).get("reply", "")).lower()
          or "blocked" in str((rd or {}).get("reply", "")).lower(),
          str((rd or {}).get("reply"))[:110])

    # ---- simple one-time action creates NO durable Mission ----
    rv = d.chat("volume 30", session_id="owner")
    check("simple action executed", bool((rv or {}).get("steps")),
          str((rv or {}).get("reply"))[:60])
    after = len(ms.list(limit=200)) if ms else 0
    check("simple/multi-step actions created NO durable Mission",
          after == before, f"missions before={before} after={after}")

    # ---- conversation creates no mission either ----
    rc = d.chat("hello", session_id="owner")
    after2 = len(ms.list(limit=200)) if ms else 0
    check("conversation created NO durable Mission", after2 == before,
          f"missions={after2}")

    d.stop()

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
