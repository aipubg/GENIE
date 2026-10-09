# GENIE UI Architecture

**Status:** Home + 12 operational pages implemented on branch
`ui-final-genie-companion`. Real backend bindings; no fabricated state.

---

## 1. Principle — the UI is a thin client

The frontend may **display** state, **send** commands and **subscribe** to
events. It must never own:

| Forbidden in the frontend | Owner |
|---|---|
| Mission truth | Mission service |
| Memory truth | Memory service |
| PTE / permission authority | SecurityScope / PTE |
| Provider routing & model selection | Model Gateway |
| Agent decisions | Agent Factory |
| Device authority | Device Mesh |
| Audit truth | Audit service |

GENIE's execution contract stays untouched:

```
PTE CHECK → EXECUTE → VERIFY → AUDIT
PLAN → ACT → OBSERVE → VERIFY → RECOVER
```

None of that logic lives in UI code.

---

## 2. Files

```
ui/
  assets/brand/                 canonical owner assets (see ASSET_MANIFEST.md)
    desktop-ui-reference.png    locked Home target (1536x1024)
    companion-states-reference.png  behavioural reference, NOT a layout
    genie-companion.png         in-app identity (RGBA, alpha 0..255)
    app-icon-master.png         desktop identity
    app-icon.ico                generated, sizes 16..256
    environment/home-atmosphere.png  environment-only layer
  web/
    tokens.css                  design system — single source of truth
    app-shell.css               sidebar / topbar / rail / command bar / lamp / gem
    home.html  home.css  home.js    locked Home
    ops.css  page-shell.js  pages.js operational pages
    companion.js                CompanionRenderer + StaticCompanionRenderer
    pages/*.html                chat, missions, agents, computer, skills,
                                devices, memory, knowledge, media, forecast,
                                security, settings
  electron/
    main.js  preload.js  package.json   desktop shell + icon + packaging
  tests/frontend/home.test.js   deterministic tests (node:test)
  tests/visual/capture.py       real Chromium screenshots
  tests/visual/compare.py       geometry regression vs the approved reference
  tests/visual/verify-icon.js   icon size/format verification
  screenshots/                  generated artifacts (22 shots)
```

---

## 3. Layers

```
┌──────────────────────────────────────────────────────────┐
│  DOM / CSS components   (real, interactive, responsive)   │
├──────────────────────────────────────────────────────────┤
│  Environment layer      decorative only, pointer-events   │
│                         none; NO baked UI                 │
├──────────────────────────────────────────────────────────┤
│  CompanionLayer         presence, reflects real events     │
├──────────────────────────────────────────────────────────┤
│  View state             cache + display only, never truth │
├──────────────────────────────────────────────────────────┤
│  HTTP + SSE             /api/* and /api/events            │
└──────────────────────────────────────────────────────────┘
                              ↓
                    GENIE daemon (authority)
```

**Anti-cheat rule.** The reference screenshot is never rendered as one flat
background with invisible hit targets. Navigation, cards, status values,
buttons, date/time and the command bar are real DOM. The only derived image
layer is `environment/home-atmosphere.png`, cropped from the owner reference
with the sidebar, rails, top bar and command bar **removed** — it contains no
interactive content.

---

## 4. Shell

`page-shell.js` injects the sidebar and top bar into every operational page
from a single `NAV` table, so navigation cannot drift between pages. Home ships
its own markup (it needs the lamp, gem and companion stage).

**Responsive collapse order:**

1. right rail hidden ≤ 1180px
2. sidebar collapses to icons ≤ 900px
3. centre stays primary; the command bar is always usable

The desktop is never uniformly scaled down.

---

## 5. Data flow

```
typed input  ─┐
              ├→ POST /api/chat ─→ NEDLE2 → Mission → agents/runtime
voice (STT) ─┘                     → Model Gateway → tools → verify → reply
```

One brain. Voice and text enter the **same** request path. The companion is
not an intelligence.

Events (`/api/events`, SSE) carry `VOICE_*`, `MISSION_*`, `AGENT_*`,
`DEVICE_*`, `PROVIDER_*`, `PERMISSION_*` and the new `VOICE_AMPLITUDE`.

**Streaming:** the backend currently returns a complete reply, so the UI does
**not** fake token-by-token streaming. Mission/tool/agent activity is surfaced
from real events. Finer streaming would require a backend extension
(chunked reply emission on `/api/chat`).

---

## 6. Quality modes

| Mode | Environment | Particles | Blur | Companion |
|---|---|---|---|---|
| LOW | static | none | reduced | minimal transform |
| BALANCED | default | subtle | normal | breathing |
| HIGH | richer | more | full | richer ambience |

Unfocused/minimized reduces decorative animation; backend missions continue.
`prefers-reduced-motion` simplifies emergence, breathing, glow and particles
while keeping all functionality.
