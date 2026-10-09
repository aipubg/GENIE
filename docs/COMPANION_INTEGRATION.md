# GENIE Companion Integration

**Status: IMPLEMENTED.**

The earlier "future only / do not build the character yet" decision is
superseded by the owner. The companion is a **visual/presence layer** over
GENIE's existing authority — never another agent, model, brain, mission store
or state machine.

```
GENIE backend state
        ↓
normalized companion state
        ↓
CompanionRenderer
        ↓
supplied companion visual
```

---

## 1. Canonical asset

| | |
|---|---|
| File | `ui/assets/brand/genie-companion.png` |
| Source | `ChatGPT Image Sep 15, 2026, 05_37_31 AM.png` (owner supplied) |
| Size | 1254 × 1254 |
| Format | PNG **RGBA** |
| Alpha | **0 … 255** (real transparency, verified) |

**This is the in-app GENIE identity only.** The desktop/application icon is a
different asset (`app-icon-master.png`). The two roles are not interchangeable.

Composition rules: no white matte, no chroma-key, no re-backgrounding, no
redrawing, no substitute character. Only scale, translate, opacity, glow,
light spill and subtle parallax.

---

## 2. Renderer contract

`ui/web/companion.js`

```js
class CompanionRenderer {
  mount(container)
  setState(state)
  setExpression(expr)
  setVoiceAmplitude(v)
  setAnchor(anchor)
  setQuality(level)
  summon(); minimize(); hide(); destroy()
}
```

Implemented today: **`StaticCompanionRenderer`** — the supplied PNG with
transform / opacity / scale / parallax / glow / light spill / minimal
particles.

Anchors (normalized, never absolute desktop pixels):

`lampAnchor` · `emergenceAnchor` · `companionIdleAnchor` · `compactAnchor` ·
`speechAnchor` · `returnToLampAnchor`

Future Live2D / Rive / Spine / 2.5D renderers slot in behind the same contract;
the rest of the UI is unaware.

---

## 3. States

14 normalized states:

`hidden` · `emerging` · `welcome` · `idle` · `attentive` · `listening` ·
`thinking` · `working` · `speaking` · `success` · `warning` · `error` ·
`offline` · `sleepy`

### Derived from real GENIE events

| Backend event | Companion state |
|---|---|
| `VOICE_LISTENING` | `listening` |
| `VOICE_TRANSCRIPT` / `VOICE_TRANSCRIPT_FAILED` | `thinking` |
| `VOICE_SPOKEN` | `speaking` |
| `VOICE_STOPPED` | `idle` |
| `BARGE_IN` | `attentive` |
| `MISSION_CREATED` | `working` |
| `MISSION_COMPLETED` | `success` |
| `MISSION_FAILED` | `warning` |
| `PROVIDER_FAILOVER` / `MODEL_UNAVAILABLE` | `error` |
| `DEVICE_OFFLINE` | `offline` |
| anything else | `null` → state unchanged |

**No fake activity timer.** Unknown events leave the state alone rather than
defaulting to "thinking".

---

## 4. Lamp relationship

The golden lamp is permanent and central. The companion **belongs to the lamp**:

```
black → environment fades in → lamp wakes → blue gem illuminates
      → companion softly emerges → settles into Home presence
```

On Home the companion rises **above** the lamp and settles in the clear band
between the quote and the lamp (rendered y ≈ 503–673 at 1536×1024). It does
not cover the greeting, does not dominate the screen, and the lamp keeps
breathing. On operational pages the companion is minimized/contextual and never
covers mission data, code, tables, findings or settings.

---

## 5. Voice amplitude

`setVoiceAmplitude()` receives **real microphone RMS** published by the existing
voice pipeline as `VOICE_AMPLITUDE` (`source: "microphone"`,
`synthetic: false`). It drives `--voice-level` (gem/lamp glow) at ≤ 30 updates
per second via a CSS variable — no DOM rebuild, no layout thrash.

Microphone input amplitude is **never** synthesised.

---

## 6. Accessibility

- The companion is decorative: `aria-hidden="true"`, `role="presentation"`.
- Its motion never conveys state that is not also available as text.
- Reduced motion simplifies breathing, emergence and particles.
- Quality modes scale effects down; LOW keeps the companion nearly static.

---

## 7. Upgrade path

1. `StaticCompanionRenderer` (shipped)
2. `TwoPointFiveDRenderer` — layered parallax on the same asset
3. `Live2DCompanionRenderer` / `RiveCompanionRenderer` — same contract, new
   asset rig

The UI calls only the contract, so no page changes.
