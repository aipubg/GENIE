# GENIE — VOICE EVIDENCE

**Status:** 🟩 **VOICE ARCHITECTURE GREEN** — with one recorded outstanding item:

```text
PHYSICAL OWNER-MIC ACCEPTANCE TEST = PENDING
```

The real STT engine (offline Vosk) is implemented and verified on real audio, so development
continues. The owner runs one command when convenient — GENIE does not block on it and will not
keep asking:

```bash
python genie.py voice-e2e
```

**Vosk is a local degraded/development fallback, not a permanent dependency.** The voice stack
stays provider-agnostic (`SpeechToTextProvider` / `RealtimeVoiceProvider` contracts): a cloud or
realtime provider can replace it later without touching the pipeline.

Real speech recognition is implemented and verified on real audio. The final
**physical-microphone → transcript** step cannot be verified from inside this machine: the
microphone in this environment cannot hear the speakers at usable signal quality (measured
SNR **1.0 dB** — see §4). A human speaking directly into the microphone is the remaining
verification, and it is a single command (§5).

---

## 1. What the owner asked to be proven

```text
physical microphone audio → VAD → real STT → transcript → Turn Manager
  → NEDLE2 → capability → real Windows execution → read-back verification → spoken confirmation
```

No transcript injection anywhere in this path.

## 2. Verified in this environment

| Step | Evidence | Real? |
|---|---|---|
| Microphone present | `Microphone (Realtek(R) Audio)` enumerated | ✅ real device |
| Microphone capture | 8 000 samples in 0.5 s, streaming 20 ms blocks | ✅ real capture |
| VAD on captured audio | speech_start / speech_end events, adaptive threshold | ✅ |
| **Real STT engine** | **Vosk (offline Kaldi)** — model `vosk-model-small-en-us-0.15` | ✅ real recognizer |
| **Real STT output** | SAPI speaks "volume 30" → WAV (75 204 bytes, 1 704 ms) → **transcript `"volume thirty"`, confidence 1.0, 364 ms** | ✅ no injection |
| Spoken number → digit | `normalize("volume thirty") == "volume 30"` | ✅ |
| Transcript → director | NEDLE2 routes it (warm ≈ 628 ms) | ✅ |
| Capability → execution | `system.volume.set` on the real machine | ✅ |
| Read-back verification | Core Audio read-back equals the requested level | ✅ |
| Spoken confirmation | Windows SAPI through the real speaker | ✅ real audio |
| Barge-in | 66 ms stop latency, measured while actually speaking | ✅ |

Full test suite: **199 tests passing**, including 14 dedicated real-STT/preprocessing tests.

## 3. Provider chain (no vendor lock)

```text
STT : cloud HTTP (OpenAI-compatible) -> Vosk offline (real, default) -> sidecar dev -> unavailable
TTS : cloud HTTP -> Windows SAPI (live) -> SAPI-to-WAV (real file) -> synthetic dev -> unavailable
```

`python genie.py voice-status` prints which provider is actually active, and
`degraded_reasons()` names anything missing. With no cloud key at all, STT and TTS are both
**real** (Vosk + SAPI) — nothing is faked.

## 4. Why the automated acoustic loop does not pass here

The fully automated variant (`voice-acoustic`) has GENIE speak a phrase through the speakers
and tries to hear it through the microphone. Measured on this machine:

```text
ambient peak  : 0.01303
playback peak : 0.01462
SNR           : 1.0 dB        (usable speech needs roughly 15-20 dB)
```

With a 1 dB signal-to-noise ratio no recognizer can recover the words — the microphone is
essentially hearing the room, not the speaker. Increasing the system volume to 100 %, applying
up to 40× AGC gain, and testing every output device did not change this. The limitation is the
microphone/speaker geometry of this machine, **not** the pipeline: the same audio fed directly
to the recognizer transcribes perfectly (confidence 1.0).

Evidence commands:

```bash
python genie.py voice-acoustic --phrase "volume 30"   # shows capture + STT stages
python genie.py voice-devices                          # which mic/speaker are used
```

## 5. How the owner completes the verification (one command)

```bash
python genie.py voice-e2e --phrase "GENIE, awaz 30 kar do" --expect awaz
```

Speak when it says `>>> listening now…`. It prints the full stage log:

```text
capture   : device, duration_ms, samples, vad_events, speech_started_at/ended_at,
            peak_level + a level hint (healthy / low / very low)
stt       : provider (vosk), transcript, confidence, latency_ms, preprocessing gain
turn      : normalised input, director + confidence, capability, mission_id,
            mission_state, executed, verified, verification detail, strategy
speech    : TTS provider, synthetic flag, spoken text
total_ms  : end-to-end latency
```

Two more manual checks with the same command:

```bash
python genie.py voice-e2e --phrase "GENIE Chrome kholo"        # real Hinglish command
# then, while GENIE is speaking a long reply, say: "Nahi, YouTube kholo"
# -> barge-in fires, playback stops, a fresh transcript is produced, the new intent executes
```

If the level hint says `low`/`very low`, the log shows the measured peak so the microphone or
input gain can be fixed before judging the pipeline.

## 6. Barge-in during real microphone speech

The barge-in path is already verified with real audio (66 ms stop) and is driven by the same
VAD that the microphone feeds:

```text
mic speech detected while state == SPEAKING
  -> TTS purge + playback stop (66 ms measured)
  -> turn marked interrupted, conversation state kept
  -> the new utterance is transcribed by the real recognizer and executed
```

The microphone-driven variant is part of the same `voice-e2e` run: interrupt GENIE while it is
speaking and the log shows `interruptions` incrementing with a fresh transcript afterwards.

## 7. What changed to make real STT possible

| Change | Why |
|---|---|
| `voice/providers.py`: **VoskSttProvider** | real offline recognizer, no cloud key needed |
| `voice/providers.py`: **WindowsSapiFileTts** | real speech rendered to a WAV artifact (acoustic tests, playback) |
| `voice/audio.py` (new) | DC removal, high-pass, bounded AGC — the missing preprocessing stage; real mics deliver very quiet signals |
| `director/normalize.py` | spoken numbers → digits ("volume thirty" → "volume 30") |
| `voice/pipeline.py` | `capture_utterance()` + `transcribe_audio()` expose the real mic→STT path with stage timings |
| `voice/service.py` | `real_e2e()` and `acoustic_loop()` produce the full stage log |
| `genie.py` | `voice-e2e`, `voice-acoustic` commands |

## 8. Honest summary

- Real STT: **done** (Vosk, offline, verified on real audio with confidence 1.0).
- Real mic capture + VAD: **done**.
- Real mic → STT → action → verified → spoken: **implemented and one command away**; blocked
  only by this machine's microphone-to-speaker signal quality (1 dB SNR).
- Nothing in this path uses injected transcripts, and the development providers are clearly
  labelled (`synthetic`, `file-stt`) so a test can never mistake them for real recognition.
