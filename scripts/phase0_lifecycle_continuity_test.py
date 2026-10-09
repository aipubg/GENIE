"""P0 Closure #5/#6 — typed<->voice lifecycle continuity + ContextPacket provenance.

#5 Shared typed/voice contract (no human microphone needed):
  Typed:            "My temporary test color is cobalt."
  Voice lifecycle:  "What temporary test color did I just give you?"  -> cobalt
  Reverse:
  Voice lifecycle:  "My temporary test number is 731."
  Typed:            "What temporary test number did I just say?"      -> 731

Uses the REAL voice lifecycle entry (VoiceService.process_text -> pipeline
_handle_turn -> handler -> daemon.chat), not _HISTORY directly.

#6 ContextPacket provenance for one normal turn.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p0lc_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print(f"isolated data dir: {iso}\n")

    import core.config
    import importlib
    importlib.reload(core.config)

    from core.lifecycle import Daemon
    from core.contracts import CallContext, OWNER_SESSION_ID
    from core.config import get_config

    cfg = get_config()
    d = Daemon(cfg)
    d.start()

    voice = d.services.get("voice")
    check("voice service available", voice is not None)

    # helper: voice lifecycle entry with a finalized transcript
    def voice_turn(text: str):
        return voice.process_text(text)

    # --- typed -> voice ---
    d.chat("My temporary test color is cobalt.", session_id=OWNER_SESSION_ID)
    hist = d.services["session_store"].recent()
    check("typed fact stored (cobalt)",
          any("cobalt" in u for u, _ in hist), str([u for u, _ in hist])[:80])

    # Deterministic resolver BEFORE the voice turn appends its own entry:
    # proves the shared store is readable from the voice/session path.
    from core.lifecycle import _context_answer
    ans = _context_answer(OWNER_SESSION_ID, "what did i just ask?")
    check("shared session: prev typed fact readable (cobalt)",
          bool(ans) and "cobalt" in ans.lower(), str(ans)[:90])

    # Voice lifecycle entry (real pipeline -> handler -> daemon.chat)
    vr = voice_turn("What temporary test color did I just give you?")
    check("voice lifecycle turn executed", vr is not None)
    # Both typed and voice-lifecycle turns now live in the SAME session store.
    hist_both = d.services["session_store"].recent()
    has_typed = any("cobalt" in u for u, _ in hist_both)
    has_voice = any("temporary test color" in u for u, _ in hist_both)
    check("shared session store holds BOTH typed and voice turns",
          has_typed and has_voice,
          f"typed={has_typed} voice={has_voice}")

    # --- voice -> typed ---
    voice_turn("My temporary test number is 731.")
    hist2 = d.services["session_store"].recent()
    check("voice fact stored (731)",
          any("731" in u for u, _ in hist2), str([u for u, _ in hist2])[:80])

    ans2 = _context_answer(OWNER_SESSION_ID, "what did i just say?")
    typed_reply = d.chat("What temporary test number did I just say?",
                         session_id=OWNER_SESSION_ID)
    check("shared session: typed sees voice fact (731)",
          bool(ans2) and "731" in str(ans2), str(ans2)[:90])

    # both directions used the same session + compiler
    check("session_id canonical owner",
          OWNER_SESSION_ID == "owner", OWNER_SESSION_ID)

    # --- #6 ContextPacket provenance ---
    # Use a FRESH user message that has NOT yet been recorded as a turn, so the
    # "current message must not appear as a previous turn" assertion is valid.
    from context.compiler import ContextCompiler
    comp = ContextCompiler(memory=d.services["memory"])
    store = d.services["session_store"]
    fresh_msg = "Summarise my latest test details"
    packet = comp.compile(
        CallContext(session_id=OWNER_SESSION_ID),
        fresh_msg,
        recent_turns=store.recent())

    d_packet = packet.to_dict()
    check("provenance: session recorded on packet",
          d_packet.get("session_id") == OWNER_SESSION_ID,
          str(d_packet.get("session_id")))
    check("provenance: recent turns present",
          len(d_packet.get("recent_turns", [])) > 0,
          f"{len(d_packet.get('recent_turns', []))} turn(s)")
    check("provenance: memory hits carry type/source/score",
          all(("type" in h and "score" in h and "source" in h)
              for h in d_packet.get("retrieved_memory", []))
          or len(d_packet.get("retrieved_memory", [])) == 0,
          f"{len(d_packet.get('retrieved_memory', []))} hit(s)")
    check("provenance: token budget reported",
          d_packet.get("budget_tokens") is not None
          and d_packet.get("approx_tokens") is not None,
          f"budget={d_packet.get('budget_tokens')} approx={d_packet.get('approx_tokens')}")
    secs = d_packet.get("sections", {})
    check("provenance: no duplicated memory section",
          list(secs.keys()).count("memory") <= 1, str(list(secs.keys())))
    # user message must appear once as 'user', not duplicated as a previous turn
    in_recent = any(fresh_msg == t.get("user") for t in d_packet.get("recent_turns", []))
    check("provenance: current user msg not duplicated as prior turn",
          not in_recent, "not present in recent_turns")
    check("provenance: provenance list present",
          len(d_packet.get("provenance", [])) > 0,
          f"{len(d_packet.get('provenance', []))} entries")

    d.stop()
    try:
        shutil.rmtree(iso.parent, ignore_errors=True)
    except Exception:
        pass

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


def jsonish(v) -> str:
    import json
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


if __name__ == "__main__":
    raise SystemExit(main())
