"""Probe the REAL GENIE voice service inside the PACKAGED Preview runtime.

Run with the embedded interpreter (which has faster-whisper + sounddevice):

    cd backend-dist/backend-runtime
    python/python.exe ../../scripts/p1_preview_voice_probe.py

It builds the real VoiceService (the object the daemon runs — NOT a demo app),
reports the real provider chain, verifies the amplitude/tts_amplitude contract
(the P1.7 fixes), and runs ONE real text turn through the real pipeline with
speech output disabled so nothing is played out loud.

This is an IMPLEMENTATION proof, not owner acceptance (no human speech).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME = os.path.dirname(os.path.dirname(sys.executable)) if sys.executable else None
_APP = os.path.join(_RUNTIME, "app") if _RUNTIME else None
if _APP and os.path.isdir(_APP) and _APP not in sys.path:
    sys.path.insert(0, _APP)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 64)
    print("P1 Preview runtime — real VoiceService provider chain")
    print("=" * 64)
    check("embedded interpreter", True, f"{sys.version.split()[0]} @ {sys.executable}")

    from core.config import get_config
    from voice.service import VoiceService

    cfg = get_config()
    # Do not speak out loud during the probe.
    try:
        cfg.set("voice.speak_replies", False) if hasattr(cfg, "set") else None
    except Exception:
        pass

    svc = VoiceService(cfg, db=None, vault=None,
                       handler=lambda text, ctx: {"reply": f"probe reply: {text}"})
    svc.pipeline.cfg.speak_replies = False          # belt-and-braces: no audio

    st = svc.status()
    provs = st.get("providers", {})
    for name in ("input", "stt", "tts", "output"):
        p = provs.get(name, {}) or {}
        label = p.get("name") or p.get("provider") or "?"
        avail = p.get("available")
        ready = p.get("ready")
        check(f"provider {name}: {label}", bool(label and label != "?"),
              f"available={avail} ready={ready}")

    # faster-whisper must be the real multilingual engine here
    stt = provs.get("stt", {}) or {}
    check("STT is faster-whisper (multilingual)",
          (stt.get("provider") or stt.get("name")) == "faster-whisper",
          f"{stt.get('provider') or stt.get('name')} ready={stt.get('ready')} loaded={stt.get('loaded')}")
    check("STT ready+loaded", bool(stt.get("ready")) and bool(stt.get("loaded")),
          f"ready={stt.get('ready')} loaded={stt.get('loaded')}")

    # amplitude contract (P1.7 fixes)
    pipe = st.get("pipeline", {}) or {}
    amp = pipe.get("amplitude")
    tts_amp = pipe.get("tts_amplitude")
    check("pipeline.amplitude is dict with level",
          isinstance(amp, dict) and "level" in amp, str(amp))
    check("pipeline.tts_amplitude present (fix)",
          isinstance(tts_amp, dict) and "level" in tts_amp, str(tts_amp))

    # one real turn through the real pipeline (no audio)
    out = svc.process_text("Hello GENIE")
    check("real text turn through VoiceService", bool(out.get("reply")),
          str(out.get("reply"))[:60])
    check("turn state returned to a listenable/idle state",
          svc.pipeline.turns.state.value in ("IDLE", "LISTENING"),
          svc.pipeline.turns.state.value)

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
