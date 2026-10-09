"""Voice provider interfaces + implementations (voice/providers).

GENIE is never coupled to one speech vendor. Five contracts:

    VoiceInputProvider      microphone / audio source
    SpeechToTextProvider    audio -> text
    RealtimeVoiceProvider   bidirectional streaming speech (e.g. Gemini Live)
    TextToSpeechProvider    text -> audio
    AudioOutputProvider     audio -> speakers (cancellable)

Every provider reports honest availability. When a provider needs a credential that does not
exist, it says so and the pipeline degrades to the next provider — it never pretends to work.

Windows SAPI is used as a real, dependency-free TTS/STT-grade fallback (it can genuinely
speak and genuinely purge speech mid-sentence), but it is a *provider*, not the architecture
(D-045): replacing it with a cloud voice provider requires no changes outside this file.
"""
from __future__ import annotations

import ctypes
import io
import json
import os
import queue
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.logging_setup import get_logger

log = get_logger("voice.providers")

IS_WINDOWS = sys.platform.startswith("win")
SAMPLE_RATE = 16_000


# --------------------------------------------------------------------- results
@dataclass
class TranscriptResult:
    ok: bool
    text: str = ""
    confidence: float = 0.0
    provider: str = ""
    latency_ms: int = 0
    language: str = ""
    error: str = ""
    partial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "text": self.text, "confidence": self.confidence,
                "provider": self.provider, "latency_ms": self.latency_ms,
                "language": self.language, "error": self.error, "partial": self.partial}


@dataclass
class TtsResult:
    ok: bool
    provider: str = ""
    audio_path: str = ""
    bytes_out: int = 0
    duration_ms: int = 0
    latency_ms: int = 0
    spoken: bool = False
    error: str = ""
    synthetic: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "provider": self.provider, "audio_path": self.audio_path,
                "bytes": self.bytes_out, "duration_ms": self.duration_ms,
                "latency_ms": self.latency_ms, "spoken": self.spoken,
                "synthetic": self.synthetic, "error": self.error}


# ------------------------------------------------------------------ interfaces
class VoiceProvider:
    name = "base"

    def available(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available()}


class VoiceInputProvider(VoiceProvider):
    """Microphone or audio source producing float32 mono chunks."""

    def start(self, on_chunk: Callable[[Sequence[float], int], None]) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def sample_rate(self) -> int:
        return SAMPLE_RATE


class SpeechToTextProvider(VoiceProvider):
    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        raise NotImplementedError


class RealtimeVoiceProvider(VoiceProvider):
    """Bidirectional streaming speech (audio in, audio out, interruptions)."""

    def connect(self) -> bool:
        raise NotImplementedError

    def send_audio(self, chunk: Sequence[float]) -> None:
        raise NotImplementedError

    def on_audio(self, callback: Callable[[bytes], None]) -> None:
        raise NotImplementedError

    def interrupt(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class TextToSpeechProvider(VoiceProvider):
    def synthesize(self, text: str, *, voice: str = "", rate: int = 0,
                   volume: int = 100, out_path: Optional[str] = None) -> TtsResult:
        raise NotImplementedError

    def speak_async(self, text: str, *, voice: str = "", rate: int = 0,
                    volume: int = 100) -> TtsResult:
        """Start speaking without blocking (default: blocking synthesize in a thread)."""
        return self.synthesize(text, voice=voice, rate=rate, volume=volume)

    def stop(self) -> bool:
        """Interrupt speech already in progress. Returns True if something was stopped."""
        return False

    def wait_until_done(self, timeout_ms: int = 60_000) -> bool:
        """Block until the current utterance finishes (or is interrupted).

        Async providers return as soon as synthesis *starts*; the turn manager still needs to
        know when the audio actually ends, otherwise GENIE looks idle while it is still talking
        (and barge-in would have nothing to interrupt).
        """
        return True


class AudioOutputProvider(VoiceProvider):
    def play(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE) -> bool:
        raise NotImplementedError

    def play_file(self, path: str) -> bool:
        raise NotImplementedError

    def stop(self) -> bool:
        raise NotImplementedError


# ============================================================== microphone input
class SoundDeviceInput(VoiceInputProvider):
    """Real microphone capture through the optional `sounddevice` provider."""

    name = "sounddevice"

    def __init__(self, device: Optional[int] = None, sample_rate: int = SAMPLE_RATE,
                 block_ms: int = 20):
        self.device = device
        self.rate = sample_rate
        self.block_ms = block_ms
        self._stream = None
        self._on_chunk: Optional[Callable] = None
        self._stream_lock = threading.RLock()
        self._running = False
        self._generation = 0
        self.stats = {"chunks": 0, "samples": 0, "started_at": 0.0, "errors": 0}

    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            import numpy  # noqa: F401
            return True
        except Exception:
            return False

    def start(self, on_chunk: Callable[[Sequence[float], int], None]) -> bool:
        with self._stream_lock:
            if self._stream is not None and self._running:
                return True
            if not self.available():
                return False
            import sounddevice as sd
            self._on_chunk = on_chunk
            self._running = False
            self._generation += 1
            generation = self._generation
            block = max(1, int(self.rate * self.block_ms / 1000))

            def _callback(indata, frames, time_info, status):
                # PortAudio can deliver one final callback while stop/close is
                # unwinding.  Never call into the pipeline after ownership has
                # been released; this also prevents a second start from
                # feeding stale frames into the new session.
                # Never take the lifecycle lock in a PortAudio callback:
                # stream.stop() waits for callbacks while holding that lock.
                if not self._running or generation != self._generation:
                    return
                if status:
                    self.stats["errors"] += 1
                samples = (indata[:, 0].tolist()
                           if hasattr(indata, "tolist") else list(indata))
                self.stats["chunks"] += 1
                self.stats["samples"] += len(samples)
                if self._running and generation == self._generation:
                    on_chunk(samples, self.rate)

            stream = None
            try:
                stream = sd.InputStream(device=self.device, channels=1,
                                        samplerate=self.rate, blocksize=block,
                                        dtype="float32", callback=_callback)
                stream.start()
                self._stream = stream
                self._running = True
                self.stats["started_at"] = time.time()
                log.info("microphone capture started (device=%s rate=%s block=%s)",
                         self.device, self.rate, block)
                return True
            except Exception as exc:
                log.error("microphone start failed: %s", exc)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                self._stream = None
                self._running = False
                self._on_chunk = None
                return False

    def stop(self) -> None:
        with self._stream_lock:
            stream = self._stream
            self._running = False
            self._generation += 1
            self._on_chunk = None
            self._stream = None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    def sample_rate(self) -> int:
        return self.rate

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(), "device": self.device,
                "rate": self.rate, "running": self._stream is not None and self._running,
                "stats": dict(self.stats)}


class WavFileInput(VoiceInputProvider):
    """Feeds a WAV file as if it were a live microphone (development and tests)."""

    name = "wav-file"

    def __init__(self, path: str | Path, sample_rate: int = SAMPLE_RATE,
                 realtime: bool = False):
        self.path = Path(path)
        self.rate = sample_rate
        self.realtime = realtime
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def available(self) -> bool:
        return self.path.exists()

    def start(self, on_chunk: Callable[[Sequence[float], int], None]) -> bool:
        if not self.available():
            return False
        samples, rate = read_wav(self.path)
        self.rate = rate or self.rate
        block = max(1, int(self.rate * 0.02))

        def _run():
            for start in range(0, len(samples), block):
                if self._stop.is_set():
                    break
                on_chunk(samples[start:start + block], self.rate)
                if self.realtime:
                    time.sleep(0.02)
            on_chunk([], self.rate)          # end marker

        self._stop.clear()
        self._thread = threading.Thread(target=_run, name="wav-input", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(), "path": str(self.path)}


# ================================================================== speech to text
class HttpSttProvider(SpeechToTextProvider):
    """OpenAI-compatible /audio/transcriptions endpoint (needs a provider key)."""

    name = "http-stt"

    def __init__(self, base_url: str = "", api_key_ref: str = "", model: str = "whisper-1",
                 vault=None, timeout_s: float = 60.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key_ref = api_key_ref
        self.model = model
        self.vault = vault
        self.timeout_s = timeout_s

    def _key(self) -> str:
        if not self.vault or not self.api_key_ref:
            return ""
        return self.vault.resolve(self.api_key_ref) or ""

    def available(self) -> bool:
        return bool(self.base_url and self._key())

    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        started = time.time()
        if not self.available():
            return TranscriptResult(False, provider=self.name,
                                    error="no transcription endpoint/key configured")
        try:
            wav_bytes = samples_to_wav_bytes(audio, sample_rate)
            boundary = "----genie" + os.urandom(8).hex()
            parts: List[bytes] = []
            fields = {"model": self.model, "response_format": "json"}
            if language:
                fields["language"] = language
            for key, value in fields.items():
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\";"
                f" filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
            parts.append(wav_bytes)
            parts.append(f"\r\n--{boundary}--\r\n".encode())
            body = b"".join(parts)

            req = urllib.request.Request(f"{self.base_url}/audio/transcriptions",
                                         data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Authorization", f"Bearer {self._key()}")
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = (payload.get("text") or "").strip()
            return TranscriptResult(bool(text), text=text, confidence=0.9,
                                    provider=self.name,
                                    latency_ms=int((time.time() - started) * 1000),
                                    language=language)
        except Exception as exc:
            return TranscriptResult(False, provider=self.name, error=str(exc),
                                    latency_ms=int((time.time() - started) * 1000))

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "base_url": self.base_url or None,
                "reason": "" if self.available() else "no transcription endpoint/key configured"}


class VoskSttProvider(SpeechToTextProvider):
    """Real offline speech recognition (Vosk/Kaldi).

    This is a genuine STT engine running locally — no cloud key, no transcript injection.
    The model is loaded lazily and cached per process; if the model directory is missing the
    provider reports itself unavailable instead of pretending.

    Word-level confidence is returned when the engine provides it.
    """

    name = "vosk"

    def __init__(self, model_path: str | Path = "", sample_rate: int = SAMPLE_RATE):
        self.model_path = Path(model_path) if model_path else Path()
        self.sample_rate = sample_rate
        self._model = None
        self._error = ""
        self._load_attempted = False
        self.stats = {"calls": 0, "words": 0, "total_ms": 0}

    # ------------------------------------------------------------------ model
    def available(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted and self._error:
            return False
        try:
            import vosk  # noqa: F401
        except Exception as exc:
            self._error = f"vosk package not installed: {exc}"
            self._load_attempted = True
            return False
        if not self.model_path or not Path(self.model_path).exists():
            self._error = (f"speech model not found at {self.model_path or '<unset>'} "
                           f"(download a Vosk model into data/models/)")
            self._load_attempted = True
            return False
        self._load_attempted = True
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            self._model = Model(str(self.model_path))
            return True
        except Exception as exc:
            self._error = f"model load failed: {exc}"
            return False

    # -------------------------------------------------------------- transcribe
    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        started = time.time()
        if not self.available():
            return TranscriptResult(False, provider=self.name, error=self._error)
        try:
            import json as _json

            from vosk import KaldiRecognizer
            rate = sample_rate or self.sample_rate
            recognizer = KaldiRecognizer(self._model, rate)
            recognizer.SetWords(True)
            pcm = _floats_to_pcm16(audio)
            recognizer.AcceptWaveform(pcm)
            payload = _json.loads(recognizer.FinalResult())
            words = payload.get("result") or []
            text = (payload.get("text") or "").strip()
            confidence = (sum(w.get("conf", 0.0) for w in words) / len(words)) if words else 0.0
            latency = int((time.time() - started) * 1000)
            self.stats["calls"] += 1
            self.stats["words"] += len(words)
            self.stats["total_ms"] += latency
            return TranscriptResult(bool(text), text=text, confidence=round(confidence, 3),
                                    provider=self.name, latency_ms=latency,
                                    language=language or "en")
        except Exception as exc:
            return TranscriptResult(False, provider=self.name, error=str(exc),
                                    latency_ms=int((time.time() - started) * 1000))

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "model": str(self.model_path) if self.model_path else None,
                "reason": "" if self.available() else self._error, "stats": dict(self.stats)}


class FasterWhisperSttProvider(SpeechToTextProvider):
    """Real multilingual offline speech recognition (faster-whisper / CTranslate2).

    Added inside the EXISTING SpeechToTextProvider abstraction — this is not a
    second voice pipeline. It exists because the configured Vosk model is
    `en-us` (English-only), which cannot serve the owner's Hindi / Hinglish /
    English speech.

    Language is AUTO-DETECTED (multilingual). Never force language="en".
    """

    name = "faster-whisper"

    def __init__(self, model_path: str | Path = "", sample_rate: int = SAMPLE_RATE,
                 model_size: str = "small", device: str = "cpu",
                 compute_type: str = "int8"):
        self.model_path = Path(model_path) if model_path else Path()
        self.model_size = model_size or "small"
        self.sample_rate = sample_rate
        self.device = device or "cpu"
        self.compute_type = compute_type or "int8"
        self._model = None
        self._error = ""
        self._load_attempted = False
        self.stats = {"calls": 0, "words": 0, "total_ms": 0}

    # ------------------------------------------------------------------ model
    def available(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted and self._error:
            return False
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:
            self._error = f"faster_whisper not installed: {exc}"
            self._load_attempted = True
            return False
        if not self.model_path or not Path(self.model_path).exists():
            self._error = (f"speech model not found at {self.model_path or '<unset>'}")
            self._load_attempted = True
            return False
        self._load_attempted = True
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                str(self.model_path), device=self.device,
                compute_type=self.compute_type)
            return True
        except Exception as exc:
            self._error = f"model load failed: {exc}"
            return False

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "model_id": self.model_size,
            "model_path": str(self.model_path) if self.model_path else "",
            "ready": self._model is not None,
            "loaded": self._model is not None,
            "language": "multilingual (auto-detect)",
            "error": self._error,
        }

    # -------------------------------------------------------------- transcribe
    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        started = time.time()
        if not self.available():
            return TranscriptResult(False, provider=self.name, error=self._error)
        try:
            import numpy as np
            samples = np.asarray(audio, dtype=np.float32)
            if samples.size == 0:
                return TranscriptResult(False, provider=self.name, error="empty audio")
            if sample_rate and sample_rate != self.sample_rate:
                # Resample linearly to the model's rate (16 kHz).
                factor = self.sample_rate / float(sample_rate)
                n = int(samples.size * factor)
                idx = (np.arange(n) / factor).astype(np.int64)
                idx = np.clip(idx, 0, samples.size - 1)
                samples = samples[idx]

            # Hinglish is a UI preference, not an ISO language accepted by Whisper.
            lang = (language or "").strip().lower()
            lang = {"hindi": "hi", "english": "en"}.get(lang, lang)
            if lang in ("", "auto", "hinglish", "mixed", "hi-en", "hi+en"):
                lang = None
            segments, info = self._model.transcribe(
                samples, beam_size=5, language=lang)
            text = " ".join(
                str(getattr(s, "text", "")).strip() for s in segments
                if str(getattr(s, "text", "")).strip()).strip()
            detected = getattr(info, "language", "") or ""
            self._error = ""
            self.stats["calls"] += 1
            self.stats["words"] += len(text.split())
            self.stats["total_ms"] += int((time.time() - started) * 1000)
            return TranscriptResult(
                bool(text), text=text, provider=self.name,
                confidence=(getattr(info, "language_probability", None)
                            if detected else None),
                language=detected)
        except Exception as exc:
            self._error = f"transcription failed: {exc}"
            return TranscriptResult(False, provider=self.name, error=self._error)


class FileSttProvider(SpeechToTextProvider):
    """Reads the transcript from a sidecar file (`speech.wav` -> `speech.txt`).

    This is the deterministic development/test path: it exercises the *real* pipeline
    (capture -> VAD -> segment -> transcript -> director -> action) without pretending that a
    cloud model was called. It is explicitly marked `synthetic` in its result.
    """

    name = "file-stt"

    def __init__(self, mapping: Optional[Dict[str, str]] = None):
        self.mapping = dict(mapping or {})

    def available(self) -> bool:
        return True

    def bind(self, audio_path: str | Path, transcript: str) -> None:
        self.mapping[str(audio_path)] = transcript

    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        started = time.time()
        text = ""
        for path, value in self.mapping.items():
            if Path(path).exists():
                sidecar = Path(path).with_suffix(".txt")
                if sidecar.exists():
                    text = sidecar.read_text(encoding="utf-8").strip()
                    break
                text = value
                break
        return TranscriptResult(bool(text), text=text, confidence=1.0, provider=self.name,
                                latency_ms=int((time.time() - started) * 1000),
                                language=language or "hinglish")

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": True,
                "note": "development provider — transcripts come from sidecar files"}


class NullSttProvider(SpeechToTextProvider):
    name = "unavailable"

    def __init__(self, reason: str = "no speech-to-text provider configured"):
        self.reason = reason

    def available(self) -> bool:
        return False

    def transcribe(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE,
                   language: str = "") -> TranscriptResult:
        return TranscriptResult(False, provider=self.name, error=self.reason)

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": False, "reason": self.reason}


# =============================================================== text to speech
# SAPI COM plumbing (dependency-free real speech on Windows)
CLSID_SpVoice = "{96749377-3391-11D2-9EE3-00C04F797396}"
IID_ISpVoice = "{6C44DF74-72B9-4992-A1EC-EF996E0422D4}"
SPF_DEFAULT = 0
SPF_ASYNC = 1
SPF_PURGEBEFORESPEAK = 2

# ISpVoice vtable layout — the interface inherits ISpEventSource <- ISpNotifySource, so the
# indices are NOT the ISpVoice-local ones. Verified against the real COM object on this
# machine (a wrong index silently calls the wrong method and can crash the process).
SPV_SETNOTIFYSINK = 3
SPV_WAITFORNOTIFYEVENT = 8          # WaitUntilDone(ms)
SPV_SETOUTPUT = 14
SPV_PAUSE = 17
SPV_RESUME = 18
SPV_SETRATE = 19
SPV_GETRATE = 20
SPV_SETVOLUME = 21
SPV_GETVOLUME = 22
SPV_SPEAK = 25


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> "_Guid":
    from uuid import UUID
    u = UUID(text)
    return _Guid(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class WindowsSapiTts(TextToSpeechProvider):
    """Real Windows speech synthesis through SAPI.

    Architecture (A-039/A-040): a **dedicated worker thread owns the COM object**. COM
    apartments are per-thread, and other components (UI Automation) switch the calling thread
    to STA — so creating the voice on one thread and calling it from another either fails or
    blocks forever. One owner thread + a command queue removes the problem entirely, and makes
    `stop()` (barge-in) reliable because the purge runs on the same thread that is speaking.

    Barge-in: `stop()` queues Speak("", SVSFPurgeBeforeSpeak) and is measured at ~20-120 ms.
    """

    name = "windows-sapi"
    SVSF_ASYNC = 1
    SVSF_PURGE_BEFORE_SPEAK = 2
    RUNNING_STATE = 2                     # SpeechRunState.SRSEDone/Running

    def __init__(self, rate: int = 0, volume: int = 100, startup_timeout: float = 4.0):
        self.rate = rate
        self.volume = volume
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._speaking = threading.Event()
        self._ready = threading.Event()
        self._ok = False
        self._error = ""
        self._thread = threading.Thread(target=self._worker, name="sapi-voice", daemon=True)
        self._thread.start()
        self._ready.wait(startup_timeout)

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        voice = None
        try:
            import comtypes
            import comtypes.client as cc
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except OSError:
                pass                       # apartment already chosen for this thread
            voice = cc.CreateObject("SAPI.SpVoice")
            voice.Volume = self.volume
            voice.Rate = self.rate
            self._ok = True
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
            return
        self._ready.set()

        while True:
            try:
                command, args, ack = self._queue.get(timeout=0.05)
            except queue.Empty:
                # keep the speaking flag honest without blocking on WaitUntilDone
                if self._speaking.is_set():
                    try:
                        if voice.Status.RunningState != self.RUNNING_STATE:
                            self._speaking.clear()
                    except Exception:
                        self._speaking.clear()
                continue

            try:
                if command == "shutdown":
                    break
                if command == "speak":
                    text, vol, rate, asynchronous = args
                    if vol != self.volume:
                        voice.Volume = vol
                    if rate:
                        voice.Rate = rate
                    voice.Speak(text, self.SVSF_ASYNC if asynchronous else 0)
                    if asynchronous:
                        self._speaking.set()
                elif command == "stop":
                    voice.Speak("", self.SVSF_PURGE_BEFORE_SPEAK | self.SVSF_ASYNC)
                    self._speaking.clear()
            except Exception as exc:
                self._error = str(exc)
            finally:
                if ack is not None:
                    ack.set()
                self._queue.task_done()

    def _submit(self, command: str, args: tuple = (), wait: bool = True,
                timeout: float = 10.0) -> bool:
        ack = threading.Event()
        self._queue.put((command, args, ack))
        return ack.wait(timeout) if wait else True

    # -------------------------------------------------------------------- API
    def available(self) -> bool:
        if not IS_WINDOWS:
            return False
        self._ready.wait(2.0)
        return self._ok

    def synthesize(self, text: str, *, voice: str = "", rate: int = 0,
                   volume: int = 100, out_path: Optional[str] = None) -> TtsResult:
        started = time.time()
        if not self.available():
            return TtsResult(False, provider=self.name,
                             error=self._error or "SAPI unavailable")
        ok = self._submit("speak", (text, volume, rate, False), timeout=180.0)
        return TtsResult(ok, provider=self.name, spoken=ok,
                         duration_ms=int((time.time() - started) * 1000),
                         latency_ms=int((time.time() - started) * 1000),
                         error="" if ok else (self._error or "Speak failed"))

    def speak_async(self, text: str, *, voice: str = "", rate: int = 0,
                    volume: int = 100) -> TtsResult:
        started = time.time()
        if not self.available():
            return TtsResult(False, provider=self.name,
                             error=self._error or "SAPI unavailable")
        ok = self._submit("speak", (text, volume, rate, True), timeout=10.0)
        return TtsResult(ok, provider=self.name, spoken=ok,
                         latency_ms=int((time.time() - started) * 1000),
                         error="" if ok else (self._error or "Speak failed"))

    def stop(self) -> bool:
        """Purge pending/ongoing speech — the barge-in primitive."""
        if not self._ok:
            return False
        ok = self._submit("stop", (), timeout=5.0)
        self._speaking.clear()
        return ok

    def wait_until_done(self, timeout_ms: int = 60_000) -> bool:
        """Wait until the utterance finishes or is interrupted (no COM call: safe anywhere)."""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if not self._speaking.is_set():
                return True
            time.sleep(0.02)
        return False

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self._ok, "speaking": self._speaking.is_set(),
                "rate": self.rate, "volume": self.volume, "error": self._error,
                "thread_alive": self._thread.is_alive()}

    def close(self) -> None:
        try:
            self._queue.put(("shutdown", (), None))
        except Exception:
            pass


class HttpTtsProvider(TextToSpeechProvider):
    """OpenAI-compatible /audio/speech endpoint (needs a provider key)."""

    name = "http-tts"

    def __init__(self, base_url: str = "", api_key_ref: str = "", model: str = "tts-1",
                 voice: str = "alloy", vault=None, timeout_s: float = 60.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key_ref = api_key_ref
        self.model = model
        self.voice = voice
        self.vault = vault
        self.timeout_s = timeout_s

    def _key(self) -> str:
        if not self.vault or not self.api_key_ref:
            return ""
        return self.vault.resolve(self.api_key_ref) or ""

    def available(self) -> bool:
        return bool(self.base_url and self._key())

    def synthesize(self, text: str, *, voice: str = "", rate: int = 0,
                   volume: int = 100, out_path: Optional[str] = None) -> TtsResult:
        started = time.time()
        if not self.available():
            return TtsResult(False, provider=self.name,
                             error="no speech endpoint/key configured")
        try:
            body = json.dumps({"model": self.model, "input": text,
                               "voice": voice or self.voice}).encode("utf-8")
            req = urllib.request.Request(f"{self.base_url}/audio/speech", data=body,
                                         method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {self._key()}")
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                audio = resp.read()
            path = ""
            if out_path:
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(audio)
                path = str(out_path)
            return TtsResult(True, provider=self.name, audio_path=path,
                             bytes_out=len(audio),
                             latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return TtsResult(False, provider=self.name, error=str(exc))

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "base_url": self.base_url or None,
                "reason": "" if self.available() else "no speech endpoint/key configured"}


class FileTtsProvider(TextToSpeechProvider):
    """Writes a WAV file (synthetic tone scaled to the text length).

    Development/test provider: it produces a *real* audio artifact of the right duration so
    the pipeline, playback and barge-in can be exercised without a speech service. Results
    are marked `synthetic=True` and are never presented as real speech.
    """

    name = "file-tts"

    def __init__(self, out_dir: str | Path = "."):
        self.out_dir = Path(out_dir)

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, *, voice: str = "", rate: int = 0,
                   volume: int = 100, out_path: Optional[str] = None) -> TtsResult:
        started = time.time()
        # ~14 characters per second of speech
        duration_ms = max(400, int(len(text) / 14.0 * 1000))
        target = Path(out_path) if out_path else \
            self.out_dir / f"tts_{int(time.time() * 1000)}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        samples = _tone(duration_ms, SAMPLE_RATE, amplitude=0.12)
        write_wav(target, samples, SAMPLE_RATE)
        return TtsResult(True, provider=self.name, audio_path=str(target),
                         bytes_out=target.stat().st_size, duration_ms=duration_ms,
                         latency_ms=int((time.time() - started) * 1000), synthetic=True)

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": True, "out_dir": str(self.out_dir),
                "note": "synthetic test audio — not real speech"}


class WindowsSapiFileTts(TextToSpeechProvider):
    """Real Windows SAPI speech rendered to a WAV file.

    Used when audio must be an artifact (acoustic tests, message playback, offline review)
    rather than played immediately. This is *real* synthesised speech, not a tone.
    """

    name = "windows-sapi-file"

    def __init__(self, out_dir: str | Path = "."):
        self.out_dir = Path(out_dir)
        self._error = ""

    def available(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            import comtypes.client as cc
            cc.CreateObject("SAPI.SpVoice")
            return True
        except Exception as exc:
            self._error = str(exc)
            return False

    def synthesize(self, text: str, *, voice: str = "", rate: int = 0,
                   volume: int = 100, out_path: Optional[str] = None) -> TtsResult:
        started = time.time()
        if not self.available():
            return TtsResult(False, provider=self.name, error=self._error or "SAPI unavailable")
        target = Path(out_path) if out_path else \
            self.out_dir / f"speech_{int(time.time() * 1000)}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            import comtypes.client as cc
            speaker = cc.CreateObject("SAPI.SpVoice")
            # Prefer a natural female Windows voice for the assistant. Keep the
            # explicit caller-selected voice when one is supplied, and fall back
            # safely on systems that do not ship a matching token.
            if not speaker:
                raise RuntimeError("SAPI voice unavailable")
            try:
                wanted = (voice or "").strip().lower()
                female_markers = ("female", "zira", "hazel", "susan", "samantha",
                                  "aria", "jenny", "sara")
                for i in range(speaker.GetVoices().Count):
                    token = speaker.GetVoices().Item(i)
                    description = str(token.GetDescription() or "").lower()
                    if (wanted and wanted in description) or (
                            not wanted and any(marker in description for marker in female_markers)):
                        speaker.Voice = token
                        break
            except Exception:
                pass
            stream = cc.CreateObject("SAPI.SpFileStream")
            stream.Format.Type = 22                 # 22 kHz 16-bit mono
            stream.Open(str(target), 3, False)      # SSFMCreateForWrite
            speaker.AudioOutputStream = stream
            speaker.Volume = volume
            if rate:
                speaker.Rate = rate
            speaker.Speak(text)
            stream.Close()
            size = target.stat().st_size if target.exists() else 0
            samples, rate_hz = read_wav(target)
            duration_ms = int(len(samples) / (rate_hz or 1) * 1000)
            return TtsResult(True, provider=self.name, audio_path=str(target),
                             bytes_out=size, duration_ms=duration_ms,
                             latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            self._error = str(exc)
            return TtsResult(False, provider=self.name, error=str(exc))

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "out_dir": str(self.out_dir), "error": self._error}


class NullTtsProvider(TextToSpeechProvider):
    name = "unavailable"

    def __init__(self, reason: str = "no text-to-speech provider configured"):
        self.reason = reason

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, **kwargs) -> TtsResult:
        return TtsResult(False, provider=self.name, error=self.reason)

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": False, "reason": self.reason}


# ================================================================ audio output
class SoundDeviceOutput(AudioOutputProvider):
    """Streaming playback through the optional `sounddevice` provider (stoppable)."""

    name = "sounddevice"

    def __init__(self, device: Optional[int] = None, sample_rate: int = SAMPLE_RATE):
        self.device = device
        self.rate = sample_rate
        self._stream = None
        self._stop = threading.Event()
        self.stats = {"plays": 0, "stopped": 0, "errors": 0}

    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            return True
        except Exception:
            return False

    def play(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE) -> bool:
        if not self.available() or not audio:
            return False
        import numpy as np
        import sounddevice as sd
        self._stop.clear()
        try:
            self._stream = sd.OutputStream(device=self.device, channels=1,
                                           samplerate=sample_rate, dtype="float32")
            self._stream.start()
            arr = np.asarray(audio, dtype="float32")
            block = max(1, int(sample_rate * 0.05))
            for start in range(0, len(arr), block):
                if self._stop.is_set():
                    self.stats["stopped"] += 1
                    break
                self._stream.write(arr[start:start + block].reshape(-1, 1))
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self.stats["plays"] += 1
            return True
        except Exception as exc:
            log.error("playback failed: %s", exc)
            self.stats["errors"] += 1
            return False

    def play_file(self, path: str) -> bool:
        samples, rate = read_wav(path)
        return self.play(samples, rate)

    def stop(self) -> bool:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        return True

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(),
                "playing": self._stream is not None, "stats": dict(self.stats)}


class WavFileOutput(AudioOutputProvider):
    """Writes playback to a WAV file instead of the speakers (development/tests)."""

    name = "wav-file"

    def __init__(self, out_dir: str | Path = "."):
        self.out_dir = Path(out_dir)
        self.last_path = ""

    def available(self) -> bool:
        return True

    def play(self, audio: Sequence[float], sample_rate: int = SAMPLE_RATE) -> bool:
        self.last_path = str(self.out_dir / f"play_{int(time.time() * 1000)}.wav")
        write_wav(self.last_path, audio, sample_rate)
        return True

    def play_file(self, path: str) -> bool:
        samples, rate = read_wav(path)
        return self.play(samples, rate)

    def stop(self) -> bool:
        return True

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": True, "out_dir": str(self.out_dir)}


# ============================================================== realtime provider
class GeminiLiveProvider(RealtimeVoiceProvider):
    """Legacy discovery facade. LiveSession owns the complete audio transport."""

    name = "gemini-live"

    def __init__(self, model: str = "gemini-3.8-live", vault=None,
                 api_key_ref: str = "secret://provider/gemini/key",
                 endpoint: str = ""):
        self.model = model
        self.vault = vault
        self.api_key_ref = api_key_ref
        self._last_error = ""

    def _key(self) -> str:
        if not self.vault:
            return ""
        return self.vault.resolve(self.api_key_ref) or ""

    def available(self) -> bool:
        return bool(self._key())

    def connect(self) -> bool:
        self._last_error = ("no Gemini API key in the vault" if not self._key()
                            else "Start realtime voice through VoiceService / LiveSession")
        return False

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": self.available(), "model": self.model,
                "reason": self._last_error or ("" if self.available() else "no API key")}


class UnavailableRealtimeProvider(RealtimeVoiceProvider):
    name = "unavailable"

    def __init__(self, reason: str = "no realtime voice provider configured"):
        self.reason = reason

    def available(self) -> bool:
        return False

    def connect(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": False, "reason": self.reason}


# ====================================================================== WAV I/O
def write_wav(path: str | Path, samples: Sequence[float], sample_rate: int = SAMPLE_RATE) -> str:
    """Write mono float32 samples as 16-bit PCM WAV."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(_floats_to_pcm16(samples))
    return str(target)


def read_wav(path: str | Path) -> tuple[List[float], int]:
    """Read a WAV file as mono float32 samples."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width == 2:
        count = len(frames) // 2
        values = struct.unpack(f"<{count}h", frames[:count * 2])
        samples = [v / 32768.0 for v in values]
    elif width == 1:
        samples = [(b - 128) / 128.0 for b in frames]
    else:
        samples = []
    if channels > 1:
        samples = samples[::channels]
    return samples, rate


def _floats_to_pcm16(samples: Sequence[float]) -> bytes:
    clipped = [max(-1.0, min(1.0, float(s))) for s in samples]
    return struct.pack(f"<{len(clipped)}h", *[int(s * 32767) for s in clipped])


def samples_to_wav_bytes(samples: Sequence[float], sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(_floats_to_pcm16(samples))
    return buf.getvalue()


def _tone(duration_ms: int, sample_rate: int, amplitude: float = 0.12,
          freq: float = 220.0) -> List[float]:
    import math
    n = int(sample_rate * duration_ms / 1000)
    return [amplitude * math.sin(2 * math.pi * freq * t / sample_rate) for t in range(n)]
