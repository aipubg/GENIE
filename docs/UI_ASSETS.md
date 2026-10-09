# GENIE UI Assets

Canonical directory: **`ui/assets/brand/`**

| Canonical file | Owner file | Role | Size | Alpha |
|---|---|---|---|---|
| `desktop-ui-reference.png` | `ui.png` | locked Home target | 1536×1024 | no |
| `companion-states-reference.png` | ChatGPT Sep 18 01_32_30 | behavioural reference | 1536×1024 | no |
| `genie-companion.png` | ChatGPT Sep 15 05_37_31 | **in-app identity** | 1254×1254 | **yes (0..255)** |
| `app-icon-master.png` | ChatGPT Sep 15 05_33_23 | **desktop identity** | 1254×1254 | no |

Exact hashes, provenance and verification status: **`ui/assets/brand/ASSET_MANIFEST.md`**.

## Two identity roles — not interchangeable

**A. Desktop / application identity** → `app-icon-master.png`
Windows EXE, desktop shortcut, taskbar, Start Menu, installer, Electron
`BrowserWindow` icon, tray.

**B. In-app GENIE identity** → `genie-companion.png`
CompanionLayer, startup emergence, Home presence, compact mode, assistant empty
states, About/brand areas.

The companion must not be scattered into every card, button or nav item.

## Derived files

- `app-icon.ico` — embedded sizes **16, 20, 24, 32, 48, 64, 128, 256**
- `app-icon-<n>.png` — per-size PNGs for Electron / Linux / macOS
- `environment/home-atmosphere.png` — environment-only layer cropped from the
  owner reference with sidebar, rails, top bar and command bar **removed**, so
  no interactive content is baked in

Derived files do not need to match the master hash; the master itself is
preserved losslessly and its hash is asserted by
`ui/tests/visual/verify-icon.js`.

## Rejected

`icon.png` — was byte-identical to `genie-companion.png` (same SHA-256). A
duplicate created during ingestion, **not** an owner-approved fifth asset.
Removed from canonical status.

## Serving

`/ui/assets/brand/*` is served by `core/ipc/server.py::_brand_asset` with a
path-traversal guard, keeping canonical owner assets outside the replaceable
`ui/web` bundle.
