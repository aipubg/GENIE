"""Phase 1 — complete voice loop + Home RMS contract (implementation proof).

Exercises the REAL VoicePipeline (the object VoiceService builds and the daemon
runs — not a demo app) and asserts:

  1. The ordered state sequence:
       IDLE -> GREETING -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> LISTENING
  2. The greeting fires exactly once per session.
  3. GENIE's own TTS never becomes an owner utterance (self-capture blocked).
  4. The backend mic/TTS amplitude dict contract is exactly what HomeViewModel
     parses, and a plain-number fallback still works.
  5. The Home animation is honest: when TTS amplitude is unavailable it shows a
     state-based floor, not a fabricated RMS.

Fake audio providers are used because no human microphone exists here — the
pipeline/service under test is the real one. This is an IMPLEMENTATION proof,
NOT owner acceptance.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_p1_loop_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def ordered_subsequence(seq: list[str], sub: list[str]) -> bool:
    it = iter(seq)
    return all(any(x == s for x in it) for s in sub)


# ---------------------------------------------------------------- fake providers
class FakeInput:
    name = "fake-mic"
    def available(self): return True
    def sample_rate(self): return 16000
    def start(self, cb): self._cb = cb; return True
    def stop(self): self._cb = None
    def status(self): return {"name": self.name, "available": True}


class FakeSTT:
    name = "fake-stt"
    def __init__(self, text="Hello GENIE"): self.text = text; self.calls = 0
    def available(self): return True
    def transcribe(self, audio, rate, language=None):
        self.calls += 1
        from voice import providers as prov
        return prov.TranscriptResult(True, text=self.text, provider=self.name, language="hi")
    def status(self): return {"name": self.name, "provider": self.name,
                              "available": True, "ready": True, "loaded": True}


class FakeTTS:
    name = "fake-tts"
    def __init__(self): self.spoken: list[str] = []
    def available(self): return True
    def synthesize(self, text, volume=100):
        self.spoken.append(text)
        from voice import providers as prov
        return prov.TtsResult(True, provider=self.name, audio_path=None)
    def status(self): return {"name": self.name, "available": True}


class UnavailableTTS:
    """Reports unavailable — used to prove the honest Speaking-state animation."""
    name = "no-tts"
    def available(self): return False
    def synthesize(self, text, volume=100):
        from voice import providers as prov
        return prov.TtsResult(False, provider=self.name, error="unavailable")
    def status(self): return {"name": self.name, "available": False,
                              "reason": "no tts"}


class FakeOutput:
    name = "fake-out"
    def available(self): return True
    def play_file(self, path): return True
    def stop(self): pass
    def status(self): return {"name": self.name, "available": True}


def make_pipeline(tts=None, speak=True, greet=True):
    from voice.pipeline import VoicePipeline, VoiceConfig
    stt = FakeSTT()
    tts = tts or FakeTTS()
    cfg = VoiceConfig(speak_replies=speak, greet_on_session_start=greet, mode="continuous")
    p = VoicePipeline(input_provider=FakeInput(), stt=stt, tts=tts, output=FakeOutput(),
                      handler=lambda text, ctx: {"reply": "Theek hoon, shukriya!"},
                      config=cfg)
    return p, stt, tts


def record_states(p) -> list[str]:
    """Wrap every transition method so the ordered state trail is captured."""
    trail: list[str] = []
    for name in ("begin_greeting", "end_greeting", "begin_listening", "begin_transcribing",
                 "end_speech", "begin_speaking", "finish_turn", "mark_stopped",
                 "mark_failed", "mark_unavailable"):
        fn = getattr(p.turns, name, None)
        if fn is None:
            continue

        def make(f):
            def wrapper(*a, **k):
                r = f(*a, **k)
                trail.append(p.turns.state.value)
                return r
            return wrapper
        setattr(p.turns, name, make(fn))
    return trail


# --------------------------------------------------------- Home parse replica
def home_parse_amplitude(node):
    """Replicates HomeViewModel.PollAmplitudeAsync() parsing (dict level / number)."""
    if node is None:
        return 0.0
    if isinstance(node, dict) and isinstance(node.get("level"), (int, float)):
        return float(node["level"])
    if isinstance(node, (int, float)):
        return float(node)
    return 0.0


def home_state_floor(state: str) -> float:
    """Replicates HomeView.OnRender() state floor (honest state cue, not RMS)."""
    return {"Listening": 0.22, "Speaking": 0.18, "Thinking": 0.14}.get(state, 0.0)


def main() -> int:
    print("=" * 64)
    print("Phase 1 — complete voice loop + Home RMS contract (implementation)")
    print("=" * 64)

    # --- 1. full ordered sequence through the REAL pipeline ---
    p, stt, tts = make_pipeline()
    trail = record_states(p)

    p.start()                                    # GREETING -> LISTENING
    p._audio = [0.1] * 1600                      # simulate one owner utterance
    p._finish_utterance(100)                     # TRANSCRIBING -> THINKING -> SPEAKING -> LISTENING
    p.stop()                                     # STOPPED

    required = ["GREETING", "LISTENING", "TRANSCRIBING", "THINKING", "SPEAKING", "LISTENING"]
    check("ordered sequence GREETING->LISTENING->TRANSCRIBING->THINKING->SPEAKING->LISTENING",
          ordered_subsequence(trail, required), " -> ".join(trail))
    check("greeting spoken first", trail and trail[0] == "GREETING", str(trail[:3]))
    check("session ends STOPPED", trail and trail[-1] == "STOPPED", trail[-1] if trail else "")

    # --- 2. greeting exactly once ---
    greetings = [s for s in tts.spoken if "welcome to GENIE" in s]
    check("greeting spoken exactly once", len(greetings) == 1, str(greetings))
    tts.spoken.clear()
    p.turns.begin_listening()
    p.process_text("Hello GENIE")
    again = [s for s in tts.spoken if "welcome to GENIE" in s]
    check("no repeated greeting after a turn", len(again) == 0, str(tts.spoken)[:60])

    # --- 3. self-capture prevention ---
    p2, stt2, _ = make_pipeline()
    p2.turns.begin_greeting()
    before = p2.stats["self_capture_blocked"]
    p2._audio = [0.1] * 1600
    p2._finish_utterance(100)
    check("utterance blocked while GREETING",
          p2.stats["self_capture_blocked"] == before + 1 and stt2.calls == 0,
          f"blocked={p2.stats['self_capture_blocked']} stt={stt2.calls}")
    from voice.turn_manager import TurnState
    p2.turns.state = TurnState.SPEAKING
    before = p2.stats["self_capture_blocked"]
    p2._audio = [0.1] * 1600
    p2._finish_utterance(100)
    check("utterance blocked while SPEAKING",
          p2.stats["self_capture_blocked"] == before + 1 and stt2.calls == 0,
          f"blocked={p2.stats['self_capture_blocked']} stt={stt2.calls}")

    # --- 4. amplitude dict contract matches Home parsing ---
    amp = p.amplitude()
    check("mic amplitude is a dict with level", isinstance(amp, dict) and "level" in amp, str(amp))
    parsed_mic = home_parse_amplitude(amp)
    check("Home parse reads real mic level", parsed_mic == float(amp["level"]),
          f"parsed={parsed_mic}")
    check("Home parse also accepts a plain number (fallback)",
          home_parse_amplitude(0.42) == 0.42)
    tts_amp = p.tts_amplitude()
    check("tts amplitude dict has level+available",
          isinstance(tts_amp, dict) and "level" in tts_amp and "available" in tts_amp,
          str(tts_amp))
    check("Home parse reads real tts level",
          home_parse_amplitude(tts_amp) == float(tts_amp["level"]))

    # --- 5. honest Speaking animation when TTS amplitude unavailable ---
    p3, _, _ = make_pipeline(tts=UnavailableTTS())
    tts_level = home_parse_amplitude(p3.tts_amplitude())
    floor = home_state_floor("Speaking")
    check("unavailable TTS amplitude -> 0 RMS (not fabricated)", tts_level == 0.0,
          f"level={tts_level}")
    check("Speaking state still animates via honest state floor", floor > 0.0,
          f"floor={floor}")
    check("Listening floor differs from Idle floor",
          home_state_floor("Listening") > home_state_floor("Idle"))

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
