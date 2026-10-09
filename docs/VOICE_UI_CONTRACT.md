# GENIE Voice UI Contract

## 1. The blue gem is the voice control

The blue gemstone set into the golden lamp **is** GENIE's primary voice button.

Explicitly **not** present on Home:

- no microphone icon on the lamp
- no separate microphone button
- no right-side listening card
- no waveform

The gem has an invisible enlarged hit target (up to 72px), `cursor: pointer`,
and full keyboard support.

### Accessibility

```
role="button"   tabindex="0"   Enter / Space
aria-pressed    aria-label = current voice state
```

State text (also `document.documentElement.dataset.voice`):

| Mode | Label |
|---|---|
| idle | `GENIE voice: off` |
| listening | `Listening` |
| processing | `Processing` |
| speaking | `Speaking` |
| muted | `GENIE voice: paused` |
| unavailable | `Voice unavailable` |

---

## 2. Click behaviour

| Current state | Action | Result |
|---|---|---|
| idle | `POST /api/voice/start` | listening |
| listening | `POST /api/voice/stop` | idle (mic really off) |
| speaking | `POST /api/voice/barge-in` then `start` | listening |
| processing | `POST /api/voice/stop` | idle |

The frontend **reflects** authoritative backend voice state; it does not run a
duplicate state machine.

---

## 3. Real microphone amplitude — never faked

The gem's brightness tracks **actual microphone energy**.

```
mic capture (existing voice pipeline)
  → true RMS per frame
  → normalise 0..1 (display gain 6.0, clamp)
  → smooth: display = prev*0.72 + cur*0.28
  → throttle ~30 updates/sec
  → event VOICE_AMPLITUDE { level, rms, source, synthetic:false }
  → SSE → frontend → CSS var --voice-level
```

Implemented in `voice/pipeline.py` (`_frame_rms`, `_emit_amplitude`,
`amplitude()`), reusing the **same** microphone frames the VAD already
consumes. No second capture is opened.

Never derived from: transcript text, word count, timers, randomness, or
CSS-only animation. The display gain never alters what STT receives.

Verified: silence → `0.0`, loud speech → `~1.0`.

### Performance

`--voice-level` is written to the document element at ≤ 30 Hz. The gem's glow
is a `box-shadow` driven by that variable, so animation is GPU-composited —
no React-style re-render, no DOM rebuild, no layout thrash.

---

## 4. TTS / speaking visual

Real TTS output amplitude is **now measured** (`voice/tts_level.py`).

When a TTS provider returns an audio **file**, its PCM is read and a real RMS
envelope is computed, then replayed in wall-clock time while the file plays. The
UI's `--tts-level` variable is therefore driven by the audio actually coming out
of the speaker.

```json
{ "level": 0.42, "available": true, "synthetic": false, "ts": 1789737477680 }
```

When there is **no audio buffer to measure** — for example a platform engine
that speaks directly (Windows SAPI) — the backend emits
`available: false` with a reason. It never synthesises an envelope; a fake one
would look identical on screen and be a lie. In that case the speaking state
keeps its restrained deterministic shimmer, and it stays labelled as such:

- `speaking` shimmer — **real amplitude when measurable, otherwise
  deterministic** (`data-tts-level` on `<html>` records which)
- `VOICE_SPOKEN` event marks the transition

Polled alternative: `GET /api/voice` → `tts_amplitude: {level, available}`.

Barge-in stops the meter immediately, so no level keeps animating after speech
has actually stopped.

---

## 5. Privacy

When voice state is off, microphone capture really is off
(`POST /api/voice/stop`). No hidden recording is kept alive for animation. The
jewel state never lies about microphone state.

---

## 6. Voice unavailable

If `/api/voice` reports the input provider unavailable, the gem is
desaturated, dimmed, marked `unavailable`, and the accessible label explains
it. Clicking does nothing.

---

## 7. Backend endpoints used

| Endpoint | Purpose |
|---|---|
| `GET /api/voice` | status, providers, amplitude |
| `POST /api/voice/start` | begin listening |
| `POST /api/voice/stop` | stop listening |
| `POST /api/voice/barge-in` | interrupt TTS |
| `POST /api/voice/say` | speak text |
| `GET /api/voice/devices` | input/output devices |
| `GET /api/voice/metrics` | voice metrics |

---

## 8. Settings-only mic meter

A conventional level meter exists **only** in Settings → Voice, driven by the
same real RMS. It is deliberately absent from Home.
