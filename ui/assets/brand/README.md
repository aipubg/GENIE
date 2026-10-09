# GENIE brand assets

The owner supplied these assets as the locked visual identity. **Do not**
replace them with stock illustrations, generic avatars, emojis, or
alternate characters. If a missing state needs additional art, use motion
and effects around the canonical asset rather than inventing a new identity.

| File | Role | Source |
|---|---|---|
| `desktop-ui-reference.png` | Final desktop Home reference — the pixel-faithful target the implementation must match | owner-supplied, Sep 2026 |
| `genie-companion.png` | Transparent in-app GENIE companion visual — used by the CompanionLayer, sidebar presence, welcome/emergence, compact companion, assistant empty-state | owner-supplied, Sep 2026 |
| `companion-states-reference.png` | Behavioural reference for the companion (startup / emergence / welcome / idle / listening / thinking / working / response / mini / rest) — not a layout replacement | owner-supplied, Sep 2026 |
| `app-icon-master.png` | Master Windows / desktop application icon — used for EXE, taskbar, Start Menu, shortcut, installer, tray, window icon | owner-supplied, Sep 2026 |
| `icon.png` | Alternate icon master variant (owner-supplied duplicate source) — preserved alongside the primary | owner-supplied, Sep 2026 |

## Windows icon generation

Generate the required ICO sizes from `app-icon-master.png` and ship in this
folder as `app-icon.ico` plus per-size PNGs:

- 16, 20, 24, 32, 48, 64, 128, 256

Wire the ICO into the Electron `BrowserWindow` icon and the packaged EXE.

## Provenance

- Project: GENIE / G3
- Owner-supplied, single point of truth for visual identity
- Tracking: tracked in Git under `ui/assets/brand/` (see `.gitignore`
  exceptions that keep these PNGs from being treated as transient media)

## Reserved directories

- `lamp/` — gold lamp / gem artwork (placeholders until owner-supplied)
- `environment/` — background / night-city environment (placeholders)

These directories exist so the locked Home composition has matching asset
slots; they should remain empty or hold lossless copies only.