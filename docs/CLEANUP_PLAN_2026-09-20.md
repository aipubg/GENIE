# Build artefact cleanup — classification (2026-09-20)

Spec §22-§23: **classify before deleting.** Nothing here has been removed.
This document exists so the decision can be made deliberately, because one of
these directories is the current release artifact and another is required to
build the next one.

Total untracked build output: **4.66 GB** across 16 directories.

## Classification

| Directory | Size | Files | Class | Recommendation |
|---|---|---|---|---|
| `dist-electron-rc14d` | 741.9 MB | 3,466 | **CURRENT RELEASE** | **KEEP** — tagged `genie-v0.1.0-rc14` |
| `backend-dist/backend-runtime` | 203.0 MB | 3,409 | **BUILD INPUT** | **KEEP** — the embedded Python runtime copied into the installer. Regenerable via `scripts/build_backend_runtime.py`, but expensive to rebuild |
| `dist-electron-rc14` | 741.9 MB | 3,468 | superseded candidate | delete after confirmation |
| `dist-electron-rc14b` | 741.4 MB | 3,461 | superseded candidate | delete after confirmation |
| `dist-electron-rc14c` | 741.9 MB | 3,466 | superseded candidate | delete after confirmation |
| `dist-electron-rc13g` | 445.8 MB | 77 | superseded generation | delete after confirmation |
| `dist-electron-rc12g` | 445.8 MB | 77 | superseded generation | delete after confirmation |
| `.build` | 367.6 MB | 11,069 | scratch (frozen venv + embed Python) | delete after confirmation |
| `backend-dist/GENIEBackend` | 235.3 MB | 844 | **superseded** PyInstaller output | delete after confirmation |
| `dist-electron-check` | 9 KB | 1 | husk (empty `win-unpacked`) | delete after confirmation |
| `dist-electron-rc12` | 9 KB | 1 | husk | delete after confirmation |
| `dist-electron-rc12b` … `rc12f` | 9 KB each | 1 each | husk ×6 | delete after confirmation |

Deleting everything except `rc14d` and `backend-runtime` reclaims **≈4.2 GB**.

## Why this is not being done automatically

1. **`dist-electron-rc14d` is the release.** The tag `genie-v0.1.0-rc14`
   points at `d92f0db`, and `dist-electron-rc14d/GENIE Setup 0.1.0.exe`
   (136,976,883 bytes, SHA-256
   `4f29328fdb5585d1fe65d78c159a41d74f7b92672a1777ea3b4c8418aa11f1c8`) is the
   artifact that identity describes. Removing it breaks the release.
2. **rc14, rc14b, rc14c are not obviously identical to rc14d.** Each has its own
   installer. They are superseded by the tag, but until the owner confirms they
   are not the build they actually tested, deleting them destroys evidence.
3. **The safe-delete guard blocks bulk removal anyway**, so this needs a
   deliberate, batched approach rather than a scripted `rmtree`.

## Proposed order (on approval)

1. Husks (`dist-electron-check`, `rc12`, `rc12b`–`rc12f`) — 7 directories, ~65 KB.
2. `.build` — scratch, regenerable.
3. `backend-dist/GENIEBackend` — superseded by `backend-runtime`.
4. `dist-electron-rc12g`, `rc13g` — two generations back.
5. `dist-electron-rc14`, `rc14b`, `rc14c` — superseded within the current line.

`rc14d` and `backend-runtime` are never touched.

A `.gitignore` entry for these build outputs is also worth adding, so they stop
showing up as untracked noise in every `git status`.
