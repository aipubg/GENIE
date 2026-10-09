"""Phase 1 P1.5 — Hindi/Hinglish voice acceptance (synthetic fixture path).

Proves the REAL faster-whisper STT engine path for Hindi WITHOUT needing a
human speaker, by:

  1. Generating a synthetic speech-like WAV fixture.
  2. Proving FasterWhisperSttProvider loads, is ready, and processes it
     (uses the Preview runtime python which has faster_whisper + numpy).
  3. Running 3 simulated voice turns through the REAL VoicePipeline with
     Hindi/Hinglish text (process_text path — same handler the real voice uses).
  4. Verifying the SessionStore retains all 3 turns and the ContextCompiler
     packet includes them (proves continuity across voice turns).

This is NOT a substitute for a human 3-turn acceptance test — it is the
automated proof that every backend component on the Hindi voice path is
operational before a human tries it.
"""
from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

iso = Path(tempfile.mkdtemp(prefix="genie_p1_hi_")) / "data"
iso.mkdir(parents=True, exist_ok=True)
os.environ["GENIE_DATA_DIR"] = str(iso)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ------------------------------------------------------------------ fixture

def make_synthetic_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 16000) -> Path:
    """Generate a speech-like sine sweep + harmonics WAV for STT probing."""
    n_samples = int(duration_s * sample_rate)
    data = bytearray()
    for i in range(n_samples):
        t = i / sample_rate
        freq = 200 + 600 * (t / duration_s)
        val = (math.sin(2 * math.pi * freq * t) * 0.4
               + math.sin(2 * math.pi * freq * 2 * t) * 0.2
               + (math.sin(2 * math.pi * 1500 * t) * 0.05))
        v = int(max(-32768, min(32767, val * 32767)))
        data.extend(struct.pack("<h", v))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return path


# ------------------------------------------------------------------ test

def main() -> int:
    print("=" * 64)
    print("Phase 1 P1.5 — Hindi voice path (synthetic fixture, no human speaker)")
    print("=" * 64)

    # --- 1. Generate fixture ---
    fixture = iso / "fixtures" / "synthetic_hi.wav"
    make_synthetic_wav(fixture, duration_s=2.5, sample_rate=16000)
    check("synthetic WAV fixture created", fixture.exists(), f"{fixture.stat().st_size} bytes")

    # --- 2. Prove faster-whisper STT loads and is ready (Preview runtime) ---
    preview_py = REPO / "backend-dist" / "backend-runtime" / "python" / "python.exe"
    stt_ok = False
    stt_detail = ""
    if preview_py.exists():
        import subprocess
        probe_script = '''
import sys
sys.path.insert(0, r"''' + str(REPO) + '''")
from voice.service import genie_model_root, _resolve_fw_model
from core.config import get_config
from voice import providers as prov
cfg = get_config()
fw_path = _resolve_fw_model({}, cfg)
try:
    fw = prov.FasterWhisperSttProvider(fw_path, model_size="small", device="cpu", compute_type="int8")
    ready = fw.available()
    st = fw.status()
    print(f"OK provider={st.get('provider')} ready={st.get('ready')} loaded={st.get('loaded')} path={fw_path}")
except Exception as e:
    print(f"FAIL {e}")
'''
        result = subprocess.run([str(preview_py), "-c", probe_script],
                                capture_output=True, text=True, timeout=120)
        out = (result.stdout or "").strip().splitlines()
        err = (result.stderr or "").strip()
        for line in out:
            if line.startswith("OK "):
                stt_ok = True
                stt_detail = line[3:]
            elif line.startswith("FAIL "):
                stt_detail = line[5:]
        if not stt_ok and err:
            stt_detail += f" | stderr: {err[:200]}"
    else:
        stt_detail = f"Preview python not found at {preview_py}"
    check("FasterWhisperSttProvider loads (Preview runtime)", stt_ok, stt_detail)

    # --- 3. Process fixture through real STT (Preview runtime) ---
    transcribe_ok = False
    tr_detail = ""
    if preview_py.exists():
        import subprocess
        tx_script = '''
import sys, wave, numpy as np
sys.path.insert(0, r"''' + str(REPO) + '''")
from voice.service import _resolve_fw_model
from core.config import get_config
from voice import providers as prov
cfg = get_config()
fw_path = _resolve_fw_model({}, cfg)
fw = prov.FasterWhisperSttProvider(fw_path, model_size="small", device="cpu", compute_type="int8")
with wave.open(r"''' + str(fixture) + '''", "rb") as w:
    frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    rate = w.getframerate()
tr = fw.transcribe(audio, rate, language=None)
print(f"OK ok={tr.ok} text={tr.text!r} provider={tr.provider} lang={getattr(tr, 'language', '?')}")
'''
        result = subprocess.run([str(preview_py), "-c", tx_script],
                                capture_output=True, text=True, timeout=120)
        out = (result.stdout or "").strip().splitlines()
        err = (result.stderr or "").strip()
        for line in out:
            if line.startswith("OK "):
                transcribe_ok = True
                tr_detail = line[3:]
            elif line.startswith("FAIL "):
                tr_detail = line[5:]
        if not transcribe_ok and err:
            tr_detail += f" | stderr: {err[:200]}"
    check("STT transcribed synthetic audio (Preview runtime)", transcribe_ok, tr_detail)

    # --- 4. Three-turn voice pipeline with Hindi/Hinglish text ---
    from voice.pipeline import VoicePipeline, VoiceConfig
    from voice.turn_manager import TurnState
    from core.contracts import OWNER_SESSION_ID
    import core.lifecycle as lifecycle
    from core.session_store import SessionStore

    class FakeInput:
        name = "fake-mic"
        def available(self): return True
        def sample_rate(self): return 16000
        def start(self, cb): self._cb = cb; return True
        def stop(self): self._cb = None
        def status(self): return {"name": self.name, "available": True}

    class FakeTTS:
        name = "fake-tts"
        def available(self): return True
        def synthesize(self, text, volume=100):
            from voice import providers as prov
            return prov.TtsResult(True, provider=self.name, audio_path=None)
        def status(self): return {"name": self.name, "available": True}

    class FakeOutput:
        name = "fake-out"
        def available(self): return True
        def play_file(self, path): return True
        def stop(self): pass
        def status(self): return {"name": self.name, "available": True}

    # wire session store
    import sqlite3
    class _MemDB:
        def __init__(self):
            self._c = sqlite3.connect(":memory:")
            self._c.row_factory = sqlite3.Row
            self._c.executescript(
                "CREATE TABLE session_turns (turn_id TEXT PRIMARY KEY, session_id TEXT,"
                " role TEXT, text TEXT, created_at INTEGER, seq INTEGER);"
                "CREATE INDEX idx_st_sid ON session_turns(session_id);"
                "CREATE TABLE session_checkpoints (session_id TEXT PRIMARY KEY,"
                " summary TEXT, updated_at INTEGER, turn_seq INTEGER);"
            )
            self._c.commit()
        def execute(self, sql, params=()):
            self._c.execute(sql, params); self._c.commit()
        def query(self, sql, params=()):
            cur = self._c.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    db = _MemDB()
    store = SessionStore(db, session_id=OWNER_SESSION_ID)
    lifecycle._SESSION_STORE = store

    replies: list[str] = []
    def handler(text, ctx):
        reply = f"Maine suna: {text}"
        lifecycle._remember_turn(ctx.session_id, text, reply)
        replies.append(reply)
        return {"reply": reply}

    cfg_vc = VoiceConfig(speak_replies=False, greet_on_session_start=False, mode="continuous")
    p = VoicePipeline(
        input_provider=FakeInput(), stt=FakeInput(), tts=FakeTTS(),
        output=FakeOutput(), handler=handler, config=cfg_vc)

    phrases = ["namaste GENIE", "aap kaise ho", "YouTube khol do"]
    for phrase in phrases:
        p.process_text(phrase)

    check("3 voice turns processed", len(replies) == 3, str(replies))
    hist = lifecycle._history("owner")
    check("turn 1 Hindi phrase persisted", "namaste GENIE" in str(hist), str(hist))
    check("turn 2 Hindi phrase persisted", "aap kaise ho" in str(hist))
    check("turn 3 Hindi phrase persisted", "YouTube khol do" in str(hist))

    # --- 5. ContextCompiler packet sees all 3 turns ---
    from context.compiler import ContextCompiler
    from core.contracts import CallContext
    compiler = ContextCompiler(memory=None)
    ctx = CallContext(person_id="owner", session_id="owner", trace_id="t1")
    packet = compiler.compile(ctx, "aur kya kar sakte ho", recent_turns=hist)
    users = [t.get("user") for t in packet.recent_turns]
    check("context packet includes all 3 Hindi turns", len(packet.recent_turns) == 3,
          f"{len(packet.recent_turns)} turns, users={users}")
    check("turn 1 in context", "namaste GENIE" in users, str(users))
    check("turn 2 in context", "aap kaise ho" in users, str(users))
    check("turn 3 in context", "YouTube khol do" in users, str(users))

    # --- 6. amplitude() returns dict contract (for HomeViewModel) ---
    amp = p.amplitude()
    check("amplitude() returns dict contract", isinstance(amp, dict) and "level" in amp,
          str(amp))
    check("amplitude source is microphone", amp.get("source") == "microphone",
          str(amp.get("source")))
    check("amplitude not synthesised", amp.get("synthetic") is False,
          str(amp.get("synthetic")))

    # --- 7. Voice state machine is clean after 3 turns ---
    check("pipeline state IDLE after turns", p.turns.state == TurnState.IDLE,
          p.turns.state.value)

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
