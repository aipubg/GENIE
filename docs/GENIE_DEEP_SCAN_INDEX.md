# GENIE Deep Scan Index

Date: 2026-09-22
Checkout: `E:\G3\GENIE`
Branch: `upgrade/genie-continuity-ui`
HEAD during scan: `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`

This is an editing orientation index. It separates source of truth from generated
or owner-runtime state so later changes do not accidentally modify private data,
packaged copies, release evidence, or browser/runtime caches.

## Source Of Truth

- Primary repo root: `E:\G3\GENIE`
- `E:\` and `E:\G3` are container/workspace parents, not the GENIE git root.
- Current source authority is the dirty working tree in `E:\G3\GENIE`, not the
  historical rc artifacts.
- Packaged backend runtime is under `backend-dist/backend-runtime`; it is a
  copied runtime/build input, not the primary edit surface.
- Runtime/private state is under `data/` in dev mode and `%LOCALAPPDATA%\GENIE`
  in packaged mode.

## Current Git State

- Tracked files: 902.
- Current worktree is already dirty.
- Many source changes were present before this scan across backend, WPF UI,
  missions, providers, scripts, and tests.
- Untracked source-like files include:
  - `missions/control.py`
  - `missions/runner.py`
  - `missions/scheduler.py`
  - `models/eligibility.py`
  - `models/provider_http.py`
  - multiple `scripts/*` diagnostics and rc26 feedback checks
  - `ui/windows/Genie.Desktop/Services/ThemeService.cs`
  - `ui/windows/Genie.Desktop/Themes/Theme.Dark.xaml`
  - `ui/windows/Genie.Desktop/Themes/Theme.Light.xaml`
  - `ui/windows/Genie.Desktop/ViewModels/UiDiagnostics.cs`
- Do not revert or normalize line endings casually; git reports LF to CRLF
  warnings for several touched files.

## Size And Surface

Source-like size excluding generated/runtime directories:

| Area | Files | Lines |
|---|---:|---:|
| Python | 606 | 155,285 |
| Markdown | 488 | 116,280 |
| JSON | 415 | 15,554 |
| JavaScript | 32 | 9,647 |
| C# | 41 | 7,497 |
| XAML | 17 | 3,682 |
| CSS | 8 | 1,150 |
| HTML | 14 | 376 |
| Installer scripts | 2 | 327 |
| PowerShell | 1 | 86 |
| Batch/CMD | 2 | 55 |

Generated/runtime-heavy directories:

- `artifacts/`: mostly pytest temp DBs, encrypted temp files, logs, release
  evidence, UI screenshots, owner-acceptance records.
- `backend-dist/`: packaged backend runtime and embeddable Python.
- `data/workspace/browser-profile/`: Chromium profile/cache/download state.
- `data/`: local dev runtime state, DB, vault, provider user config, screenshots,
  voice files, workspace, and tracked persona corpus.

## Runtime And Private Data Boundary

Observed private/runtime shape without dumping raw sensitive values:

- `data/genie.db`: live dev DB with tables for audit, missions, mission steps,
  provider state, voice metrics, grants, devices, plugins, skills, memory,
  agents, proactive, perception, sync, knowledge, media/artifacts, specs.
- `data/plugin_dbg.db`: plugin/debug DB.
- `data/providers.user.json`: present; keys are `providers`, `roles`, `version`.
- `data/vault.enc`: present; do not inspect or print decrypted content.
- `data/running.flag`: present.
- `data/personas/`: 295 tracked persona markdown files; product data, not owner
  private runtime state.
- `data/models/`, `data/voice/`, screenshots, browser profile, and workspace are
  runtime/private or generated and should not be edited as source.

Edit rule: source changes may alter schemas, migrations, or contracts, but should
not mutate live owner data unless an explicit migration/test requires it and the
target is a throwaway DB.

## Packaged Runtime

- `scripts/sync_backend_runtime.py --check` passed.
- Runtime manifest source commit: `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`.
- Manifest file count: 238 copied app files.
- Packaged runtime path: `backend-dist/backend-runtime/app`.
- Packaged Python path: `backend-dist/backend-runtime/python`.
- Treat this as generated/build input. Normal edits belong in source modules,
  followed by sync/check when packaging behavior matters.

## Verification Already Run

- WPF Release build:
  - `E:\G3\.dotnet\dotnet.exe build ui/windows/Genie.Desktop/Genie.Desktop.csproj --configuration Release --nologo`
  - Result: succeeded, 0 warnings, 0 errors.
- Focused backend tests:
  - `python -m pytest tests\unit\test_provider_failures.py tests\unit\test_security.py tests\unit\test_director.py tests\e2e\test_wrong_action_baseline.py -q`
  - Result: 41 passed, 1 deselected.
- Runtime sync check:
  - `python scripts\sync_backend_runtime.py --check`
  - Result: packaged runtime matches source at current commit.
- Test collection:
  - `python -m pytest --collect-only -q`
  - Result: 1553/1632 tests collected, 79 deselected.
- Duplication audit:
  - `python scripts\audit_duplication.py`
  - Result: no duplicates found; accepted director hot-swap reassignment only.

## Test Policy

- Default pytest run is deterministic and excludes:
  - `real_machine`
  - `hardware_optional`
  - `owner_acceptance`
- `tests/conftest.py` wires a full throwaway GENIE stack on a temp DB.
- `real_machine` tests acquire a cross-process lock so desktop state is not
  mutated concurrently.
- Owner acceptance tests are pending by design and should not be counted as
  automated pass.
- Build outputs and packaged third-party tests are excluded by `pytest.ini`.

## Backend Map

Primary boot and truth flow:

- `core/lifecycle.py`: daemon construction and boot sequence. It wires config,
  DB, audit, vault, trust, memory, missions, registry, health, gateway,
  director, plugins, computer, skills, devices, perception, proactive, security,
  agents, knowledge, experience, browser/runtime, scheduler/runner, voice,
  crash recovery, and IPC.
- `core/orchestrator.py`: chat/action/mission decision layer. It gates simple
  actions, missions, task execution, provider reasoning, streaming, and honest
  failure wording.
- `core/ipc/server.py`: HTTP/SSE API surface. It is hand-routed with
  `BaseHTTPRequestHandler`, not framework decorators.
- `core/db.py`: migrations and schema authority.
- `core/paths.py`: dev vs packaged path authority.
- `core/hardening.py`, `core/backup.py`, `core/updater.py`, `core/release_fetch.py`:
  safe mode, backup, update, release fetch support.

Important API families in `core/ipc/server.py`:

- status/health/UI: `/health`, `/ui`, `/api/status`
- chat: `/api/chat`, `/api/chat/stream`
- missions: `/api/missions`, `/api/missions/create`, `/api/missions/{id}/*`
- providers/models: `/api/providers`, `/api/providers/catalog`,
  `/api/providers/test`, `/api/providers/discover`, `/api/models/roles`
- agents: `/api/agents`, `/api/agents/teams`, `/api/agents/missions`,
  `/api/agents/continuation/{mission}`
- computer/browser/isolation: `/api/metrics/actions`, `/api/isolation`,
  `/api/browser/status`, `/api/browser/frame`
- voice: `/api/voice`, `/api/voice/devices`, `/api/voice/start`,
  `/api/voice/stop`, `/api/voice/say`, `/api/voice/barge-in`, self-test and
  profiles endpoints
- skills/teaching: `/api/skills/*`, `/api/teaching/*`
- devices/sync/peripherals: `/api/devices/*`, `/api/devices/sync*`,
  `/api/peripherals*`
- proactive/perception: `/api/proactive*`, `/api/perception*`
- knowledge/media/experience/competition: `/api/knowledge*`, `/api/media`,
  `/api/experience*`, `/api/competition*`
- ops/security/forecast/control center: `/api/ops/*`,
  `/api/security/findings`, `/api/forecast/calibration`, `/api/control-center`

## WPF Frontend Map

- Project: `ui/windows/Genie.Desktop/Genie.Desktop.csproj`.
- Target: `net8.0-windows`, WPF, self-contained `win-x64`.
- `App.xaml.cs`: startup, crash logging, single instance, shutdown relay,
  recoverable dispatcher exceptions.
- `MainWindow.xaml`: shell and navigation templates for Home, Chat, Missions,
  Agents, Computer, Competition, Experience, Knowledge, Media, Settings, and
  generic owner surfaces.
- `Services/BackendLifecycle.cs`: single-instance events, backend startup,
  current-build backend detection, exact PID shutdown of foreign backend.
- `Services/GenieBackend.cs`: backend process ownership and stop logic.
- `Services/BackendClient.cs`: canonical typed HTTP/SSE transport used by
  ViewModels.
- `Services/ThemeService.cs` plus theme XAML: current untracked theme support.
- ViewModels own page state and commands; views are thin WPF bindings plus
  minor UI behaviors such as safe auto-scroll.

UI/backend contract note:

- `BackendClient.cs` calls real backend endpoints directly.
- Keep API changes backward-compatible or update both `core/ipc/server.py` and
  the corresponding WPF ViewModel/client call.

## Model And Provider Map

- `config/providers.json`: predefined templates only; no secret values.
- `data/providers.user.json`: runtime owner/provider config; do not print
  secrets or mutate without explicit user action.
- `security/vault.py`: secret resolution; raw values should remain in-process.
- `models/registry.py`: provider/model/role registry and secret-ref presence.
- `models/provider_http.py`: OpenAI-compatible URL/auth/request normalization.
- `models/providers/openai_compat.py`: live provider/discovery/test behavior.
- `models/eligibility.py`: runtime provider readiness/cooldown/quota/auth state.
- `models/gateway.py`: candidate selection, capability/policy/health/eligibility,
  failover, streaming, and degraded mock behavior.
- `models/failures.py`: failure classification authority.

## Director And Intent Map

- `director/nedle2.py`: Cactus Needle 2 integration, smoke/availability, and
  visible heuristic fallback when unavailable.
- `director/heuristics.py`: degraded deterministic routing fallback.
- `director/tools.py`: tool catalogue and parameter canonicalization.
- `director/normalize.py`: Hinglish normalization.
- `director/semantic_guard.py`: refusal/ambiguity/destructive guard logic.

Needle routes intent; it does not own truth or claim completion.

## Mission And Agent Map

- `missions/service.py`: mission state machine, steps, snapshots, locks.
- `missions/planner.py`: task planning heuristics/model planning entry.
- `missions/runner.py`: high-level runner, idempotent step execution,
  verification, checkpoints, resume/finalize behavior.
- `missions/scheduler.py`: recurrence/scheduling and catch-up policy.
- `missions/control.py`: mission control facade.
- `agents/runtime.py`: capability worker and execution runtime.
- `agents/service.py`: agent mission/team facade.
- `agents/team.py`: DAG/team/mailbox/blackboard/budget flow.
- `agents/competition.py`: candidate contests and verifier scoring.
- `agents/receipts.py`: accepted/rejected/unverified evidence ledger.
- `experience/bank.py`: evidence-backed lessons, not transcript replay.

Edit rule: never make an action look completed unless execute and verify both
support it. Failed steps should remain visible and resumable.

## Computer, Browser, Devices, Voice

- `computer/service.py`: PTE, action metrics, capability registry, executor,
  verifier, audit.
- `computer/executor.py`: actual OS actions including process/app/window/file/
  shell/audio/UIA/input paths.
- `computer/verifier.py`: capability verification; no verifier means no verified
  completion.
- `computer/workspace.py`: workspace isolation and cleanup guard areas.
- `browser/service.py` and `browser/cdp.py`: CDP browser ownership, profile,
  tabs, forms, waits, screenshots/download/upload/dialog/accessibility.
- `browser/targets.py`: target resolution; working page beats placeholder.
- `devices/service.py`, `devices/registry.py`, `devices/sync.py`: pairing,
  trust, commands, queueing, sync/conflicts.
- `voice/service.py`, `voice/pipeline.py`, `voice/providers.py`: capture, VAD,
  STT/TTS, voice metrics, barge-in, profiles, honest degraded reasons.

## Security And Safety

- `security/trust.py`: PTE/default deny grants.
- `security/audit.py`: append-only audit.
- `security/vault.py`: DPAPI on Windows, documented fallback elsewhere.
- `security/policy.py`: model/data policy registry.
- `security/injection_guard.py`: untrusted content taint and action fencing.
- `security/skill_scanner.py`: skill-install static scan.
- Installer policy: per-user install under `%LOCALAPPDATA%\Programs\GENIE`;
  owner data under `%LOCALAPPDATA%\GENIE` survives uninstall.
- Installer/shutdown rule: ask GENIE to close itself; never force-kill by default.

## Scripts Map

Important script classes:

- Runtime/build: `build_backend_runtime.py`, `sync_backend_runtime.py`,
  `verify_build_match.py`, `packaged_runtime_smoke.py`.
- Installer/app lifecycle: `installer_preflight.py`,
  `verify_installer_upgrade.py`, `verify_full_exit_e2e.py`,
  `verify_win10_acceptance.py`, `verify_native_client.py`,
  `verify_single_instance.py`, `verify_shutdown_switch.py`.
- Provider/UI diagnostics: `acceptance_custom_provider.py`,
  `capture_pass4_provider.py`, `capture_provider_flow.py`,
  `diag_provider_state.py`, `verify_provider_e2e.py`.
- Responsiveness/chat: `chat_conversation.py`, `multi_turn_test.py`,
  `responsiveness_suite.py`, `hang_probe.py`.
- Release/docs/checks: `check_release_docs.py`, `golden_gate.py`,
  `audit_duplication.py`.
- Cleanup helpers: `cleanup_test_missions.py`; use only against intended test
  data or explicit `GENIE_DATA_DIR`.

Several scripts can launch/kill test processes. Read headers and flags before
running. `installer_preflight.py` is read-only by default and requires explicit
`--force-kill --yes` for destructive kill behavior.

## Known Drift And Risks

- README/ACTIVE_WORK/packaged acceptance docs contain historical rc14/rc15/rc13
  references and do not fully match the current source-only branch state.
- Current source HEAD is newer than several documentation claims.
- `backend-dist` matches current source by manifest, but release installers are
  historical and should not be treated as rebuilt from the latest dirty tree.
- Some abstract/base classes intentionally raise `NotImplementedError`; these
  are contracts, not necessarily unfinished product work.
- Degraded/fallback paths are explicit and generally visible in status or test
  wording.
- Mock provider exists for offline/test degraded mode and should not be mixed
  with real provider choices in owner-facing UI.
- `data/` and `artifacts/` include real or sensitive local state; keep scans
  structural unless the user explicitly asks for a specific artifact.

## Editing Checklist

Before editing:

1. Identify whether the target is source, generated runtime, test fixture,
   release artifact, or owner/private state.
2. Check existing dirty changes in the exact file; do not revert unrelated work.
3. If backend API changes, update WPF `BackendClient` and relevant ViewModels.
4. If provider/model behavior changes, update registry/gateway/provider tests.
5. If mission/action behavior changes, preserve verify-before-complete and
   idempotent resume behavior.
6. If installer/shutdown behavior changes, preserve owner data and no force-kill
   default.
7. If tests touch desktop/Chrome/mic/speaker/device, mark `real_machine` or
   appropriate optional marker.
8. If packaged runtime matters, run `scripts/sync_backend_runtime.py --check`
   or sync explicitly after source edits.

Recommended focused gates:

- Backend logic: relevant `tests/unit/*` and `tests/contract/*`.
- IPC/API shape: `tests/contract/test_ipc_api.py`,
  `tests/contract/test_provider_settings_contract.py`.
- Provider failover: `tests/unit/test_provider_failures.py` and provider E2E
  script/tests when needed.
- Missions/agents: mission, runner, and phase10 agent tests.
- UI build: WPF Release build.
- Runtime copy: `python scripts\sync_backend_runtime.py --check`.

## Scan Limit

This index is not a promise that every generated byte in `artifacts/`,
`backend-dist/python/site-packages`, browser cache, DB rows, or private vault
content was manually read. Those areas were classified structurally. The source
tree, contracts, boot flow, APIs, tests, scripts, runtime boundaries, and edit
risk surfaces were scanned deeply enough to guide later code changes safely.
