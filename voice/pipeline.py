"""Voice pipeline (voice/pipeline).

    Microphone -> preprocessing -> VAD -> STT -> Turn Manager
        -> Context + NEDLE2 / agents -> Communication Brain -> TTS -> Speaker

Properties:
  * streaming wherever the provider allows it
  * barge-in is wired into the VAD path, so user speech stops GENIE immediately
  * voice never bypasses mission / trust / audit: the pipeline only *speaks* the result of a
    normal orchestrator turn, which already went through PTE, execution, verification and audit
  * honest degradation: if STT or TTS is unavailable the pipeline says so and keeps the
    text path working
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.contracts import CallContext, EventType
from core.events import get_bus
from core.logging_setup import get_logger

from . import audio as audio_pre
from . import providers as prov
from .communication import CommunicationBrain
from .metrics import VoiceMetrics
from .turn_manager import TurnManager, TurnState
from .vad import Vad, VadConfig

log = get_logger("voice.pipeline")

TurnHandler = Callable[[str, CallContext], Dict[str, Any]]

# P1.3 — spoken exactly once when a NEW voice session starts.
GREETING_TEXT = "Hello, welcome to GENIE. Aaj aap kya karna chahte hain?"


@dataclass
class VoiceConfig:
    sample_rate: int = prov.SAMPLE_RATE
    mode: str = "push_to_talk"          # push_to_talk | continuous
    silence_timeout_ms: int = 700
    min_speech_ms: int = 250
    max_utterance_ms: int = 30_000
    language: str = "hinglish"
    speak_replies: bool = True
    tts_volume: int = 100
    barge_in: bool = True
    continuous_listen_after_speech: bool = True
    stt_target_peak: float = 0.9        # AGC target before recognition
    # P1.3 — greet once on a NEW voice session (not on Home, not per turn).
    greet_on_session_start: bool = True


class VoicePipeline:
    def __init__(self, *, input_provider: prov.VoiceInputProvider,
                 stt: prov.SpeechToTextProvider,
                 tts: prov.TextToSpeechProvider,
                 output: prov.AudioOutputProvider,
                 handler: Optional[TurnHandler] = None,
                 brain: Optional[CommunicationBrain] = None,
                 metrics: Optional[VoiceMetrics] = None,
                 config: Optional[VoiceConfig] = None,
                 realtime: Optional[prov.RealtimeVoiceProvider] = None):
        self.input = input_provider
        self.stt = stt
        self.tts = tts
        self.output = output
        self.realtime = realtime
        self.handler = handler
        self.brain = brain or CommunicationBrain()
        self.metrics = metrics or VoiceMetrics()
        self.cfg = config or VoiceConfig()
        self.vad = Vad(VadConfig(end_silence_ms=self.cfg.silence_timeout_ms,
                                 min_speech_ms=self.cfg.min_speech_ms,
                                 max_segment_ms=self.cfg.max_utterance_ms),
                       sample_rate=self.cfg.sample_rate)
        self.turns = TurnManager(silence_timeout_ms=self.cfg.silence_timeout_ms,
                                 on_interrupt=self._stop_speaking)
        self._bus = get_bus()
        self._audio: List[float] = []
        self._speech_started_at = 0.0
        self._capturing = False
        self._utterance_worker: Optional[threading.Thread] = None
        self._capture_generation = 0
        self._speak_thread: Optional[threading.Thread] = None
        self._speak_lock = threading.Lock()
        self._last_result: Dict[str, Any] = {}
        self._last_tts: prov.TtsResult = prov.TtsResult(False, error="not spoken yet")
        self.last_preprocess: Dict[str, Any] = {}
        self.stats = {"utterances": 0, "transcripts": 0, "spoken": 0, "barge_ins": 0,
                      "stt_failures": 0, "tts_failures": 0}
        # --- real microphone amplitude telemetry (UI visual only) -------------
        # Measured by THIS pipeline from the SAME microphone frames the VAD uses.
        # No second capture device is opened. Never synthesised.
        self._amp_lock = threading.Lock()
        self._amp_smoothed = 0.0
        self._amp_last_emit_ms = 0.0
        self._amp_emit_interval_ms = 1000.0 / 30.0   # ~30 visual updates/sec max
        self._amp_peak_floor = 0.02                  # ignore pure silence noise floor
        # P1.3 — greeting is spoken once per voice session; reset by stop().
        self._greeted = False
        # P1.4 — counts owner utterances rejected because GENIE was speaking.
        self.stats["self_capture_blocked"] = 0
        # Real TTS output level (measured from the audio actually played).
        from .tts_level import TtsLevelMeter
        self._tts_meter = TtsLevelMeter(emit=self._emit_tts_level)
        self._tts_level_available = False

    # ------------------------------------------------------------------ capture
    def _on_chunk(self, samples: Sequence[float], rate: int) -> None:
        if not samples:                      # end-of-stream marker from a file input
            self._finish_utterance()
            return
        if not self._capturing:
            return
        self._emit_amplitude(samples)
        # Never run recognition/network/TTS on PortAudio's real-time callback.
        # Drop busy-turn audio rather than queueing stale speech or speaker echo.
        if ((self._utterance_worker and self._utterance_worker.is_alive())
                or TurnState.is_speaking(self.turns.state)):
            return
        self._audio.extend(samples)

        events = self.vad.process_chunk(samples, rate)
        for event in events:
            if event.kind == "speech_start":
                self._speech_started_at = time.time()
                if self.turns.state == TurnState.SPEAKING and self.cfg.barge_in:
                    # user spoke while GENIE was talking -> stop immediately
                    info = self.turns.detect_barge_in(True)
                    if info:
                        self.stats["barge_ins"] += 1
                        self.metrics.mark("barge_in_stop_ms", info["stop_latency_ms"])
                self.turns.begin_listening()
            elif event.kind == "speech_end":
                if event.reason == "too_short_ignored":
                    log.debug("utterance ignored (too short: %sms)", event.duration_ms)
                    self.vad.reset()
                    self._audio = []
                    continue
                self._dispatch_utterance(event.duration_ms)
        # Retain only a short pre-roll while idle, not minutes of room noise.
        limit = int(rate * (self.cfg.max_utterance_ms / 1000 + 1)) if self.vad.in_speech else int(rate * 0.5)
        if len(self._audio) > limit:
            self._audio = self._audio[-limit:]

    def _dispatch_utterance(self, duration_ms: int = 0) -> None:
        if self._utterance_worker and self._utterance_worker.is_alive():
            return
        audio, self._audio = self._audio, []
        generation = self._capture_generation

        def run():
            try:
                self._finish_utterance(duration_ms, audio=audio, generation=generation)
            except Exception as exc:
                log.exception("voice utterance failed")
                self._emit("VOICE_TRANSCRIPT_FAILED", {"error": str(exc)})
            finally:
                if generation == self._capture_generation:
                    self.vad.reset()
                    self.turns.finish_turn()
                    if self._capturing:
                        self.turns.begin_listening()

        self._utterance_worker = threading.Thread(target=run, daemon=True, name="voice-utterance")
        self._utterance_worker.start()

    def _finish_utterance(self, duration_ms: int = 0, *, audio=None, generation=None) -> None:
        if audio is None:
            audio, self._audio = self._audio, []
        if not audio:
            return
        # P1.4 — TTS self-capture prevention. While GENIE is greeting/speaking,
        # captured audio is GENIE's own speaker output and must NEVER be
        # finalized as an owner utterance.
        if TurnState.is_speaking(self.turns.state):
            self.stats["self_capture_blocked"] += 1
            log.debug("self-capture blocked: state=%s, dropped %d samples",
                      self.turns.state.value, len(audio))
            self._emit("VOICE_SELF_CAPTURE_BLOCKED",
                       {"state": self.turns.state.value, "samples": len(audio)})
            self.vad.reset()
            return
        if not self.turns.can_accept_owner_utterance():
            self.stats["self_capture_blocked"] += 1
            self.vad.reset()
            return
        self.stats["utterances"] += 1
        started = time.time()
        # P1.2 — TRANSCRIBING is a real state driven by the real STT call.
        self.turns.begin_transcribing()
        try:
            transcript = self.transcribe_audio(audio, self.cfg.sample_rate)
        except Exception as exc:
            transcript = prov.TranscriptResult(False, error=str(exc), provider=self.stt.name)
        if generation is not None and generation != self._capture_generation:
            return
        self.metrics.mark("stt_ms", transcript.latency_ms or int((time.time() - started) * 1000))
        if not transcript.ok or not transcript.text.strip():
            self.stats["stt_failures"] += 1
            log.warning("transcription failed: %s", transcript.error or "empty transcript")
            self._emit("VOICE_TRANSCRIPT_FAILED", {"error": transcript.error,
                                                   "provider": transcript.provider})
            self.turns.finish_turn()
            self.vad.reset()
            if self.cfg.mode == "continuous" and self.cfg.continuous_listen_after_speech:
                self.turns.begin_listening()
            return
        self.stats["transcripts"] += 1
        turn = self.turns.end_speech(transcript.text)
        self.metrics.current() and self.metrics.current().__setattr__(
            "provider_stt", transcript.provider)
        self._emit("VOICE_TRANSCRIPT", {"text": transcript.text,
                                        "provider": transcript.provider,
                                        "turn_id": turn.turn_id})
        self._handle_turn(transcript.text)

    # ------------------------------------------------------------------- turn
    def _handle_turn(self, text: str) -> Dict[str, Any]:
        started = time.time()
        from core.contracts import OWNER_SESSION_ID
        ctx = CallContext(person_id="owner", session_id=OWNER_SESSION_ID)
        policy_kwargs: Dict[str, Any] = {}
        plan = self.brain.plan(text, **policy_kwargs)
        result: Dict[str, Any] = {}
        if self.handler is not None:
            try:
                raw = self.handler(text, ctx)
                result = self._normalise_result(raw)
            except Exception as exc:
                log.error("voice turn handler failed: %s", exc)
                result = {"error": str(exc)}
        self.metrics.mark("action_ms", int((time.time() - started) * 1000))

        reply = result.get("reply") or result.get("error") or ""
        state = result.get("state", "")
        interrupted = self.turns.state == TurnState.INTERRUPTED

        # a second plan now that we know the outcome (acknowledgement + result-aware length)
        # A conversation turn has NO tasks, so indexing tasks[0] raised IndexError.
        _tasks = (result.get("decision") or {}).get("tasks") or []
        _intent = str(_tasks[0].get("type", "")) if _tasks else ""
        plan = self.brain.plan(text, interrupted=interrupted,
                               result_ok=(state == "COMPLETED" if state else None),
                               intent=_intent)
        spoken = self.brain.shape_for_speech(
            (plan.acknowledgement + " " + reply).strip(), plan.policy)
        self._last_result = {"text": text, "result": result, "spoken": spoken,
                             "policy": plan.policy.to_dict()}
        self.brain.record_turn(text, reply, interrupted=interrupted)

        if self.cfg.speak_replies and spoken:
            self.speak(spoken)
        else:
            self.turns.finish_turn()
        return {"transcript": text, "reply": reply, "spoken": spoken, "result": result,
                "policy": plan.policy.to_dict()}

    @staticmethod
    def _normalise_result(raw: Any) -> Dict[str, Any]:
        """Accept whatever the handler returns: dict, ActionResult or anything with to_dict."""
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if hasattr(raw, "to_dict"):
            data = raw.to_dict()
            return {"reply": data.get("detail", ""), "ok": data.get("ok"),
                    "verified": data.get("verified"), "state":
                        ("COMPLETED" if data.get("verified") else "UNVERIFIED") if data.get("ok") else "FAILED",
                    "steps": [data], "data": data}
        return {"reply": str(raw)}

    # ------------------------------------------------------------------- speak
    def speak(self, text: str, *, wait: bool = True) -> prov.TtsResult:
        """Speak text; the caller may interrupt via `barge_in()` at any time."""
        if not text.strip():
            return prov.TtsResult(False, error="empty text")

        def _run() -> None:
            started = time.time()
            self.turns.begin_speaking(text)
            profile_volume = self.cfg.tts_volume
            try:
                if hasattr(self.tts, "speak_async") and self.tts.available():
                    result = self.tts.speak_async(text, volume=profile_volume)
                    # first audio is available as soon as async synthesis is accepted
                    self.metrics.mark("tts_first_audio_ms",
                                      int((time.time() - started) * 1000))
                    if result.ok and hasattr(self.tts, "wait_until_done"):
                        # stay in SPEAKING for the real duration of the audio, and stay
                        # interruptible: stop() purges speech and returns from this wait
                        self.tts.wait_until_done(45_000)
                else:
                    result = self.tts.synthesize(text, volume=profile_volume)
                    self.metrics.mark("tts_first_audio_ms",
                                      int((time.time() - started) * 1000))
            except Exception as exc:
                result = prov.TtsResult(False, provider=self.tts.name, error=str(exc))
            self._last_tts = result
            if result.ok:
                self.stats["spoken"] += 1
                self.metrics.current() and setattr(self.metrics.current(), "provider_tts",
                                                   result.provider)
                if result.audio_path and self.output.available():
                    # Measure the REAL output level of the audio we are about to
                    # play (RMS envelope of its PCM). If the file cannot be read,
                    # the meter reports unavailable rather than faking an envelope.
                    self._start_tts_level(result.audio_path)
                    # providers that return a file are played through the output provider
                    try:
                        self.output.play_file(result.audio_path)
                    except Exception:
                        pass
                    self._tts_meter.stop()
            else:
                self.stats["tts_failures"] += 1
                log.warning("speech synthesis failed: %s", result.error)
            self.metrics.mark("tts_ms", int((time.time() - started) * 1000))
            self._emit("VOICE_SPOKEN", {"chars": len(text), "provider": result.provider,
                                        "ok": result.ok})
            # P1.4 — after GENIE finishes speaking, drop any audio the mic
            # captured from our own speaker before returning to Listening.
            self.vad.reset()
            self._audio = []
            if self.turns.state == TurnState.SPEAKING:
                turn = self.turns.finish_turn()
                self.metrics.finish_turn()
                # continuous mode hands the floor straight back to the owner
                if self._capturing:
                    self.turns.begin_listening()

        with self._speak_lock:
            self._speak_thread = threading.Thread(target=_run, name="voice-speak", daemon=True)
            self._speak_thread.start()
        if wait:
            self._speak_thread.join(timeout=120)
        # report what the provider actually did (including whether the audio is synthetic)
        return self._last_tts

    def _stop_speaking(self) -> None:
        """Called by the turn manager on barge-in: stop TTS and playback immediately."""
        try:
            if hasattr(self.tts, "stop"):
                self.tts.stop()
        except Exception as exc:
            log.debug("tts stop failed: %s", exc)
        try:
            self.output.stop()
        except Exception as exc:
            log.debug("output stop failed: %s", exc)
        # stop metering the spoken audio immediately on barge-in
        try:
            self._tts_meter.stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("tts level stop failed: %s", exc)
        # close the interrupted turn so its metrics are recorded and the state is consistent
        if self.turns.state == TurnState.INTERRUPTED:
            turn = self.turns.finish_turn()
            if turn is not None:
                self.metrics.mark("barge_in_stop_ms",
                                  self.metrics.current().stages.get("barge_in_stop_ms", 0)
                                  if self.metrics.current() else 0)
                self.metrics.finish_turn()
                self._emit("VOICE_INTERRUPTED", turn.to_dict())

    def barge_in(self) -> Optional[Dict[str, Any]]:
        """External trigger (API/test): treat as if the user started speaking."""
        return self.turns.detect_barge_in(True)

    # --------------------------------------------------------------- control
    # ------------------------------------------------------- P1.3 greeting-once
    def _greet(self) -> None:
        """Speak the session greeting exactly once, then hand the floor over.

        GREETING is a real state: GENIE's audio is on the speaker, so any audio
        captured during it is GENIE's own voice and must not become an owner
        turn (P1.4). The VAD buffer is cleared afterwards.
        """
        self._greeted = True
        if not self.tts.available():
            self.turns.mark_unavailable("tts")
            self.turns.begin_listening()
            return
        self.turns.begin_greeting()
        self._emit("VOICE_GREETING", {"text": GREETING_TEXT})
        try:
            result = self.tts.synthesize(GREETING_TEXT, volume=self.cfg.tts_volume)
            if result.ok:
                self.stats["spoken"] += 1
                if result.audio_path and self.output.available():
                    self._start_tts_level(result.audio_path)
                    try:
                        self.output.play_file(result.audio_path)
                    except Exception:
                        pass
                    self._tts_meter.stop()
            else:
                self.stats["tts_failures"] += 1
                log.warning("greeting synthesis failed: %s", result.error)
        except Exception as exc:
            log.warning("greeting speech failed: %s", exc)
            self.stats["tts_failures"] += 1
        finally:
            # P1.4 — discard anything the mic picked up from our own speaker.
            self.vad.reset()
            self._audio = []
            self.turns.end_greeting()
            self._emit("VOICE_GREETED", {"greeted": True})

    def start(self, mode: Optional[str] = None) -> Dict[str, Any]:
        mode = mode or self.cfg.mode
        if self._capturing:
            return {"ok": True, "mode": self.cfg.mode, "detail": "already listening"}
        if self._utterance_worker and self._utterance_worker.is_alive():
            return {"ok": False, "error": "Previous voice turn is still finishing; try again shortly."}
        self.cfg.mode = mode
        self._capture_generation += 1
        if not self.input.available():
            self.turns.mark_unavailable("mic")
            return {"ok": False, "error": f"no audio input available ({self.input.name})"}
        if not self.stt.available():
            self.turns.mark_unavailable("stt")
            return {"ok": False, "error": f"no speech-to-text provider ({self.stt.name})"}
        self._capturing = True
        self.vad.reset()
        self._audio = []
        started = self.input.start(self._on_chunk)
        if not started:
            self._capturing = False
            self.turns.mark_failed("capture could not start")
            return {"ok": False, "mode": mode, "input": self.input.name,
                    "stt": self.stt.name, "tts": self.tts.name,
                    "error": "capture could not start"}
        # P1.3 — greet once on a NEW session, then listen. Not on Home open,
        # not after every reply, not after every listening cycle.
        if self.cfg.greet_on_session_start and not self._greeted and self.cfg.speak_replies:
            self._greet()
        else:
            self.turns.begin_listening()
        self._emit("VOICE_LISTENING", {"mode": mode})
        return {"ok": started, "mode": mode, "input": self.input.name,
                "stt": self.stt.name, "tts": self.tts.name, "error": ""}

    def stop(self) -> Dict[str, Any]:
        self._capturing = False
        self._capture_generation += 1
        self.input.stop()
        self._stop_speaking()
        self.turns.finish_turn()
        # A future start() is a NEW session -> it may greet again.
        self._greeted = False
        self.turns.mark_stopped()
        self._emit("VOICE_STOPPED", {})
        return {"ok": True, "stats": dict(self.stats)}

    def push_to_talk(self, seconds: float = 5.0, mode: Optional[str] = None) -> Dict[str, Any]:
        """Record for a fixed window (development mode), then process the utterance."""
        started = self.start(mode or "push_to_talk")
        if not started.get("ok"):
            return started
        self.turns.push_to_talk_start()
        time.sleep(max(0.2, seconds))
        self._capturing = False
        self.input.stop()
        if self._audio:
            self._finish_utterance()
        else:
            self.turns.finish_turn()
        return {"ok": True, "captured_samples": len(self._audio), "stats": dict(self.stats),
                "last": self._last_result}

    def capture_utterance(self, timeout_s: float = 8.0) -> Dict[str, Any]:
        """Capture ONE utterance from the real input device using the VAD.

        Returns the raw audio plus VAD timestamps, so a caller can log exactly what was heard
        and when — this is the primitive behind the real microphone-to-action test.
        """
        if not self.input.available():
            return {"ok": False, "error": f"no audio input ({self.input.name})"}
        self._audio = []
        self.vad.reset()
        events: List[Any] = []
        speech_started: Optional[float] = None
        speech_ended: Optional[float] = None
        started = time.time()
        rate = self.input.sample_rate()

        def on_chunk(samples, chunk_rate):
            nonlocal speech_started, speech_ended
            if not samples:
                return
            self._audio.extend(samples)
            for event in self.vad.process_chunk(samples, chunk_rate):
                events.append(event)
                if event.kind == "speech_start" and speech_started is None:
                    speech_started = time.time()
                elif event.kind == "speech_end":
                    speech_ended = time.time()

        if not self.input.start(on_chunk):
            return {"ok": False, "error": "capture could not start"}
        try:
            while time.time() - started < timeout_s:
                if speech_ended is not None:
                    break
                time.sleep(0.05)
        finally:
            self.input.stop()

        duration_ms = int(len(self._audio) / (rate or 1) * 1000)
        return {
            "ok": bool(self._audio),
            "samples": len(self._audio),
            "rate": rate,
            "duration_ms": duration_ms,
            "capture_started": started,
            "speech_started": speech_started,
            "speech_ended": speech_ended,
            "vad_events": [e.to_dict() for e in events],
            "audio": list(self._audio),
            "input_provider": self.input.name,
        }

    def transcribe_audio(self, audio: Sequence[float], rate: int) -> prov.TranscriptResult:
        """Preprocess captured audio, then run the configured (real) recognizer.

        The preprocessing gain is reported on the result so a transcript produced from a
        heavily boosted signal is visible in the metrics rather than hidden.
        """
        started = time.time()
        pre = audio_pre.preprocess(audio, rate, target_peak=self.cfg.stt_target_peak)
        self.last_preprocess = pre.to_dict()
        result = self.stt.transcribe(pre.samples, rate, language=self.cfg.language)
        if not result.latency_ms:
            result.latency_ms = int((time.time() - started) * 1000)
        self.metrics.mark("stt_ms", result.latency_ms)
        return result

    def process_text(self, text: str) -> Dict[str, Any]:
        """Text-in path (used by tests and by the text UI to exercise the same pipeline)."""
        self.metrics.begin_turn(f"text_{int(time.time()*1000)}")
        self.turns.begin_listening()
        self.turns.end_speech(text)
        return self._handle_turn(text)

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.cfg.mode,
            "capturing": self._capturing,
            "input": self.input.status(),
            "stt": self.stt.status(),
            "tts": self.tts.status(),
            "output": self.output.status(),
            "realtime": self.realtime.status() if self.realtime else None,
            "vad": self.vad.status(),
            "turns": self.turns.status(),
            "brain": self.brain.status(),
            "metrics": self.metrics.summary(),
            "stats": dict(self.stats),
            "degraded": self.degraded_reasons(),
            "amplitude": self.amplitude(),
            # Home/Settings read BOTH meters from this block; without
            # tts_amplitude here the Speaking ring could never react to the real
            # TTS output level (it would read None -> 0.0).
            "tts_amplitude": self.tts_amplitude(),
        }

    def degraded_reasons(self) -> List[str]:
        reasons: List[str] = []
        if not self.input.available():
            reasons.append(f"no audio input ({self.input.name})")
        if not self.stt.available():
            reasons.append(self.stt.status().get("reason") or "no speech-to-text provider")
        if not self.tts.available():
            reasons.append(self.tts.status().get("reason") or "no text-to-speech provider")
        return reasons

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self._bus.publish(event_type, payload)
        except Exception:
            pass

    # ------------------------------------------------- real amplitude telemetry
    @staticmethod
    def _frame_rms(samples: Sequence[float]) -> float:
        """True RMS of a raw microphone frame. Returns 0.0 for an empty frame."""
        if not samples:
            return 0.0
        total = 0.0
        n = 0
        for s in samples:
            total += float(s) * float(s)
            n += 1
        if n == 0:
            return 0.0
        return math.sqrt(total / n)

    def _emit_amplitude(self, samples: Sequence[float]) -> None:
        """Publish REAL microphone energy for the UI blue gem.

        Contract (voice.amplitude):
          * source  = the same microphone frames this pipeline already captures
          * measure = true RMS of the frame
          * shape   = normalised 0.0 .. 1.0 then smoothed
          * rate    = throttled to ~30 updates/sec (audio may run faster)
          * NEVER synthesised from text, word count, timers or randomness

        Normalisation: RMS of full-scale speech sits well below 1.0, so the raw
        value is scaled by a fixed gain and clamped. The gain is a display
        constant only -- it never alters what STT receives.
        """
        rms = self._frame_rms(samples)

        gain = 6.0                      # display gain: maps typical speech RMS into 0..1
        level = rms * gain
        if level < self._amp_peak_floor:
            level = 0.0
        level = 1.0 if level > 1.0 else level

        now = time.time() * 1000.0
        with self._amp_lock:
            # display = previous * 0.72 + current * 0.28  (natural, non-jittery)
            self._amp_smoothed = (self._amp_smoothed * 0.72) + (level * 0.28)
            if now - self._amp_last_emit_ms < self._amp_emit_interval_ms:
                return
            self._amp_last_emit_ms = now
            payload = {
                "level": round(self._amp_smoothed, 4),
                "rms": round(rms, 6),
                "source": "microphone",
                "synthetic": False,
                "ts": int(now),
            }
        self._emit(EventType.VOICE_AMPLITUDE, payload)

    def amplitude(self) -> Dict[str, Any]:
        """Current smoothed real microphone level (0..1) for polled UI/health use."""
        with self._amp_lock:
            return {"level": round(self._amp_smoothed, 4),
                    "source": "microphone", "synthetic": False}

    # ------------------------------------------------- real TTS output level
    def _start_tts_level(self, path: str) -> None:
        """Meter the real level of the audio being played, or report honestly."""
        try:
            self._tts_meter.stop()
            started = self._tts_meter.start_file(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("tts level meter failed to start: %s", exc)
            started = False
        self._tts_level_available = bool(started)
        if not started:
            # no audio buffer to measure — say so, do not synthesise a waveform
            self._emit(EventType.VOICE_TTS_AMPLITUDE,
                       {"level": 0.0, "available": False,
                        "reason": "no measurable audio buffer for this TTS provider"})
        else:
            self._emit(EventType.VOICE_TTS_AMPLITUDE,
                       {"level": 0.0, "available": True})

    def _emit_tts_level(self, level: float) -> None:
        self._emit(EventType.VOICE_TTS_AMPLITUDE,
                   {"level": level, "available": True, "synthetic": False,
                    "ts": int(time.time() * 1000)})

    def tts_amplitude(self) -> Dict[str, Any]:
        """Current real TTS output level, and whether it could be measured."""
        return {"level": self._tts_meter.level,
                "available": bool(self._tts_level_available)}
