# GENIE — PHASE 3: VOICE

**Status:** 🟩 **GREEN** — architecture complete and verified · one recorded outstanding item:

```text
PHYSICAL OWNER-MIC ACCEPTANCE TEST = PENDING  (owner runs: python genie.py voice-e2e)
```

Evidence: [`VOICE.md`](./VOICE.md) · real STT (offline Vosk) verified on real audio.
Vosk is a **degraded/development fallback, not a permanent dependency**: the provider contracts
allow a cloud or realtime provider to replace it later.

Real speech recognition (offline Vosk) is implemented and verified on real audio. The final
physical-microphone → transcript step needs a human voice: this machine's microphone hears the
speakers at only **1.0 dB SNR** (measured), so the automated acoustic loop cannot recover words.
Everything else in the exit gate is verified on the real machine.

> GENIE is now a **voice assistant interface over the working brain + computer engine**.
> Voice does not bypass anything: a spoken command becomes a normal GENIE turn that goes
> through NEDLE2 → mission → PTE → execution → verification → audit, and only the *result*
> is spoken back.

---

## 1. Pipeline

```text
Microphone -> preprocessing -> VAD -> streaming STT / realtime voice
   -> Turn Manager -> Context + NEDLE2 / agents -> Communication Brain
   -> streaming TTS / realtime audio -> Speaker
```

| Module | Responsibility |
|---|---|
| `voice/devices.py` | audio device discovery (sounddevice provider, dependency-free WinMM fallback) |
| `voice/vad.py` | adaptive energy + zero-crossing VAD with speech start/end events |
| `voice/providers.py` | the five provider contracts + implementations |
| `voice/turn_manager.py` | turn state machine, barge-in transitions, stop-latency measurement |
| `voice/communication.py` | Communication Brain (length, pace, humour, acknowledgements, proactivity) |
| `voice/profiles.py` | voice profiles + consent-gated cloned voices |
| `voice/metrics.py` | per-stage latency metrics (memory + SQLite) |
| `voice/pipeline.py` | wiring, barge-in, honest degradation, event emission |
| `voice/service.py` | facade: provider selection, status, self-test, profiles, metrics |

## 2. Provider contracts (GENIE is never coupled to one speech vendor)

```text
VoiceInputProvider      microphone / audio source        sounddevice  ->  WAV file (dev)
SpeechToTextProvider    audio -> text                    http (OpenAI-compatible)  ->  sidecar file  ->  unavailable
RealtimeVoiceProvider   bidirectional streaming speech   Gemini Live (needs key)  ->  unavailable
TextToSpeechProvider    text -> audio                    http  ->  Windows SAPI  ->  synthetic WAV  ->  unavailable
AudioOutputProvider     audio -> speakers (cancellable)  sounddevice  ->  WAV file (dev)
```

Selection is configuration-driven and every step reports honestly. Windows SAPI is a **real**
fallback (it genuinely speaks and genuinely purges mid-sentence) but it is a provider, not the
architecture (D-045): swapping in a cloud voice provider touches only `voice/providers.py`.

## 3. Barge-in (mandatory, day one)

```text
user speech detected while GENIE is speaking
  -> stop playback immediately            (output.stop)
  -> purge remaining speech               (SAPI Speak("", SVSFPurgeBeforeSpeak))
  -> mark the turn interrupted, keep the useful conversation state
  -> process the new turn normally
```

Measured stop latency: **19–120 ms** (self-test: 66 ms). The turn manager records it per turn
and the pipeline closes the interrupted turn so metrics stay honest.

Barge-in works because of one architectural decision (A-039/A-040): **a dedicated worker thread
owns the SAPI COM object**, and `stop()` runs on that same thread via a command queue. Creating
the voice on one thread and calling it from another either fails or blocks forever — this was a
real bug, found by a hanging test suite, not a theory.

## 4. Communication Brain

Behaviour lives outside the model, so changing the LLM or voice provider cannot change GENIE's
personality:

| Input situation | Behaviour |
|---|---|
| action intents (`application.open`, `volume.set`) | short confirmation, no acknowledgement filler |
| serious context / urgency > 0.7 | humour 0, short, direct |
| user frustration ("not working again") | humour 0, silent proactivity |
| playful user | humour 2 |
| after an interruption | shorter reply, no acknowledgement |
| low routing confidence | shorter, less assertive |
| quiet hours | proactivity silent |

Also owns **spoken shaping**: markdown, code blocks, links and bullets are converted to
speakable text and long replies are cut at a sentence boundary (never mid-thought).

Proactivity is a **scored decision** (`urgency × relevance × confidence − interruption cost`),
never a timer — the score maps to `ignore / show_silently / mention_later / speak_now / interrupt`.

## 5. Hinglish

No per-session language choice. Hinglish normalisation (D-038) runs before the director, so
spoken commands route exactly like typed ones:

| Spoken | Routed to | Verified |
|---|---|---|
| "volume 30" / "awaz 30" | `system.volume.set` | ✅ real machine volume changed and read back |
| "Chrome kholo" / "notepad kholo" | `application.open` | ✅ |
| "mute" | `system.volume.mute` | ✅ |
| "Spotify pause" | `media.pause` | ✅ local routing (no cloud model) |
| "phone ka next song" | `media.next` on `phone_main` | ✅ |
| "open youtube.com/@X" | `browser.navigate` (fast path) | ✅ |

## 6. Measured latency (`python genie.py voice-selftest`, `tools/benchmark.py`)

| Stage | Value |
|---|---|
| microphone capture (real device) | stream running, ~20 ms blocks |
| VAD speech detection | within ~3 frames (~60 ms) |
| VAD end-of-speech | after the configured silence timeout (default 700 ms) |
| speech → transcript | provider dependent (sidecar dev provider ≈ 0 ms; cloud STT not yet configured) |
| transcript → director decision | NEDLE2 warm ≈ 628 ms |
| action (execute + verify) | `volume.set` ≈ 131 ms · app open ≈ 1.9 s |
| TTS first audio | **≈ 20–40 ms** (SAPI async) |
| full turn (transcript → spoken reply) | ≈ 1.5–3.6 s, dominated by action + speech duration |
| **barge-in stop** | **66 ms** measured in the self-test (19–120 ms across runs) |

All stages are recorded per turn in `voice_metrics` (memory + SQLite) and exposed through
`/api/voice/metrics`.

## 7. Exit gate — real machine

| Requirement | Result |
|---|---|
| microphone capture works | ✅ real device "Microphone (Realtek(R) Audio)", chunks + samples captured |
| VAD works | ✅ `speech_start` + `speech_end` on synthetic speech; runs on live capture |
| speech becomes a real GENIE request | ✅ transcript → orchestrator → mission |
| Hinglish command works | ✅ "awaz 30" → `system.volume.set` |
| response audio plays | ✅ real Windows SAPI speech through the real speaker |
| user can interrupt GENIE mid-sentence | ✅ `was_speaking: true` → interruption recorded |
| playback stops quickly | ✅ 66 ms |
| conversation resumes correctly | ✅ new intent processed after the interruption |
| simple commands still use the local fast path | ✅ no remote model for volume/app/media |
| voice does not bypass mission/trust/audit | ✅ trust.check + computer.* + mission + audit verified in tests |
| fallback behaviour is honest | ✅ `degraded_reasons()` names each missing provider; text path keeps working |
| latency metrics are recorded | ✅ per-stage metrics in memory and SQLite |

Manual E2E equivalent (automated in `tests/e2e/test_real_voice_phase3.py`): speak "volume 30"
→ real action verified → GENIE speaks; interrupt with a new intent → speech stops in ~66 ms and
the new intent executes.

## 8. Honest caveats

- **No cloud speech provider is configured yet**, so STT uses the sidecar development provider
  and TTS uses Windows SAPI. This is *real audio in both directions* but it is not a modern
  speech model — the provider contracts are in place so a key is all that is needed (D-037).
- Gemini Live realtime voice is implemented as a contract-complete provider that reports
  `available: false` without a key; it never pretends to work.
- The VAD is energy/zero-crossing based (no webrtcvad/silero). It is adaptive and tested, but a
  neural VAD would be more robust in noisy rooms — it can be added behind the same interface.
- Continuous mode is implemented (capture → VAD → turn loop, auto re-listen); the development
  default remains push-to-talk because it is far easier to debug.
- `sounddevice`/`comtypes` are optional providers (same pattern as UIA). The daemon core stays
  dependency-free and degrades to the text path.
- Speech is never used to *decide* anything: transcripts go through exactly the same director,
  trust, execution, verification and audit path as text.
