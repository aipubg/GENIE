"""Voice Service (voice/service) — the facade the daemon and API talk to.

Builds the provider chain from configuration with honest fallbacks, owns the profiles,
metrics and the pipeline, and exposes voice operations to the rest of GENIE.

Provider selection (first available wins, and every step is reported):

    input    sounddevice mic  ->  WAV file (development)
    stt      http provider    ->  sidecar-file provider  ->  unavailable
    tts      http provider    ->  Windows SAPI           ->  synthetic WAV  ->  unavailable
    output   sounddevice      ->  WAV file (development)
    realtime Gemini Live      ->  unavailable (needs a key)
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

from . import devices as devices_mod
from . import providers as prov
from .communication import CommunicationBrain
from .metrics import VoiceMetrics
from .live import LiveSession
from .pipeline import VoiceConfig, VoicePipeline
from .profiles import VoiceProfileRegistry
from .vad import Vad, VadConfig, synthesize_silence, synthesize_speech




def genie_model_root() -> Path:
    """Authoritative GENIE model store.

    Models must not depend on an arbitrary Hugging Face cache snapshot that may
    be cleaned at any time. Everything GENIE owns lives under
    %LOCALAPPDATA%\\GENIE\\models (or $XDG_DATA_HOME-equivalent on other OSes).
    """
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    root = base / "GENIE" / "models"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return root


def _resolve_fw_model(stt_cfg: Dict[str, Any], config) -> Path:
    """Resolve the faster-whisper model path deterministically.

    Order: explicit config -> authoritative GENIE model root -> repo data dir.
    """
    configured = str(stt_cfg.get("faster_whisper_model_path", "") or "").strip()
    if configured:
        p = Path(configured)
        if not p.is_absolute():
            p = Path(config.data_dir).parent / p
        return p
    return genie_model_root() / "faster-whisper-small"

log = get_logger("voice.service")


class VoiceService:
    def __init__(self, config, *, db=None, vault=None, handler: Optional[Callable] = None,
                 workspace_root: Optional[str | Path] = None, live_tool_handler=None,
                 context_provider=None, record_turn=None, screen_provider=None):
        self.config = config
        self.db = db
        self.vault = vault
        self.handler = handler
        self.workspace = Path(workspace_root) if workspace_root else config.data_dir
        self._mode_lock = threading.RLock()
        self.live = LiveSession(vault=vault, settings_path=self.workspace / "voice_live.json",
                                tool_handler=live_tool_handler, context_provider=context_provider,
                                record_turn=record_turn, screen_provider=screen_provider)
        self._live_selected = self.live.settings.enabled

        voice_cfg = config.get("voice", {}) or {}
        self.cfg = VoiceConfig(
            sample_rate=int(voice_cfg.get("sample_rate", prov.SAMPLE_RATE)),
            mode=str(voice_cfg.get("mode", "push_to_talk")),
            silence_timeout_ms=int(voice_cfg.get("silence_timeout_ms", 700)),
            min_speech_ms=int(voice_cfg.get("min_speech_ms", 250)),
            language=str(voice_cfg.get("language", "hinglish")),
            speak_replies=bool(voice_cfg.get("speak_replies", True)),
            tts_volume=int(voice_cfg.get("tts_volume", 100)),
            barge_in=bool(voice_cfg.get("barge_in", True)),
        )

        self.profiles = VoiceProfileRegistry(self.workspace / "voice_profiles.json")
        self.metrics = VoiceMetrics(db=db)
        self.brain = CommunicationBrain(self.profiles.active().style)

        self.input_provider: prov.VoiceInputProvider = self._build_input(voice_cfg)
        self.stt_provider: prov.SpeechToTextProvider = self._build_stt(voice_cfg)
        self.tts_provider: prov.TextToSpeechProvider = self._build_tts(voice_cfg)
        self.output_provider: prov.AudioOutputProvider = self._build_output(voice_cfg)
        self.realtime_provider: prov.RealtimeVoiceProvider = prov.GeminiLiveProvider(
            vault=vault,
            model=self.live.settings.model)

        self.pipeline = VoicePipeline(
            input_provider=self.input_provider, stt=self.stt_provider,
            tts=self.tts_provider, output=self.output_provider, handler=handler,
            brain=self.brain, metrics=self.metrics, config=self.cfg,
            realtime=self.realtime_provider)

    # ------------------------------------------------------------- providers
    def _build_input(self, cfg: Dict[str, Any]) -> prov.VoiceInputProvider:
        device = cfg.get("input_device")
        wav = cfg.get("wav_input", "")
        if cfg.get("input_provider") == "wav-file" and wav:
            return prov.WavFileInput(wav)
        candidate = prov.SoundDeviceInput(device=device, sample_rate=self.cfg.sample_rate)
        if candidate.available():
            return candidate
        if wav:
            log.warning("sounddevice unavailable — using WAV file input %s", wav)
            return prov.WavFileInput(wav)
        return candidate                        # reports unavailable honestly

    def _build_stt(self, cfg: Dict[str, Any]) -> prov.SpeechToTextProvider:
        """Provider chain: cloud HTTP -> real offline recognizer (Vosk) -> dev -> unavailable."""
        stt_cfg = cfg.get("stt", {}) or {}
        http = prov.HttpSttProvider(

            base_url=str(stt_cfg.get("base_url", "")),
            api_key_ref=str(stt_cfg.get("api_key_ref", "")),
            model=str(stt_cfg.get("model", "whisper-1")),
            vault=self.vault)
        if http.available():
            return http
        # Multilingual FIRST. The bundled Vosk model is en-us (English-only) and
        # cannot serve Hindi/Hinglish, so an available faster-whisper model wins.
        if stt_cfg.get("allow_faster_whisper", True):
            fw_path = _resolve_fw_model(stt_cfg, self.config)
            fw = prov.FasterWhisperSttProvider(
                fw_path,
                model_size=str(stt_cfg.get("faster_whisper_model", "small")),
                device=str(stt_cfg.get("faster_whisper_device", "cpu")),
                compute_type=str(stt_cfg.get("faster_whisper_compute_type", "int8")))
            if fw.available():
                log.info("speech recognition: multilingual engine "
                         "(faster-whisper) using %s", fw_path)
                return fw
            log.warning("multilingual recognizer unavailable: %s",
                        fw.status().get("error"))

        # Normal owner voice must NOT silently fall back to an English-only
        # model or to a file-based fixture: the owner speaks Hindi/Hinglish/
        # English, so presenting those as "ready" would be a lie. They remain
        # reachable only when explicitly enabled for dev/test.
        if not stt_cfg.get("allow_dev_fallback", False):
            return prov.NullSttProvider(
                "Live speech recognition unavailable: multilingual "
                "faster-whisper model not ready")

        if stt_cfg.get("allow_vosk", True):
            model_path = stt_cfg.get("vosk_model", "")
            if model_path and not Path(model_path).is_absolute():
                model_path = str(self.config.data_dir.parent / model_path)
            vosk = prov.VoskSttProvider(model_path or "")
            if vosk.available():
                log.info("speech recognition: real offline engine (vosk, "
                         "English-only model) using %s", model_path)
                return vosk
            log.warning("offline recognizer unavailable: %s", vosk.status().get("reason"))
        if stt_cfg.get("allow_file_provider", True):
            return prov.FileSttProvider()
        return prov.NullSttProvider("no speech-to-text provider configured "
                                    "(set voice.stt.base_url + api key, or install a Vosk model)")

    def _build_tts(self, cfg: Dict[str, Any]) -> prov.TextToSpeechProvider:
        # (placeholder kept for correct patch anchoring)
        tts_cfg = cfg.get("tts", {}) or {}
        http = prov.HttpTtsProvider(
            base_url=str(tts_cfg.get("base_url", "")),
            api_key_ref=str(tts_cfg.get("api_key_ref", "")),
            model=str(tts_cfg.get("model", "tts-1")),
            voice=str(tts_cfg.get("voice", "alloy")),
            vault=self.vault)
        if http.available():
            return http
        if tts_cfg.get("allow_windows_sapi", True):
            sapi = prov.WindowsSapiTts(volume=self.cfg.tts_volume)
            if sapi.available():
                return sapi
        if tts_cfg.get("allow_sapi_file", True):
            sapi_file = prov.WindowsSapiFileTts(self.workspace / "voice")
            if sapi_file.available():
                log.info("speech output: Windows SAPI rendered to WAV (real speech)")
                return sapi_file
        if tts_cfg.get("allow_synthetic", True):
            log.warning("no real speech provider — using synthetic test audio (dev only)")
            return prov.FileTtsProvider(self.workspace / "voice")
        return prov.NullTtsProvider("no text-to-speech provider configured "
                                    "(set voice.tts.base_url + api key)")

    def _build_output(self, cfg: Dict[str, Any]) -> prov.AudioOutputProvider:
        out = prov.SoundDeviceOutput(device=cfg.get("output_device"))
        if out.available():
            return out
        return prov.WavFileOutput(self.workspace / "voice")

    # ------------------------------------------------------------------ ops
    def status(self) -> Dict[str, Any]:
        if self._live_selected:
            live = self.live.status()
            return {"mode": "gemini-live", "language": self.cfg.language,
                    "pipeline": live, "live": live,
                    # STT is initialized independently of the selected realtime
                    # mode and remains part of startup diagnostics. Omitting it
                    # here made /api/status claim the provisioned local model
                    # was absent whenever Gemini Live was selected.
                    "providers": {"stt": self.stt_provider.status(), "realtime": live},
                    "amplitude": live["amplitude"], "tts_amplitude": live["tts_amplitude"]}
        return {
            "mode": self.cfg.mode,
            "live": self.live.status(),
            "language": self.cfg.language,
            "profile": self.profiles.active().to_dict(),
            "pipeline": self.pipeline.status(),
            "devices": devices_mod.summary(),
            "providers": {
                "input": self.input_provider.status(),
                "stt": self.stt_provider.status(),
                "tts": self.tts_provider.status(),
                "output": self.output_provider.status(),
                "realtime": self.realtime_provider.status(),
            },
            "metrics": self.metrics.summary(),
            "metrics_db": self.metrics.db_summary(),
            # Real microphone energy for the UI blue gem. Source = the single
            # capture this pipeline owns; never synthesised.
            "amplitude": self.pipeline.amplitude(),
            # Real TTS OUTPUT level, measured from the audio actually played.
            # available=false when there is no audio buffer to measure (e.g. a
            # platform TTS engine that speaks directly) — never synthesised.
            "tts_amplitude": self.pipeline.tts_amplitude(),
        }

    def start_listening(self, mode: Optional[str] = None) -> Dict[str, Any]:
        with self._mode_lock:
            if mode == "gemini-live" or (mode is None and self.live.settings.enabled):
                self.pipeline.stop()
                self._live_selected = True
                return self.live.start()
            stopped = self.live.stop()
            if not stopped["ok"]:
                return stopped
            self._live_selected = False
            return self.pipeline.start(mode)

    def stop_listening(self) -> Dict[str, Any]:
        with self._mode_lock:
            live = self.live.stop()
            local = self.pipeline.stop()
            return live if self._live_selected else local

    def configure_live(self, changes):
        with self._mode_lock:
            result = self.live.configure(changes)
            if result.get("ok"):
                self._live_selected = self.live.settings.enabled
            return result

    def levels(self):
        if self._live_selected:
            state = self.live.status()
            return {"amplitude": state["amplitude"], "tts_amplitude": state["tts_amplitude"]}
        return {"amplitude": self.pipeline.amplitude(), "tts_amplitude": self.pipeline.tts_amplitude()}

    def push_to_talk(self, seconds: float = 5.0) -> Dict[str, Any]:
        if self.live.active:
            return {"ok": False, "error": "Stop Live voice before starting local push-to-talk."}
        return self.pipeline.push_to_talk(seconds)

    def process_text(self, text: str) -> Dict[str, Any]:
        """Same pipeline, text entry point (used by tests and the text UI)."""
        self.metrics.begin_turn(f"text_{int(time.time() * 1000)}")
        return self.pipeline.process_text(text)

    def say(self, text: str) -> Dict[str, Any]:
        if self.live.active:
            return {"ok": False, "error": "Live voice owns the speaker; use the active conversation."}
        result = self.pipeline.speak(text)
        return result.to_dict()

    def barge_in(self) -> Dict[str, Any]:
        if self._live_selected:
            return self.live.interrupt()
        info = self.pipeline.barge_in() or {}
        return {"ok": True, **info}

    def stop_speaking(self) -> Dict[str, Any]:
        if self._live_selected:
            return self.live.interrupt()
        self.pipeline._stop_speaking()
        return {"ok": True}

    # ---------------------------------------------------- real microphone E2E
    def real_e2e(self, seconds: float = 8.0, expected: str = "") -> Dict[str, Any]:
        """**Real** microphone-to-action test — no transcript injection anywhere.

        Captures live audio from the physical microphone, runs the real recognizer, routes
        through NEDLE2, executes a real Windows action, verifies it, and speaks the
        confirmation. Every stage is logged with its timing.
        """
        if self.live.active:
            return {"ok": False, "error": "Stop Live voice before the local microphone diagnostic."}
        report: Dict[str, Any] = {"stages": {}, "ok": False}
        total_started = time.time()
        self.metrics.begin_turn(f"e2e_{int(time.time()*1000)}")

        # 1. capture (real device)
        capture = self.pipeline.capture_utterance(seconds)
        report["stages"]["capture"] = {
            "ok": bool(capture.get("ok")),
            "device": (capture.get("input_provider") or ""),
            "duration_ms": capture.get("duration_ms"),
            "samples": capture.get("samples"),
            "vad_events": capture.get("vad_events"),
            "speech_started_at": capture.get("speech_started"),
            "speech_ended_at": capture.get("speech_ended"),
            "error": capture.get("error", ""),
        }
        if not capture.get("ok"):
            report["error"] = capture.get("error", "no audio captured")
            return report
        audio = capture.get("audio") or []
        peak = max((abs(float(s)) for s in audio), default=0.0)
        report["stages"]["capture"]["peak_level"] = round(peak, 5)
        report["stages"]["capture"]["level_hint"] = (
            "healthy" if peak > 0.05 else
            "low — move closer to the microphone or raise the input level"
            if peak > 0.005 else "very low — check that the correct microphone is selected")
        self.metrics.mark("vad_ms", capture.get("duration_ms", 0))

        # 2. real speech recognition
        transcript = self.pipeline.transcribe_audio(audio, capture["rate"])
        report["stages"]["stt"] = {
            "ok": transcript.ok, "provider": transcript.provider,
            "transcript": transcript.text, "confidence": transcript.confidence,
            "latency_ms": transcript.latency_ms, "error": transcript.error,
            "preprocessing": self.pipeline.last_preprocess,
        }
        if not transcript.ok or not transcript.text.strip():
            report["error"] = transcript.error or "recognizer returned nothing"
            return report

        # 3. the normal GENIE turn: director -> mission -> PTE -> execute -> verify
        turn = self.process_text(transcript.text)
        result = turn.get("result", {})
        steps = result.get("steps") or [{}]
        decision = result.get("decision") or {}
        report["stages"]["turn"] = {
            "ok": bool(result) and not result.get("error"),
            "transcript": transcript.text,
            "normalised_input": decision.get("model_input"),
            "director": decision.get("source"),
            "director_confidence": decision.get("confidence"),
            "capability": steps[0].get("capability"),
            "mission_id": result.get("mission_id"),
            "mission_state": result.get("state"),
            "executed": steps[0].get("ok"),
            "verified": steps[0].get("verified"),
            "verification_detail": steps[0].get("detail"),
            "strategy": steps[0].get("strategy_used"),
            "reply": turn.get("reply"),
        }

        # 4. spoken confirmation (real TTS)
        tts_status = self.tts_provider.status()
        report["stages"]["speech_output"] = {
            "ok": bool(self.pipeline._last_tts.ok),
            "provider": tts_status.get("name"),
            "synthetic": getattr(self.pipeline._last_tts, "synthetic", False),
            "spoken_text": turn.get("spoken"),
        }

        # 5. expectations
        if expected:
            matched = expected.lower() in (transcript.text or "").lower()
            report["stages"]["expectation"] = {"expected": expected, "matched": matched}
            report["ok"] = matched and bool(report["stages"]["turn"]["verified"])
        else:
            report["ok"] = bool(report["stages"]["turn"]["verified"])

        report["total_ms"] = int((time.time() - total_started) * 1000)
        report["metrics"] = self.metrics.summary()
        return report

    def acoustic_loop(self, phrase: str = "volume 30", seconds: float = 6.0,
                      play_volume: int = 70) -> Dict[str, Any]:
        """Fully automated acoustic round trip — still no injection.

        GENIE *speaks the phrase out loud* through the speakers, the physical microphone hears
        it, the real recognizer transcribes it, and the resulting turn is executed and
        verified. Useful for automated verification when nobody is at the keyboard.
        """
        report: Dict[str, Any] = {"stages": {}, "ok": False}
        from . import providers as prov_mod

        # speak the phrase to a file with the real synthesiser, then play it out loud
        tts_file = prov_mod.WindowsSapiFileTts(self.workspace / "voice")
        if not tts_file.available():
            return {"ok": False, "error": "no real speech synthesiser available"}
        speech = tts_file.synthesize(phrase, volume=100)
        report["stages"]["synthesis"] = {"ok": speech.ok, "provider": speech.provider,
                                         "path": speech.audio_path,
                                         "duration_ms": speech.duration_ms}
        if not speech.ok:
            return report

        # start listening first, then play, so nothing is missed
        import threading
        captured: Dict[str, Any] = {}

        def _listen():
            captured.update(self.pipeline.capture_utterance(seconds))

        listener = threading.Thread(target=_listen, name="acoustic-listen", daemon=True)
        listener.start()
        time.sleep(0.6)                       # let the stream open before playing
        played = self.output_provider.play_file(speech.audio_path)
        report["stages"]["playback"] = {"ok": played, "provider": self.output_provider.name}
        listener.join(timeout=seconds + 4)

        if not captured.get("ok"):
            report["error"] = captured.get("error", "nothing captured from the microphone")
            return report
        report["stages"]["capture"] = {
            "ok": True, "device": captured.get("input_provider"),
            "duration_ms": captured.get("duration_ms"),
            "vad_events": captured.get("vad_events"),
        }
        transcript = self.pipeline.transcribe_audio(captured["audio"], captured["rate"])
        report["stages"]["stt"] = {"ok": transcript.ok, "provider": transcript.provider,
                                   "transcript": transcript.text,
                                   "confidence": transcript.confidence,
                                   "latency_ms": transcript.latency_ms}
        if not transcript.ok or not transcript.text.strip():
            report["error"] = "recognizer returned nothing (acoustic loop)"
            return report

        turn = self.process_text(transcript.text)
        result = turn.get("result", {})
        steps = result.get("steps") or [{}]
        report["stages"]["turn"] = {
            "capability": steps[0].get("capability"),
            "mission_state": result.get("state"),
            "verified": steps[0].get("verified"),
            "detail": steps[0].get("detail"),
            "reply": turn.get("reply"),
        }
        report["ok"] = bool(steps[0].get("verified"))
        report["spoken_phrase"] = phrase
        report["heard"] = transcript.text
        return report

    # ------------------------------------------------------------- self-test
    def selftest(self, command: str = "volume 30") -> Dict[str, Any]:
        """End-to-end verification of the voice stack on this machine.

        Steps: devices -> VAD on synthetic speech -> transcript -> real GENIE turn (with
        verification) -> speech output -> barge-in. Every step reports honestly.
        """
        report: Dict[str, Any] = {"steps": {}, "ok": True}

        # 1. devices
        devs = devices_mod.summary()
        report["steps"]["devices"] = {
            "ok": devs["microphone_present"],
            "microphone": (devs["default_input"] or {}).get("name"),
            "speaker": (devs["default_output"] or {}).get("name"),
        }

        # 2. VAD on synthetic speech
        vad = Vad(VadConfig())
        events = vad.process_chunk(synthesize_silence(200))
        events += vad.process_chunk(synthesize_speech(600))
        events += vad.process_chunk(synthesize_silence(900))
        kinds = [e.kind for e in events]
        report["steps"]["vad"] = {"ok": "speech_start" in kinds and "speech_end" in kinds,
                                  "events": kinds, "threshold": round(vad.threshold, 5)}

        # 3. real turn through the handler (PTE + execution + verification + audit)
        turn = self.process_text(command)
        result = turn.get("result", {})
        report["steps"]["turn"] = {
            "ok": bool(result) and not result.get("error"),
            "command": command, "reply": turn.get("reply"),
            "state": result.get("state"),
            "verified": bool((result.get("steps") or [{}])[0].get("verified")),
            "spoken_text": turn.get("spoken"),
        }

        # 4. speech output
        tts = self.tts_provider.status()
        said = self.say(turn.get("spoken") or turn.get("reply") or "GENIE ready")
        report["steps"]["speech_output"] = {"ok": bool(said.get("ok")),
                                            "provider": said.get("provider"),
                                            "synthetic": said.get("synthetic", False),
                                            "latency_ms": said.get("latency_ms")}

        # 5. barge-in: start speaking, then interrupt while the audio is still playing
        self.metrics.begin_turn(f"barge_{int(time.time()*1000)}")
        self.pipeline.speak("This sentence is intentionally long so that it can be "
                            "interrupted in the middle of speaking by the user.", wait=False)
        time.sleep(0.5)
        was_speaking = self.pipeline.turns.state.value == "SPEAKING"
        interrupt = self.barge_in()
        report["steps"]["barge_in"] = {
            "ok": was_speaking and interrupt.get("stop_latency_ms") is not None,
            "was_speaking": was_speaking,
            "stop_latency_ms": interrupt.get("stop_latency_ms"),
            "interruptions": self.pipeline.turns.stats["interruptions"],
        }

        report["ok"] = all(step.get("ok") for step in report["steps"].values())
        report["metrics"] = self.metrics.summary()
        report["degraded"] = self.pipeline.degraded_reasons()
        return report
