# GENIE — Project Structure

A short map of where things live. For deep design, see `ARCHITECTURE.md` and
`CONTRACTS.md`. This file is the "where do I put this?" reference, not the theory.

## The one rule

> If you open `E:\G3\GENIE`, you should quickly know what is *trusted kernel*,
> what is a *product capability*, what is an *external integration*, what is
> *vendored upstream*, what is *UI*, and what is *generated*.

## Top-level tree

```text
GENIE/
├── core/             # Trusted kernel: config, events, audit, hardening,
│                     #   backup, update, lifecycle. No UI, no optional features.
│
├── memory/   missions/   agents/    director/   context/
├── computer/ browser/    voice/     skills/     teaching/
├── devices/  perception/ security/  forecast/   usermodel/
├── experience/  proactive/
│                     # Product capabilities. One folder = one job.
│
├── models/           # Model registry + provider adapters — CODE only.
│   └── providers/    #   Provider adapters (OpenAI-compatible, mock, …).
│
├── channels/  plugins/  config/  specs/  evaluation/
├── observability/  android/  tools/
│
├── integrations/     # GENIE adapters for external systems: Prime RLM,
│                     #   Strix, Scrapling, MiroFish, MCP runtimes.
├── vendor/           # Preserved upstream code (prime_rlm, strix-audit).
│
├── ui/
│   ├── web/          #   helpers.js (rendering helpers), pages.js (binding),
│   │                 #     *.html shells, ops.css, page-shell.js.
│   ├── electron/     #   Desktop shell — THIS is the current installer source
│   │                 #     (package.json "build" + build/installer.nsh, NSIS).
│   ├── tests/        #   Frontend + API/UI contract + visual gate.
│   └── assets/
│
├── tests/            # unit/ contract/ e2e/ external_optional/ owner/ fixtures/
├── scripts/          # Operational tooling (acceptance, icon, build, probes).
├── docs/             # Human documentation.
│
├── installer/        # LEGACY — old Inno Setup spec (GENIE.iss), unverified.
│                     #   Superseded. NOT the current packaging path.
│
├── data/             # Private runtime state — NOT source.
├── artifacts/        # Generated verification / release evidence.
├── dist-electron*/   # Generated desktop builds.
│
└── genie.py          # Entry point: `python genie.py daemon`
```

## The three "not source" buckets

- **`data/`** — mutable private state: runtime databases, vault/state,
  mission and workspace data, voice and browser runtime profiles, downloaded
  model weights, logs. Normally ignored/private, except intentionally tracked
  fixtures or seed data.
- **`artifacts/`** — evidence *about* builds: acceptance JSON/MD, release
  manifests, release matrices, screenshots, diagnostic reports. A live
  database, the encrypted vault, the user workspace, a browser profile, model
  weights or persistent user files are **not** artifacts — they belong in
  `data/`.
- **`dist-electron*/`** — actual build products (electron-builder output).
  Ignored, safe to regenerate, never edited.

## What each layer owns

- **`core/`** — trusted kernel and system-wide authority. Must not import
  presentation code. Keep it small; do not move every feature in here.
- **Domain folders** — first-party product capabilities. Major domains expose a
  small, obvious public service or entry point; verified examples are
  `MemoryService`, `MissionService`, `DeviceService`, `ForecastService`,
  `ComputerService`, `BrowserService`, `SkillService`, `VoiceService`,
  `SecurityFindingService`, and `models.Gateway` / `ModelRegistry`.
  Browser control (CDP, target resolution, session control) is first-party
  capability in `browser/`, not an external integration.
- **`integrations/`** — GENIE-written adapters for external systems, always
  reached through a GENIE contract. Contrast with **`vendor/`**, which is
  preserved *upstream* code with provenance. Do not mix the two meanings.
- **`ui/`** — presentation only. Every value shown comes from a real backend
  endpoint; missing data renders as an honest empty state, never invented
  numbers.
- **`tests/`** — location matches what is tested. `real_machine` is a **pytest
  marker** (serial real-desktop tests), not a directory.

## Simple rules

| Folder | Ask yourself |
|---|---|
| `core/` | Does the whole GENIE system depend on this authority/infrastructure? |
| domain folder | Is this a first-party product capability? |
| `integrations/` | Does GENIE talk to an external runtime/system through this? |
| `vendor/` | Is this preserved upstream code? |
| `ui/` | Is this presentation? |
| `data/` | Is this mutable private/runtime state? |
| `artifacts/` | Is this generated evidence? |
| `dist-electron*/` | Is this a generated build? |

## Notes

- There is intentionally **no separate Knowledge module** — the Knowledge page
  renders from the skills/corpus service.
- `models/` holds registry, gateway, health and provider adapters — source code.
  Downloaded model weights are runtime data and live under `data/`.
- Phase numbers belong in `docs/` and `ROADMAP.md`, never in folder names.
- The current desktop installer is electron-builder + NSIS, configured in
  `ui/electron/package.json` with `ui/electron/build/installer.nsh`. The root
  `installer/` directory is legacy material only.
- Structural cleanup (for example retiring the legacy `installer/`) is deferred
  until **after v0.1**. Nothing is moved during release hardening.
