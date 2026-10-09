"""Turn manager (voice/turn_manager).

Owns who is speaking and what happens when both want to. States:

    IDLE -> LISTENING -> THINKING -> SPEAKING -> (INTERRUPTED) -> LISTENING

Barge-in is a first-class transition here, not a later feature: user speech while GENIE is
speaking immediately stops playback, cancels the remaining speech and hands the floor back.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.contracts import EventType
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("voice.turns")


class TurnState(str, Enum):
    # --- normal owner voice session -------------------------------------
    IDLE = "IDLE"
    GREETING = "GREETING"            # GENIE speaks the session greeting (once)
    LISTENING = "LISTENING"          # capturing owner speech
    TRANSCRIBING = "TRANSCRIBING"    # STT running on the captured utterance
    THINKING = "THINKING"            # model / orchestrator producing the reply
    SPEAKING = "SPEAKING"            # TTS playing GENIE's reply
    INTERRUPTED = "INTERRUPTED"      # owner barged in

    # --- terminal / degraded states -------------------------------------
    STOPPED = "STOPPED"              # session stopped by the owner
    MIC_UNAVAILABLE = "MIC_UNAVAILABLE"
    STT_UNAVAILABLE = "STT_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TTS_UNAVAILABLE = "TTS_UNAVAILABLE"
    FAILED = "FAILED"

    @classmethod
    def is_terminal(cls, state: "TurnState") -> bool:
        return state in (cls.STOPPED, cls.MIC_UNAVAILABLE, cls.STT_UNAVAILABLE,
                         cls.MODEL_UNAVAILABLE, cls.TTS_UNAVAILABLE, cls.FAILED)

    @classmethod
    def is_speaking(cls, state: "TurnState") -> bool:
        """GENIE's own audio is on the speaker during these states."""
        return state in (cls.GREETING, cls.SPEAKING)


@dataclass
class Turn:
    turn_id: str
    started_at: float = field(default_factory=time.time)
    transcript: str = ""
    reply: str = ""
    interrupted: bool = False
    interrupt_at_ms: int = 0
    stop_latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"turn_id": self.turn_id, "transcript": self.transcript,
                "reply": self.reply, "interrupted": self.interrupted,
                "interrupt_at_ms": self.interrupt_at_ms,
                "stop_latency_ms": self.stop_latency_ms,
                "ts": int(self.started_at)}


class TurnManager:
    def __init__(self, silence_timeout_ms: int = 700, on_interrupt: Optional[Callable[[], None]] = None):
        self.state = TurnState.IDLE
        self.silence_timeout_ms = silence_timeout_ms
        self.on_interrupt = on_interrupt
        self._lock = threading.RLock()
        self._turn: Optional[Turn] = None
        self._seq = 0
        self._speaking_since = 0.0
        self._listen_since = 0.0
        self.history: List[Turn] = []
        self.stats = {"turns": 0, "interruptions": 0, "barge_in_stops": 0}
        self._bus = get_bus()

    # ------------------------------------------------------------------ states
    def begin_listening(self) -> Turn:
        with self._lock:
            if self._turn is None:
                self._seq += 1
                self._turn = Turn(turn_id=f"turn_{int(time.time()*1000)}_{self._seq}")
            self.state = TurnState.LISTENING
            self._listen_since = time.time()
            self._emit(EventType.SPEECH_STARTED, {"turn_id": self._turn.turn_id})
            return self._turn

    # ------------------------------------------------------- P1.2/P1.3 states
    def begin_greeting(self) -> Turn:
        """GENIE starts speaking the session greeting (once per session)."""
        with self._lock:
            if self._turn is None:
                self._seq += 1
                self._turn = Turn(turn_id=f"turn_{int(time.time()*1000)}_{self._seq}")
            self.state = TurnState.GREETING
            self._speaking_since = time.time()
            self._emit("VOICE_STATE", {"state": self.state.value,
                                       "turn_id": self._turn.turn_id})
            return self._turn

    def begin_transcribing(self) -> Turn:
        """STT is running on a captured utterance."""
        with self._lock:
            turn = self._turn or self.begin_listening()
            self.state = TurnState.TRANSCRIBING
            self._emit("VOICE_STATE", {"state": self.state.value,
                                       "turn_id": turn.turn_id})
            return turn

    def end_greeting(self) -> Turn:
        """Greeting finished -> hand the floor to the owner."""
        with self._lock:
            turn = self._turn
            self.state = TurnState.LISTENING
            self._listen_since = time.time()
            self._emit("VOICE_STATE", {"state": self.state.value})
            return turn

    def mark_stopped(self) -> None:
        with self._lock:
            self._turn = None
            self.state = TurnState.STOPPED
            self._emit("VOICE_STATE", {"state": self.state.value})

    def mark_unavailable(self, kind: str) -> None:
        """kind: mic | stt | model | tts"""
        with self._lock:
            mapping = {
                "mic": TurnState.MIC_UNAVAILABLE,
                "stt": TurnState.STT_UNAVAILABLE,
                "model": TurnState.MODEL_UNAVAILABLE,
                "tts": TurnState.TTS_UNAVAILABLE,
            }
            self.state = mapping.get(kind, TurnState.FAILED)
            self._emit("VOICE_STATE", {"state": self.state.value, "reason": kind})

    def mark_failed(self, reason: str = "") -> None:
        with self._lock:
            self.state = TurnState.FAILED
            self._emit("VOICE_STATE", {"state": self.state.value, "reason": reason})

    def can_accept_owner_utterance(self) -> bool:
        """P1.4 — GENIE must never transcribe its own speaker output.

        Owner utterances are only accepted while GENIE is not producing audio.
        """
        with self._lock:
            return self.state in (TurnState.LISTENING, TurnState.IDLE)

    def end_speech(self, transcript: str) -> Turn:
        with self._lock:
            turn = self._turn or self.begin_listening()
            turn.transcript = transcript
            self.state = TurnState.THINKING
            self._emit(EventType.SPEECH_ENDED, {"turn_id": turn.turn_id,
                                                "transcript": transcript[:200]})
            return turn

    def begin_speaking(self, reply: str) -> Turn:
        with self._lock:
            if self._turn is None:
                self._turn = Turn(turn_id=f"turn_{int(time.time()*1000)}")
            turn = self._turn
            turn.reply = reply
            self.state = TurnState.SPEAKING
            self._speaking_since = time.time()
            return turn

    def finish_turn(self) -> Optional[Turn]:
        with self._lock:
            if self._turn is None:
                self.state = TurnState.IDLE
                return None
            turn = self._turn
            self.history.append(turn)
            self.history = self.history[-50:]
            self.stats["turns"] += 1
            self._turn = None
            self.state = TurnState.IDLE
            return turn

    # --------------------------------------------------------------- barge-in
    def detect_barge_in(self, speech_detected: bool) -> Optional[Dict[str, Any]]:
        """Called by the VAD path. Returns details when an interruption happened."""
        if not speech_detected:
            return None
        with self._lock:
            if self.state != TurnState.SPEAKING:
                return None
            started = time.time()
            if self._turn is not None:
                self._turn.interrupted = True
                self._turn.interrupt_at_ms = int((time.time() - self._turn.started_at) * 1000)
            self.state = TurnState.INTERRUPTED
            self.stats["interruptions"] += 1
        # cancel speech outside the lock (providers may block briefly)
        stop_latency = 0
        try:
            if self.on_interrupt:
                self.on_interrupt()
        finally:
            stop_latency = int((time.time() - started) * 1000)
        with self._lock:
            if self._turn is not None:
                self._turn.stop_latency_ms = stop_latency
            self.stats["barge_in_stops"] += 1
        payload = {"turn_id": self._turn.turn_id if self._turn else "",
                   "stop_latency_ms": stop_latency}
        self._emit("BARGE_IN", payload)
        log.info("barge-in: speech stopped in %sms", stop_latency)
        return payload

    # -------------------------------------------------------------- push-to-talk
    def push_to_talk_start(self) -> Turn:
        return self.begin_listening()

    def push_to_talk_stop(self, transcript: str) -> Turn:
        return self.end_speech(transcript)

    # ------------------------------------------------------------------- info
    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state.value,
                "turn_id": self._turn.turn_id if self._turn else None,
                "speaking_for_ms": int((time.time() - self._speaking_since) * 1000)
                if self.state == TurnState.SPEAKING else 0,
                "listening_for_ms": int((time.time() - self._listen_since) * 1000)
                if self.state == TurnState.LISTENING else 0,
                "stats": dict(self.stats),
                "history": [t.to_dict() for t in self.history[-5:]],
            }

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self._bus.publish(event_type, payload)
        except Exception:
            pass
