# GENIE — REPO MAP

Actual tree as implemented. Status: 🟥 not started · 🟨 in progress · 🟩 working + tested · ⬜ n/a

> Per D-029, folders are created when their phase starts — no empty micro-modules.
> A module = one clear responsibility, one clear contract, minimum useful complexity.

| Path | Layer | Purpose | Status |
|---|---|---|---|
| `genie.py` | — | Launcher CLI: `daemon` / `status` / `chat` / `selftest` | 🟩 |
| `core/contracts.py` | — | Shared types, mission transitions, event names | 🟩 |
| `core/config.py` | — | JSON config, env overrides, data dirs | 🟩 |
| `core/logging_setup.py` | — | Structured logs + trace binding | 🟩 |
| `core/events.py` | — | Event bus (wildcards, DLQ, ring buffer) | 🟩 |
| `core/db.py` | — | SQLite/WAL + migrations + all v1 tables | 🟩 |
| `core/orchestrator.py` | 2 | Turn loop (director → mission → capability → verify) | 🟩 |
| `core/lifecycle.py` | — | Daemon boot/shutdown, recovery, status | 🟩 |
| `core/ipc/server.py` | — | REST + SSE local API, serves the UI bundle | 🟩 |
| `security/vault.py` | 1 | Secrets vault (DPAPI / scrypt fallback) | 🟩 |
| `security/trust.py` | 1 | PTE — default deny, grants, destructive confirmation | 🟩 |
| `security/audit.py` | 1/8 | Hash-chained append-only audit log | 🟩 |
| `security/policy.py` | 1 | Model Policy Registry (data-class gating) | 🟩 |
| `memory/service.py` | 3 | Memory truth owner (dedupe, supersede, forget, FTS) | 🟩 |
| `missions/service.py` | 2 | Mission state machine + locks | 🟩 |
| `models/registry.py` | 4 | Runtime-editable provider/model registry | 🟩 |
| `models/gateway.py` | 4 | Selection, failover, budget, cost accounting | 🟩 |
| `models/health.py` | 4 | Circuit breaker + provider state | 🟩 |
| `models/providers/` | 4 | `openai_chat`, `openai_responses`, `mock` adapters | 🟩 |
| `director/base.py` | 2 | `DirectorProvider` contract + decision schema | 🟩 |
| `director/nedle2.py` | 2 | **Real Cactus Needle 2 director** (official harness contract, confidence gate, escalation) | 🟩 validated |
| `director/needle_runtime.py` | 2 | Runtime provisioning/verification (official package + engine, sha256, GENIE-managed dir) | 🟩 |
| `director/tools.py` | 2 | GENIE tool catalogue for NEDLE2 (single source, `@needle.tool`) | 🟩 |
| `director/normalize.py` | 2 | Hinglish/Hindi command normalisation in front of the director | 🟩 |
| `director/heuristics.py` | 2 | Deterministic fallback (degraded mode only) | 🟩 |
| `context/builder.py` | 3/7 | Budgeted context packet | 🟩 |
| `agents/runtime.py` | 4 | Agent lifecycle, budgets, cancel + capability worker | 🟩 |
| `computer/service.py` | 5 | PTE gate + audit + verified execution (65 capabilities) | 🟩 |
| `computer/windows_api.py` | 5 | Win32 layer: windows, monitors, SendInput, clipboard, GDI capture | 🟩 |
| `computer/state.py` | 5 | ComputerState snapshots (the OBSERVE half) | 🟩 |
| `computer/apps.py` | 5 | Installed-app discovery + launch-method cache | 🟩 |
| `computer/audio.py` | 5 | Core Audio volume read/set/mute | 🟩 |
| `computer/uia.py` | 5 | UI Automation (optional provider) — semantic targeting | 🟩 |
| `computer/input.py` | 5 | Keyboard/mouse + USER_TAKEOVER detection | 🟩 |
| `computer/locks.py` | 5 | Desktop lease + takeover handling | 🟩 |
| `computer/files.py` | 5 | Workspace-first filesystem, atomic writes | 🟩 |
| `computer/shell.py` | 5 | Hardened shell (allowlist + policy + timeouts) | 🟩 |
| `computer/workspace.py` | 5 | Workspace, isolation detection, resource profile | 🟩 |
| `computer/planner.py` | 5 | Strategy chains (automation priority) | 🟩 |
| `computer/verifier.py` | 5 | 33 verifiers — nothing completes unverified | 🟩 |
| `computer/executor.py` | 5 | PLAN→ACT→OBSERVE→VERIFY→RECOVER loop | 🟩 |
| `computer/events.py` | 5 | Polling event sources → bus | 🟩 |
| `browser/cdp.py` | 5 | Stdlib WebSocket + CDP client | 🟩 |
| `browser/service.py` | 5 | BrowserProvider: DOM-first actions, render-verified navigation | 🟩 |
| `tools/benchmark.py` | 8 | Latency + RAM/CPU benchmark | 🟩 |
| `voice/devices.py` | 7 | Audio device discovery | 🟩 |
| `voice/vad.py` | 7 | Adaptive VAD (speech start/end) | 🟩 |
| `voice/providers.py` | 7 | Five voice provider contracts + implementations (SAPI, HTTP, Gemini Live, dev) | 🟩 |
| `voice/turn_manager.py` | 7 | Turn states + barge-in transitions | 🟩 |
| `voice/communication.py` | 7 | Communication Brain (behaviour + spoken shaping) | 🟩 |
| `voice/profiles.py` | 7 | Voice profiles + consent gate | 🟩 |
| `voice/metrics.py` | 7 | Per-stage latency metrics | 🟩 |
| `voice/pipeline.py` | 7 | Capture→VAD→STT→turn→brain→TTS wiring | 🟩 |
| `voice/service.py` | 7 | Voice facade, self-test, provider selection | 🟩 |
| `voice/audio.py` | 7 | Audio preprocessing (DC removal, high-pass, bounded AGC) | 🟩 |
| `plugins/sdk.py` | 5 | Plugin manifest, capability specs, host protocol, adapter base | 🟩 |
| `plugins/host.py` | 5 | Plugin host process (one per plugin) | 🟩 |
| `plugins/client.py` | 5 | Supervision: timeouts, crash counter, restart, disable | 🟩 |
| `plugins/registry.py` | 5 | Discovery, install/uninstall, enable/disable, permission grants | 🟩 |
| `plugins/service.py` | 5 | Capability routing, permission enforcement, audit | 🟩 |
| `plugins/installed/` | 5 | media · vscode · crash/timeout fixtures | 🟩 |
| `security/injection_guard.py` | 1 | Untrusted-content taint, scan and action fencing | 🟩 |
| `ui/web/` | 7 | Basic ChatGPT-like UI (thin client) | 🟩 |
| `ui/electron/` | 7 | Electron shell, no business logic | 🟩 |
| `config/providers.json` | 4 | Predefined provider templates | 🟩 |
| `tests/unit`, `tests/contract`, `tests/e2e` | 8 | 242 tests (real machine: computer, voice, plugins, browser, golden mission, routing) | 🟩 |
| `docs/` | — | Spec, architecture, contracts, decisions, roadmap, license matrix | 🟩 |
| `data/` | — | Local DB, logs, vault, workspace (**gitignored**) | 🟩 |
| `ops/` | 8 | Observability/audit dashboards, backup, updater, scheduler, sync | 🟥 Phase 8/14 |
| `browser/` | 5 | Browser service + pinchtab adapter | 🟥 Phase 2 |
| `devices/` | 6 | Device mesh, protocol, Android/Pi adapters | 🟥 Phase 6 |
| `perception/` | 6 | Screen/camera/audio, presence, fusion | 🟥 Phase 8 |
| `skills/` | 4 | Skill registry/runtime/learning | 🟥 Phase 5 |
| `teaching/` | 4 | Teaching recorder, generalizer | 🟥 Phase 5 |
| `plugins/` | 5 | Plugin host, sandbox, SDK | 🟥 Phase 4 |
| `evolution/` | 8 | Proposals, isolated workspace, adoption | 🟥 Phase 12 |
| `integrations/` | 4/5/6 | n8n, comfyui, omnivoice, kronos, mirofish (isolated) | 🟥 Phase 11 |
| `third_party/` | — | Provenance-tracked vendor code (never 🟠/🔴) | ⬜ |

## Rules

1. Naya top-level folder sirf decision/RFC se (D-029).
2. `security/` aur `ops/` ko kisi aur module mein merge karna mana hai (cross-cutting).
3. 🟠/🔴 licensed code kabhi import nahi — alag repo + process boundary (`docs/LICENSE_MATRIX.md`).
4. Zero new third-party runtime dependencies without a decision entry (D-030).
5. Module complete tab jab `tests/` mein uske liye green tests hon.
