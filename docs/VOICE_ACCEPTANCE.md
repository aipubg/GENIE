# GENIE Voice Acceptance

## Real-machine procedure (owner / real hardware)

1. Launch GENIE (`python genie.py daemon`, then the Electron shell).
2. Verify the gem is idle: dim sapphire, `aria-label = "GENIE voice: off"`.
3. Click the blue gem.
4. Verify the microphone actually starts (`GET /api/voice` → capturing true).
5. Speak softly → gem glow rises slightly.
6. Speak louder → gem glow rises further.
7. Stop speaking → glow falls naturally (smoothing 0.72/0.28).
8. Allow the utterance to complete → processing state.
9. Verify the GENIE response.
10. Verify the speaking shimmer.
11. Click the gem while GENIE speaks → TTS stops, listening begins.
12. Click again → microphone really turns off.
13. Confirm no hidden capture remains active.

## Automated pre-conditions

`node --test ui/tests/frontend` — 19 deterministic tests, no paid provider:

- companion state mapping (listening/thinking/working/speaking/success/error/offline)
- renderer contract presence
- unknown-state rejection
- gem transitions (idle→listening→idle, speaking→listening, processing→idle)
- amplitude clamping
- honest empty-state text
- greeting by time of day

## Amplitude verification

`VOICE_AMPLITUDE` payload:

```json
{ "level": 0.42, "rms": 0.0712, "source": "microphone", "synthetic": false, "ts": 1789737477680 }
```

`level` is the smoothed, clamped 0..1 display value; `rms` is the true per-frame
RMS. `synthetic` is always `false`. If it is ever absent or true, the UI must
not animate the gem from it.

Verified: silence → 0.0, loud speech → ~1.0.

## Not acceptable

- faking microphone amplitude from transcript, word count, timers or randomness
- animating the gem while the microphone is off
- keeping a hidden capture alive for visual effect
- a second independent microphone capture in the frontend
