# GENIE UI Design System

All visual values live in **`ui/web/tokens.css`**. Components consume tokens;
inline hex values outside the token file are not allowed.

## Palette

| Role | Token | Value |
|---|---|---|
| Background deep | `--bg-deep-0..3` | `#050812` `#07101D` `#09172A` `#0A1C34` |
| Panel / glass | `--panel` | `rgba(10,22,42,.62)` |
| Hairline | `--hairline` | `rgba(110,150,210,.16)` |
| Sapphire primary | `--blue-500` | `#2478FF` |
| Sapphire bright | `--blue-300` | `#5CB5FF` |
| Gold primary | `--gold-500` | `#D79A3A` |
| Gold bright | `--gold-300` | `#FFD37A` |
| Text | `--text` | `#E7E8EF` |
| Text dim / muted | `--text-dim` / `--text-muted` | `#B7BDD0` / `#7E8499` |
| Ok / warn / bad | `--ok` / `--warn` / `--bad` | `#5DD8A8` / `#FFC86B` / `#FF7B7B` |

Gold and blue are **highlights**, never large filled areas.

## Typography

- `--font-serif` (Cormorant Garamond → Georgia) for display: greeting, page titles
- `--font-sans` (Segoe UI → system-ui) for all operational UI
- Scale: `--fs-xs 11` … `--fs-hero 56`

## Spacing, radius, blur, shadow, glow

- spacing `--s-1 4px` … `--s-10 72px`
- radius `--r-xs 6` … `--r-pill 999`
- blur `--blur-sm 6` / `--blur-md 14` / `--blur-lg 22`
- shadow `--shadow-sm/md/lg`
- glow `--glow-blue`, `--glow-blue-hot`, `--glow-gold`

## Motion

| Token | Value |
|---|---|
| `--motion-fast` | 120ms (hover) |
| `--motion-base` | 200ms (panels) |
| `--motion-slow` | 320ms (drawers) |
| `--motion-ambient` | 12s (background) |
| `--ease-out` | `cubic-bezier(.22,1,.36,1)` |

Motion is restrained. The interface should feel alive, not restless.

## Z-index

`--z-base 1` · `--z-rail 10` · `--z-topbar 20` · `--z-companion 30` ·
`--z-gem 40` · `--z-overlay 50` · `--z-toast 60`

## Layout (measured from the approved reference)

```
--rail-left-w   219px   (reference vertical edge at x=218..220)
--rail-right-w  359px   (reference rail starts x=1177)
--topbar-h       60px
```

## Runtime variables

- `--voice-level` 0..1 — **real** microphone RMS (never synthesised)
- `--tts-level` 0..1 — reserved for real TTS amplitude
- `--quality` `"LOW" | "BAL" | "HIGH"`

## Quality modes

| Mode | Environment | Particles | Blur | Companion |
|---|---|---|---|---|
| LOW | static | none | reduced | near-static |
| BALANCED | default | subtle | normal | breathing |
| HIGH | richer | more | full | richer |

Unfocused/minimized reduces decorative animation. `prefers-reduced-motion`
zeroes panel motion, disables particles and damps breathing while keeping all
functionality.

## Responsive

- `≤1180px` right rail collapsed
- `≤900px` sidebar icon-only
- centre remains primary; command bar always usable
- the desktop is never uniformly scaled down
