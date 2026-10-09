"""Phase 1 corrective — reproduce the owner's examples through the REAL director.

Classifies the four examples with the actual director (NEDLE2 or its fallback) and
prints the decision: intent, mission_required, and each task's capability/target.
Also reports which authority each capability resolves to (app vs browser).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_repro_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

EXAMPLES = {
    "A": "Barsaat song",
    "B": "YouTube ko full screen karo",
    "C": "YouTube open karo aur Barsaat song play karo",
    "D": ("Brave browser mein ChatGPT kholo, new chat shuru karo aur swimming pool "
          "mein AI character ki image generate karwao"),
}


def main() -> int:
    from core.config import get_config
    from core.contracts import CallContext
    from director.nedle2 import get_director
    from core.orchestrator import (
        _apply_conversation_override, _apply_mission_gate, _apply_web_action_plan)

    cfg = get_config()
    director = get_director(cfg)
    print(f"director: {director.__class__.__name__}  available={director.available()}")

    # authority resolution helper
    try:
        from computer import apps
    except Exception:
        apps = None

    print("=" * 70)
    for key, text in EXAMPLES.items():
        ctx = CallContext(person_id="owner", session_id="owner", trace_id=f"repro_{key}")
        try:
            d = director.classify(text, ctx, {})
            d = _apply_conversation_override(text, d)
            d = _apply_mission_gate(text, d)
            d = _apply_web_action_plan(text, d)
        except Exception as exc:
            print(f"[{key}] classify FAILED: {exc}")
            continue
        print(f"\n[{key}] {text!r}")
        print(f"    intent={d.intent}  mission_required={d.mission_required}  "
              f"source={d.source}  conf={d.confidence}")
        print(f"    reasoning_required={d.reasoning_required}  "
              f"reply_hint={d.reply_hint[:50]!r}")
        if not d.tasks:
            print("    tasks: (none)")
        for t in d.tasks:
            cap = t.capability
            tgt = t.target or (t.params or {}).get("target") or (t.params or {}).get("url") or ""
            resolved = None
            if apps is not None and cap == "application.open" and tgt:
                e = apps.resolve(str(tgt))
                resolved = (e.to_dict().get("target") if e else "None (not installed)")
            auth = ("BROWSER" if cap.startswith("browser.") else
                    f"app-resolver -> {resolved}" if cap == "application.open" else "?")
            print(f"    task: type={t.type.value} cap={cap!r} target={tgt!r} "
                  f"params={t.params}  [{auth}]")
        print(f"    raw.reason={d.raw.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
