"""Phase 1 — P1.2 state machine, P1.3 greeting-once, P1.4 TTS self-capture.

Uses fake audio providers (no human microphone needed) to exercise the REAL
VoicePipeline state machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_p1_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- fake providers
class FakeInput:
    name = "fake-mic"

    def __init__(self):
        self._cb = None

    def available(self):
        return True

    def sample_rate(self):
        return 16000

    def start(self, cb):
        self._cb = cb
        return True

    def stop(self):
        self._cb = None

    def status(self):
        return {"name": self.name, "available": True}


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text="namaste GENIE"):
        self.text = text
        self.calls = 0

    def available(self):
        return True

    def transcribe(self, audio, rate, language=None):
        self.calls += 1
        from voice import providers as prov
        return prov.TranscriptResult(True, text=self.text, provider=self.name,
                                     language=language or "hi")

    def status(self):
        return {"name": self.name, "provider": self.name, "available": True,
                "ready": True, "loaded": True}


class FakeTTS:
    name = "fake-tts"

    def __init__(self):
        self.spoken: list[str] = []

    def available(self):
        return True

    def synthesize(self, text, volume=100):
        self.spoken.append(text)
        from voice import providers as prov
        return prov.TtsResult(True, provider=self.name, audio_path=None)

    def status(self):
        return {"name": self.name, "available": True}


class FakeOutput:
    name = "fake-out"

    def available(self):
        return True

    def play_file(self, path):
        return True

    def stop(self):
        pass

    def status(self):
        return {"name": self.name, "available": True}


def make_pipeline(speak=True, greet=True):
    from voice.pipeline import VoicePipeline, VoiceConfig
    from voice import providers as prov
    stt = FakeSTT()
    tts = FakeTTS()
    cfg = VoiceConfig(speak_replies=speak, greet_on_session_start=greet,
                      mode="continuous")
    p = VoicePipeline(input_provider=FakeInput(), stt=stt, tts=tts,
                      output=FakeOutput(),
                      handler=lambda text, ctx: {"reply": "theek hoon"},
                      config=cfg)
    return p, stt, tts


def main() -> int:
    print("=" * 60)
    print("Phase 1 — voice session: state machine / greeting-once / self-capture")
    print("=" * 60)

    from voice.turn_manager import TurnState

    # --- P1.2/P1.3: start greets once, ends in LISTENING ---
    p, stt, tts = make_pipeline()
    check("initial state IDLE", p.turns.state == TurnState.IDLE, p.turns.state.value)
    p.start()
    check("greeting spoken on session start",
          any("welcome to GENIE" in s for s in tts.spoken), str(tts.spoken)[:70])
    check("greeting text is the owner-specified phrase",
          tts.spoken and tts.spoken[0] ==
          "Hello, welcome to GENIE. Aaj aap kya karna chahte hain?",
          tts.spoken[0] if tts.spoken else "")
    check("after greeting -> LISTENING",
          p.turns.state == TurnState.LISTENING, p.turns.state.value)
    check("greeted flag set", p._greeted is True)

    # --- P1.3: no greeting on subsequent listening cycles / turns ---
    tts.spoken.clear()
    p.turns.begin_listening()          # a listening cycle
    check("no greeting on listening cycle", len(tts.spoken) == 0, str(tts.spoken))
    p.process_text("Hello GENIE")      # an owner turn (TTS off? speak=True)
    # process_text -> _handle_turn -> speak(reply). That reply TTS is allowed.
    greeting_again = [s for s in tts.spoken if "welcome to GENIE" in s]
    check("no repeated greeting after a turn", len(greeting_again) == 0,
          str(tts.spoken)[:70])

    # --- P1.4: self-capture blocked while GREETING ---
    p2, stt2, tts2 = make_pipeline()
    p2.turns.begin_greeting()
    before = p2.stats["self_capture_blocked"]
    p2._audio = [0.1] * 1600           # pretend mic captured GENIE's own voice
    p2._finish_utterance(100)
    check("utterance blocked while GREETING",
          p2.stats["self_capture_blocked"] == before + 1,
          f"blocked={p2.stats['self_capture_blocked']}")
    check("STT not called for self-captured audio",
          stt2.calls == 0, f"stt calls={stt2.calls}")

    # --- P1.4: self-capture blocked while SPEAKING ---
    p2.turns.state = TurnState.SPEAKING
    before = p2.stats["self_capture_blocked"]
    p2._audio = [0.1] * 1600
    p2._finish_utterance(100)
    check("utterance blocked while SPEAKING",
          p2.stats["self_capture_blocked"] == before + 1,
          f"blocked={p2.stats['self_capture_blocked']}")
    check("STT still not called", stt2.calls == 0, f"stt calls={stt2.calls}")

    # --- owner utterance accepted while LISTENING ---
    p2.turns.state = TurnState.LISTENING
    p2._audio = [0.1] * 1600
    p2._finish_utterance(100)
    check("owner utterance accepted while LISTENING",
          stt2.calls == 1, f"stt calls={stt2.calls}")

    # --- P1.2: TRANSCRIBING -> THINKING transition ---
    p3, stt3, tts3 = make_pipeline()
    p3.turns.begin_listening()
    p3._audio = [0.1] * 1600
    seen = []
    orig = p3.turns.begin_transcribing

    def spy():
        t = orig()
        seen.append(p3.turns.state.value)
        return t
    p3.turns.begin_transcribing = spy
    p3._finish_utterance(100)
    check("TRANSCRIBING state entered", "TRANSCRIBING" in seen, str(seen))

    # --- error/end states exist ---
    for s in ("STOPPED", "MIC_UNAVAILABLE", "STT_UNAVAILABLE",
              "MODEL_UNAVAILABLE", "TTS_UNAVAILABLE", "FAILED"):
        check(f"state {s} defined", hasattr(TurnState, s))

    # --- stop -> new session may greet again ---
    p.stop()
    check("stop -> STOPPED", p.turns.state == TurnState.STOPPED, p.turns.state.value)
    check("stop resets greeted", p._greeted is False)
    tts.spoken.clear()
    p.start()
    check("new session greets again",
          any("welcome to GENIE" in s for s in tts.spoken), str(tts.spoken)[:60])

    print("=" * 60)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
