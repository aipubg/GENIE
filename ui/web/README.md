# ui/web — classification: DIAGNOSTIC ONLY (not a desktop UI)

**Status: CURRENT.** There is exactly one owner-facing desktop UI:
**`Genie.Desktop`** (the Windows-native client, `ui/windows/Genie.Desktop`).

Everything under this directory is a **backend diagnostic surface** served over
HTTP. It exists for support and debugging. It is deliberately *not* a second
desktop UI, and it must not grow into one.

## What is kept and why

| File | Class | Why kept |
|---|---|---|
| `ops.html` / `ops.js` / `ops.css` | diagnostic | operator dashboard over live endpoints |
| `control.html` / `control.js` | diagnostic | control centre over live endpoints |
| `api.js` | diagnostic support | shared fetch helpers used by the two pages above |
| `styles.css`, `tokens.css` | diagnostic support | minimal styling for those pages |
| `index.html` | diagnostic entry | explicitly states this port is diagnostics and links to the two pages |

## What was removed (2026-09-20)

The browser-based *desktop* UI: `home`, `chat`, `missions`, `agents`,
`computer`, `skills`, `devices`, `memory`, `forecast`, `security`, `settings`,
`knowledge`, `media`, plus `compact`, the companion shell, and their
`app.js` / `pages.js` / `page-shell.js` / `home.js` / `companion.js`.

Reason: the native client reached parity on all of those surfaces. Keeping a
second owner-facing shell would mean the same state rendered in two places,
free to disagree. Removing it is what makes "one mature Windows application"
true rather than aspirational.

## Rule going forward

Do not add owner-facing screens here. New product surfaces belong in
`ui/windows/Genie.Desktop`, reading the backend API — the same API these
diagnostic pages read.
