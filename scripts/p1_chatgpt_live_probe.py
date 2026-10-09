"""Phase 1 closure — LIVE Brave/ChatGPT -> new chat -> image verification.

Attaches to the owner's ALREADY-RUNNING, debug-enabled browser (never launches a
fresh profile) and drives the real thin ChatGPT adapter end-to-end:

    new chat -> image prompt -> wait -> verify a NEW image artifact appeared.

Success requires an actual image element that did NOT exist before the prompt.
A sign-in wall is reported as BLOCKED (never bypassed); prompt submission alone
is never counted as image generation.

This script is the live counterpart of p1_chatgpt_adapter_test.py. It uses
MODE_OWNER_EXISTING so it attaches to the browser the owner enabled with
`--remote-debugging-port=9222` rather than opening a logged-out GENIE profile.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_cgpt_live_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)


def _cdp_version() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


def main() -> int:
    from browser.mode import detect_owner_browser, MODE_OWNER_EXISTING, _default_port_owner
    from browser.service import get_browser

    print("=" * 72)
    print("Phase 1 LIVE — Brave/ChatGPT -> new chat -> image (MODE_OWNER_EXISTING)")
    print("=" * 72)

    # --- honest identity disclosure (read-only) -------------------------------
    ver = _cdp_version()
    print("[identity] CDP /json/version Browser :", ver.get("Browser", ver.get("error")))
    print("[identity] CDP User-Agent            :",
          (ver.get("User-Agent", "") or "")[:60], "...")
    det = detect_owner_browser("brave", port_owner=_default_port_owner)
    print("[identity] detect_owner_browser     :",
          json.dumps({k: det.get(k) for k in
                      ("found", "running", "attachable", "debug_port",
                       "unidentifiable", "genie_owned_conflict", "reason") if k in det},
                     ensure_ascii=False))
    if det.get("unidentifiable"):
        print("[identity] REJECTED: a debug port is reachable but GENIE cannot confirm "
              "it is your signed-in Brave. It will NOT attach to or modify that browser.")
    if det.get("genie_owned_conflict"):
        print("[identity] REJECTED: the running Brave is GENIE's own background profile, "
              "not your signed-in browser.")
    if not det.get("attachable"):
        print("[identity] No confirmed owner browser to attach -> reporting needs_owner_action.")

    # --- attach + drive the real adapter ---------------------------------------
    from core.config import get_config
    cfg = get_config()
    b = get_browser(workspace_root=cfg.data_dir / cfg.get("computer.workspace", "workspace"))

    prompt = "swimming pool mein AI character ki image"  # acceptance scenario (Hindi)
    res = b.chatgpt_image({"browser": "brave",
                           "prompt": prompt,
                           "browser_mode": MODE_OWNER_EXISTING,
                           "timeout_s": 180})

    print("\n---- step receipts ----")
    for s in (res.get("steps") or []):
        print(f"  {str(s.get('step')):12} ok={s.get('ok')}  "
              f"{str(s.get('detail'))[:90]}")

    print("\n---- result ----")
    print("verified :", res.get("verified"))
    print("blocked  :", res.get("blocked"))
    print("needs_owner:", res.get("needs_owner"))
    print("detail   :", str(res.get("detail"))[:200])

    # Evidence: if verified, show how many image sources are now present.
    if res.get("verified"):
        print("\n[EVIDENCE] a NEW image artifact was detected after the prompt.")
        return 0
    print("\n[EVIDENCE] no verified image; reported honestly (see detail).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
