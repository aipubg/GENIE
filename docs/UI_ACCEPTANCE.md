# GENIE UI Acceptance

## Geometry vs the approved reference

`python ui/tests/visual/compare.py` derives the reference geometry by edge
detection and compares it with real DOM bounding boxes.

| Metric | Rendered | Reference | Delta | Result |
|---|---|---|---|---|
| Sidebar width | 219.0 | 218.0 | 1.0 | pass |
| Rail x start | 1177.0 | 1176.0 | 1.0 | pass |
| Rail width | 359.0 | 360.0 | 1.0 | pass |

Tolerance 8px. Deliberately **not** pixel equality: fonts, GPU rasterisation and
anti-aliasing legitimately differ.

Vertical composition was derived from text bands located by horizontal stroke
energy in the reference ("Good Evening" y284–308, subtitle y336–356,
command-bar text y~896, chips y~960). Rendered: greeting h1 y=264.8,
command bar y=868, chips y=936.

## Screenshots (real Chromium, `ui/screenshots/`)

| Area | Files |
|---|---|
| Home | `home-idle`, `home-companion`, `home-listening`, `home-thinking`, `home-speaking` |
| Home variants | `home-narrow`, `home-narrower`, `home-quality-low`, `home-reduced-motion`, `home-offline` |
| Operational | `chat`, `missions`, `agents`, `computer`, `skills`, `devices`, `memory`, `knowledge`, `media`, `forecast`, `security`, `settings` |

Capture: `python ui/tests/visual/capture.py` (daemon must be running).

## Acceptance checklist

| # | Requirement | Status |
|---|---|---|
| 1 | Sidebar / rail geometry matches reference | ✓ within 1px |
| 2 | Real DOM components, no flat background image | ✓ |
| 3 | Left navigation works (13 items) | ✓ |
| 4 | Golden lamp central and permanent | ✓ |
| 5 | Blue gem is the voice button | ✓ |
| 6 | No mic icon on the lamp | ✓ |
| 7 | No right-side listening card | ✓ |
| 8 | No Home waveform | ✓ |
| 9 | Gem toggles listening on/off | ✓ |
| 10 | Real microphone amplitude drives the gem | ✓ (RMS, `synthetic:false`) |
| 11 | Processing / speaking states | ✓ |
| 12 | Barge-in | ✓ (`/api/voice/barge-in`) |
| 13 | Mic really off when voice off | ✓ |
| 14 | Companion implemented | ✓ `StaticCompanionRenderer` |
| 15 | Companion states from real events | ✓ |
| 16 | Windows icon 16..256 | ✓ verified |
| 17 | Honest empty states, no fake data | ✓ |
| 18 | Responsive collapse order | ✓ |
| 19 | Quality modes + reduced motion | ✓ |
| 20 | Accessibility (role, aria, keyboard, labels) | ✓ |

## Known gaps

(Streaming, TTS amplitude, compact mode, Forecast and Security were on this list
and are now implemented — see their sections below.)

1. **Installer signing** — `GENIE Setup 0.1.0.exe` is built but **unsigned**.
   Code signing had to be skipped on this host (extracting `winCodeSign` fails
   with "Cannot create symbolic link: a required privilege is not held"), so
   Windows SmartScreen will warn until a certificate is attached. Everything
   else about the installer is verified — see below.

Forecast and Security were on this list and are now wired: `ForecastService` and
`SecurityFindingService` are registered on the daemon and exposed read-only at
`GET /api/forecast/calibration` and `GET /api/security/findings`. Both pages show
real status and honest empty states. MiroFish remains NOT LIVE, so no numeric
probability is offered without evidence.

## Implemented during this work (were previously gaps)

- **Token streaming** — `POST /api/chat/stream` emits real SSE deltas from the
  provider. Reports `mode: tokens` / `single` / `actions` so a non-streaming
  provider is never presented as streaming.
- **TTS output amplitude** — measured from the PCM of the audio actually played
  (`voice/tts_level.py`); reports `available: false` when there is no audio
  buffer to measure instead of synthesising an envelope.
- **Compact floating mode** — `ui/web/compact.html`, 320x420 frameless
  always-on-top window; the gem uses the same voice contract as Home.


## Windows installer — BUILT

`electron-builder` bundles its own NSIS, so Inno Setup 6 is **not** required —
the earlier "cannot be produced here" note was wrong.

| Artifact | Detail |
|---|---|
| `dist-electron/GENIE Setup 0.1.0.exe` | rc13 — 82,420,646 bytes, SHA-256 `6d5f9000…`, valid PE (MZ), NSIS markers present |
| `dist-electron/win-unpacked/GENIE.exe` | packaged app, GENIE icon resource embedded (verified at all 8 sizes) |

productName `GENIE`, appId `ai.genie.desktop`, canonical `app-icon.ico`.

Current SHA-256: `6d5f900098609f34651916369f01cedbffbe6106c28cb3702d0d91c1fd06d274`
(rc13, 82,420,646 bytes).

### Historical

- rc12 — SHA-256 `3c7a4fbb3431212da9c8cb8a4a0cc27047f29943061ddfa569e1dc4770bc34b7`, 82,420,694 bytes.
- rc11 — 82,088,453 bytes.

Build hazards recorded in the commit history: npm needs `--prefix` here; npm's
cleanup step leaves `*.DELETE.<hash>` files that must be renamed back;
electron-builder needs Electron installed locally to resolve its version; and
the final NSIS step throws inside the sandbox delete guard **after** artifacts
are written, so a non-zero exit does not mean the build failed.

Build output is gitignored (`dist-electron/`, `ui/electron/node_modules/`).
