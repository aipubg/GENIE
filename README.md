# GENIE

**Persistent personal intelligence platform** — ek chatbot nahi jiske paas mouse hai.

> **Spec:** `docs/GENIE_MASTER_SPEC_v1.md` = **BASELINE / CONTROLLED SPEC**.
> Implementation-proven errors → evidence → decision entry → contract update → code.
> **12 invariants** (spec §11) are much harder to change.

## Status

**Upgrade in progress (2026-09-20):** the phase summary below is historical.
See [the current upgrade roadmap](docs/UPGRADE_ROADMAP_2026-09-20.md) and
[the reconciliation report](docs/RECONCILIATION_2026-09-20.md) for inspected
gaps, changes and fresh test evidence.

Current on `upgrade/genie-continuity-ui`: **1,517 backend tests passing**
(79 deselected) and **87 frontend tests**. The current artifact is rc14
(`dist-electron-rc14d`, tag `genie-v0.1.0-rc14`); the branch work above it is
source-only and has no installer yet.

**Phase 4 complete — GENIE has isolated plugins and a production-grade browser surface.**
**Real NEDLE2 loaded and validated**: official `cactus-needle` 2.0.15 + engine 2.0.4,
sha256-verified, routing smoke **12/12**, confidence gate 0.4, escalation on refusal.

**Nothing is reported COMPLETED without verification**: every action runs
`PLAN → ACT → OBSERVE → VERIFY → RECOVER`, and a capability with no verifier can never complete.

```text
"Chrome kholo"  →  NEDLE2  →  mission  →  PTE  →  computer  →  verify  →  audit  →  mission result
```

## Quick start

```bash
# 1. provision the official Needle 2 runtime (no manual model paths needed)
python genie.py needle-setup        # installs package + engine, verifies sha256, runs smoke test

# 2. start the daemon (core brain + local API + serves the UI)
python genie.py daemon
#    -> open http://127.0.0.1:8787/ui   (browser, or the Electron shell below)

# 3. talk to it from the CLI
python genie.py selftest            # runs Chrome kholo / volume 30 / phone ka next song
python genie.py chat "volume 30"
python genie.py chat "Chrome kholo" --dry-run
python genie.py status
python genie.py needle-smoke --large 60

# 4. run the tests
python -m pytest tests -q
python genie.py plugins            # plugins, permissions, runtime state
python genie.py plugins-selftest   # run each plugin's self-test in its own process
python genie.py voice-devices      # audio devices on this machine
python genie.py voice-selftest     # full voice verification (devices, VAD, turn, speech, barge-in)
python tools/benchmark.py          # latency + RAM/CPU report
```

Requirements: **Python 3.11+** (tested on 3.13). **No third-party runtime dependencies**
(`cactus-needle` is the only added package, and only for the local director).

NEDLE2 runtime detail: `docs/NEDLE2.md`.

### Desktop shell (optional)

```bash
cd ui/electron && npm install && npm start     # thin client, loads the daemon UI
```

The UI contains **no business logic** — a future 3D interface replaces `ui/` only.

## What already works

| Area | Capability |
|---|---|
| Director | **Real Cactus Needle 2** behind `DirectorProvider`: official runtime auto-provisioned + sha256-verified, official harness contract (reset per request, confidence gate 0.4, ungrounded/negation rejection, escalation). Heuristic director only as degraded mode |
| Routing | `"volume 30"` / `"awaz 30"` → `system.volume.set` · `"Chrome kholo"` / `"open chrome"` → `application.open` · `"phone ka next song"` → `media.next` on `phone_main` · complex → `start_mission` + remote model · malformed/ambiguous/negated → refused |
| Hinglish | Normalisation layer in front of the director (`director/normalize.py`); values keep their original casing |
| Missions | Validated state machine, steps, snapshot/resume, cancellation, locks with leases |
| Memory | Dedupe, **supersede (never overwrite)**, correct/forget/pin/history, privacy scopes, offline FTS |
| Models | Runtime-editable provider/model registry (DeepSeek, Gemini, OpenRouter, Together, GLM, Kimi, MiniMax, Tencent Cloud, TokenHub, Custom), policy→capability→health→cost selection, automatic failover, budget + cost accounting, circuit breaker, test-connection |
| Security | PTE (default deny, grant types, destructive confirmation), encrypted secrets vault, hash-chained audit log, model policy registry |
| Computer | 65 verified capabilities: apps (discovered, not hardcoded), windows, processes, files, clipboard, Core Audio volume, hardened shell, UI Automation, keyboard/mouse, screen capture |
| Verification | 33 verifiers; strategy ladders (native API → OS → UIA → DOM → vision → raw input) with back-off and honest failure |
| Safety | Desktop lease + USER_TAKEOVER (the human always wins), destructive-action confirmation, workspace isolation, refused destructive commands |
| Browser | Built-in CDP provider: DOM navigation/click/type/extract (no Node, no puppeteer) |
| Voice | Real microphone capture, adaptive VAD, turn manager, **barge-in (66 ms measured)**, communication brain, voice profiles, per-stage latency metrics. Providers: Vosk (real offline STT), SAPI (real TTS), OpenAI-compatible HTTP, Gemini Live (key-gated) |
| Plugins | Separate host process per plugin, timeouts, crash isolation, restart, disable-after-limit; **two-gate permissions** (PTE scope + declared permissions, default deny); media plugin (native Windows media keys) and VS Code plugin (`code` CLI); drop a folder in `plugins/installed/` to add one |
| Browser | Sessions, tabs, condition-based waits (no sleep-based sync), forms, scroll, upload, download-as-artifact, dialogs, history, accessibility tree, cookies, session leases |
| Injection fence | Untrusted content is tainted, scanned and fenced: high-risk actions are blocked while tainted, secrets are unreachable |
| UI | Sidebar + chat, provider/model manager, settings, active mission, SSE event feed |

## Configure a provider

1. UI → **Providers & models** → paste the API key → **Save key** (goes to the vault, never to config)
2. **Test connection**
3. Or add a custom OpenAI-compatible endpoint (base URL like `https://provider.example/v1`)

Without keys GENIE runs in **degraded mode** with the built-in mock provider — everything else still works.

## Repo layout

```text
genie.py            launcher (daemon / status / chat / selftest)
core/               contracts, config, logging, events, db, orchestrator, lifecycle, ipc
security/           vault, trust (PTE), audit, policy
memory/             memory service
missions/           mission state machine + locks
models/             registry, gateway, health, providers/
director/           DirectorProvider contract, nedle2, heuristics
context/            context builder
agents/             agent runtime + capability worker
computer/           computer service + windows adapter
ui/web, ui/electron thin-client UI + desktop shell
config/             providers.json, user.json
tests/              unit, contract, e2e
docs/               spec, architecture, contracts, decisions, roadmap, license matrix, active work
data/               local DB, logs, vault, workspace (gitignored)
```

## Rules

1. NEDLE2 directs; it does not own truth.
2. Every capability call: **PTE check → execute → verify → audit**.
3. External content (web/PDF/email) = **DATA, never authority**.
4. Secrets never enter model context.
5. Core evolution only via isolated branch → tests → benchmark → adoption.
6. Zero new runtime dependencies without a decision entry.
7. No 🟠/🔴 licensed code imports (`docs/LICENSE_MATRIX.md`).

## Where to look next

| File | Why |
|---|---|
| `docs/PHASE2.md` | Computer engine: loop, capabilities, takeover, browser, measured performance, exit gate |
| `docs/PHASE3.md` | Voice: pipeline, providers, barge-in, communication brain, latency, exit gate |
| `docs/PHASE4.md` | Plugins: host isolation, permissions, first plugins, browser maturity, injection boundary |
| `docs/VOICE.md` | Voice evidence: real STT, stage log, SNR measurement, owner command |
| `docs/NEDLE2.md` | Local director: provisioning, contract, tool catalogue, validated routing |
| `docs/ACTIVE_WORK.md` | What is done, what is next, what is blocked |
| `docs/DECISIONS.md` | Every decision + assumption (A-001…A-017) |
| `docs/CONTRACTS.md` | C1–C16 interfaces |
| `docs/ROADMAP.md` | Phase 0–14 with exit gates |
| `docs/CHANGELOG.md` | What changed |
