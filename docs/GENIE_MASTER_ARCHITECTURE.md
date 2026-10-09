# GENIE Master Architecture

Current audit date: 2026-09-22

This file is the current developer orientation map for GENIE. It is based on
the repository at `E:\G3\GENIE`, branch `upgrade/genie-continuity-ui`, HEAD
`6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`, plus the dirty working tree
present during this audit. Historical phase and release documents remain useful
context, but current source and current runtime checks are the authority.

## A. Current Repository Identity

- Root: `E:\G3\GENIE`
- Branch: `upgrade/genie-continuity-ui`
- HEAD: `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`
- Tags present: `genie-v0.1.0-rc16` through `genie-v0.1.0-rc26`
- Source size: 911 tracked/untracked source files under the GENIE checkout at audit time.
- Working tree: dirty. Modified backend, browser, model, mission, WPF and script files are present; untracked files include mission runner/control/scheduler, model eligibility/provider HTTP helpers, provider acceptance scripts, responsiveness scripts, and WPF theme service/resources.
- Native frontend: `ui/windows/Genie.Desktop`, WPF, `.NET 8`, `net8.0-windows`, `win-x64`, self-contained, `OutputType=WinExe`.
- Backend runtime: Python daemon launched from `backend-dist/backend-runtime/python/pythonw.exe app/backend_entry.py`.
- Runtime drift check: `python scripts/sync_backend_runtime.py --check` passed for 238 packaged backend files.
- Narrow verification performed: WPF Release build passed; focused pytest slice passed (`41 passed, 1 deselected`).

## B. Product Definition

GENIE is a persistent local-first personal AI operating layer. The GENIE identity
is not any one remote model. Models are interchangeable reasoning engines behind
the Model Gateway. The product authority is the Python daemon; the WPF app is
presentation and owner interaction.

GENIE's desired loop is:

```text
owner text/voice
  -> director intent/category
  -> memory/context
  -> model requirement
  -> model gateway/provider selection
  -> mission/action/agent/tool execution
  -> observation/verification/receipt
  -> persisted checkpoint
  -> experience learning
  -> truthful owner response
```

## C. Runtime Architecture

```text
Genie.Desktop.exe (WPF)
  - single instance, activation/shutdown events
  - starts/reuses one current-build backend
  - HTTP + SSE client only
  - no backend business authority

localhost HTTP/SSE on 127.0.0.1:8787
  - core/ipc/server.py

Python daemon
  - core/lifecycle.py bootstraps config, DB, audit, vault, trust, services
  - core/orchestrator.py owns chat turn routing
  - services own their domain truth

SQLite/WAL + vault + runtime files
  - data directory from core/paths.py
  - source checkout data in development
  - %LOCALAPPDATA%\GENIE\data in packaged mode
```

## D. Authority Map

- WPF frontend: presentation, navigation, owner input, status display, theme.
- `core/lifecycle.py`: daemon composition and boot/shutdown service graph.
- `core/ipc/server.py`: HTTP/SSE API surface; delegates to daemon services.
- `core/orchestrator.py`: turn-level authority for conversation/action/mission choice.
- `director/nedle2.py` and `director/heuristics.py`: local intent/task classification only.
- `models/registry.py`: provider/model configuration authority; no secrets.
- `security/vault.py`: API keys, pairing secrets and secret values.
- `models/gateway.py`: provider/model selection, failover, budget, health feedback.
- `models/health.py` and `models/eligibility.py`: runtime model/provider health.
- `memory/service.py`: memory record write/query authority.
- `missions/service.py`: mission state authority.
- `missions/runner.py` and `missions/scheduler.py`: execution/wake engines, not mission truth.
- `agents/service.py`, `agents/team.py`, `agents/runtime.py`: temporary workers and teams.
- `computer/service.py`: local computer capability authority with PTE, execution and verification.
- `browser/service.py`: GENIE-owned CDP browser session authority.
- `plugins/service.py`: plugin discovery, permission, process supervision and invocation.
- `devices/service.py`: device mesh registry, pairing, command routing and queueing.
- `agents/receipts.py`: deterministic evidence ledger with accepted/rejected/unverified verdicts.
- `experience/bank.py`: recorded trajectories and derived lessons.
- `security/trust.py`, `security/policy.py`, `security/injection_guard.py`, `security/audit.py`: permission, policy, taint and audit authority.

## E. Frontend Architecture

Project: `ui/windows/Genie.Desktop/Genie.Desktop.csproj`

- `App.xaml` merges app resources and converters; startup is owned by `App.xaml.cs`.
- `App.xaml.cs` handles single-instance flow, backend lifecycle, crash logging, theme startup, and shutdown.
- `MainWindow.xaml` provides the native shell, sidebar and per-view `DataTemplate` mapping.
- `Services/BackendClient.cs` is the HTTP/SSE client.
- `Services/BackendLifecycle.cs` owns mutex/events and backend process coordination.
- `Services/GenieBackend.cs` and `Services/BackendProcess.cs` locate/start/stop `pythonw.exe`.
- `Services/ThemeService.cs` applies System/Light/Dark resources and DWM title-bar state.
- `ViewModels/MainViewModel.cs` owns nav/current page state.
- Main views/view-models: Home, Chat, Missions, Agents, Computer, Competition, Experience, Settings, Knowledge, Media, and generic owner surfaces for Skills, Devices, Memory, Forecast and Security.
- Shared themes: `Themes/GenieControls.xaml`, `Theme.Dark.xaml`, `Theme.Light.xaml`, `GeniePalette.xaml`.

Preserved UI fixes:

- `BackendClient.StreamChatAsync` uses `ReadLineAsync` off-dispatcher with `ConfigureAwait(false)`, idle timeout, cancellation disposal, and terminal `done/error` handling.
- `ChatView.xaml.cs` defers `ScrollIntoView` with `Dispatcher.BeginInvoke` to avoid `ItemsControl` collection inconsistency crashes.
- Dispatcher exception handling logs all exceptions and only handles a narrow class of known presentation-only failures.

## F. Backend Boot Sequence

`core/lifecycle.py` starts:

1. Config/logging.
2. SQLite migrations.
3. Audit, vault, trust.
4. Memory, missions, locks.
5. Model registry, health monitor, eligibility tracker, gateway.
6. Director/NEDLE2.
7. Plugins.
8. Computer engine and injection guard.
9. Skills.
10. Devices.
11. Perception, proactivity, forecast, security findings.
12. Agents, artifacts, knowledge, experience.
13. Capability worker and agent runtime.
14. Windows event watcher where available.
15. Orchestrator.
16. Mission runner/scheduler.
17. Voice service.
18. NEDLE provisioning.
19. Crash recovery and interrupted mission detection.
20. IPC server.

## G. Database / Migrations

`core/db.py` uses SQLite with WAL and forward-only migrations stored in
`schema_migrations`. Migrations currently cover:

- Core memory, missions, mission steps, audit, grants, provider state/runtime, artifacts, locks.
- Computer app cache, verifications, workspace index.
- Voice metrics.
- Plugins and browser downloads.
- Skills and corrections.
- Devices, pairings, commands, sync records, sync policies/conflicts.
- Perception, proactivity.
- Agent definitions, tasks, messages, blackboard, artifacts, escalations, experience.
- Agent outcomes, eval runs/results, action metrics.
- Channel sessions/messages and user model facts.
- Experience bank, spec pipeline, knowledge sources.
- Mission schedules and durable mission execution columns.
- Provider runtime backfill.

Tests use isolated DBs through `tests/conftest.py` and `reset_db_for_tests`; owner data should not be used as test fixture.

## H. Providers / Models

Model/provider architecture is backend-owned:

- `models/registry.py` merges shipped defaults with user providers in `data/providers.user.json`.
- Providers have one base URL and one secret ref, with many saved model records.
- API keys stay in `security/vault.py`; provider summaries expose only credential presence.
- `models/provider_http.py` normalizes base/model/chat URLs, builds auth headers, follows redirects safely, and avoids cross-origin credential leakage.
- Discovery returns available models; saved models are only the owner's selected models.
- Empty/unknown capabilities are normalized to `["general"]`, so discovered models without advertised metadata are routable.
- `models/gateway.py` selects by capability, policy, health, eligibility, credential presence, priority and cost.
- Generic internet connectivity probing is diagnostic only and does not remove provider candidates.
- Model-scoped failures such as permission denied/model unavailable/malformed cool down the model, not the whole provider.
- Provider-wide auth/quota/rate/unreachable failures update runtime eligibility.

Open product gap: recent-success preference exists indirectly via `HealthMonitor` and ordering, but the audited code still sorts primarily by priority/cost after filtering. It does not yet prove a rich "prefer the 3-4 recently working models out of 111" strategy.

## I. NEDLE2 / Director

`director/nedle2.py` is the fast director and `director/heuristics.py` provides fallback/guards. Its role is classification: intent, task category, capability, mission vs action vs conversation, memory writes/query hints. It is not the model judge. `core/orchestrator.py` converts the decision into a `ModelRequirement`; `models/gateway.py` chooses the provider/model.

## J. Memory / Context

- `memory/service.py` persists typed records with person/project/mission/privacy/data-class metadata.
- `context/builder.py` builds safety, mission, environment, memory and recent sections for model prompts.
- `core/lifecycle.py` also has an in-process per-session transcript for recent conversational references such as "what did I just ask".
- `agents/contextcompaction.py` and `agents/continuation.py` support longer-running agent continuity.

## K. Chat Pipeline

```text
ChatView
  -> ChatViewModel
  -> BackendClient.StreamChatAsync
  -> POST /api/chat/stream
  -> core/ipc/server.py _chat_stream
  -> Daemon.stream_chat
  -> Orchestrator.stream_text
  -> Director + context + Gateway/action/mission
  -> SSE start/delta/done or error
  -> BackendClient posts UI deltas
  -> observable transcript
```

SSE contract:

- `event: start`, JSON includes `session_id`.
- `event: delta`, JSON includes `text` and `kind` (`token`, `progress`, `final`).
- `event: done`, JSON includes `deltas`, `token_deltas`, `mode`, `streamed`.
- `event: error`, JSON includes `error` and diagnostic location.

## L. Voice Pipeline

Voice is wired through the same GENIE intelligence path:

```text
input provider
  -> STT provider
  -> VoicePipeline
  -> handler in Daemon.chat
  -> same orchestrator/router/model/action path
  -> TTS provider
  -> output provider
```

Current provider chain from source:

- Input: `sounddevice` microphone when available; otherwise configured WAV input or unavailable status.
- STT: HTTP STT if configured; Vosk offline recognizer if installed; `file-stt` fallback if allowed; null provider otherwise.
- TTS: HTTP TTS if configured; Windows SAPI; SAPI-to-file; synthetic file TTS; null provider.
- Realtime: Gemini Live provider exists, but `available()` requires a Gemini key in the vault.

Implemented but not live-accepted by this audit: natural multi-turn live voice, one greeting per voice session, and live STT quality on this machine. Source shows real microphone and same-orchestrator wiring, but no live microphone acceptance was run in this pass.

## M. Missions

`missions/service.py` owns mission truth and legal transitions. `missions/planner.py` creates durable DAGs. `missions/runner.py` starts agent teams, checkpoints each step, uses idempotency keys, verifies evidence, and finalizes missions as completed/failed/waiting. `missions/scheduler.py` normalizes and wakes recurring/due missions. `missions/control.py` parses owner pause/resume/cancel/run commands.

Durable mission execution is implemented in source, but broad long-running acceptance, restart/resume acceptance and schedule matrix testing were not run in this audit.

## N. Agents

- `agents/factory.py`: profile/agent creation and experience store.
- `agents/personas.py`: loads persona templates from `data/personas`; current corpus count is 295 markdown personas.
- `agents/service.py`: active team facade and mission/team operations.
- `agents/team.py`: DAG, mailbox, blackboard, task orchestration.
- `agents/runtime.py`: capability worker execution path.
- `agents/budget.py`: budget controls.
- `agents/artifacts.py` and `agents/outputstore.py`: artifact storage.
- `agents/competition.py`: independent candidate competition.
- `agents/receipts.py`: receipt ledger.
- `agents/evolution.py`: governed self-evolution.

Personas are templates, not hundreds of live agents. Agent instantiation is task/mission-specific.

## O. Competition

`agents/competition.py` implements independent attempts, blind verifier scoring, no-winner handling, cancellation, budget-sensitive candidate counts, and one commit stage. It composes existing agent/team/runtime services. Current source does not show automatic mission-runner use of competition for every mission step; it is available as a service/API (`/api/competition`, `/api/competition/run`) and tested in unit coverage, but automatic integration remains not proven.

## P. Experience / Self-Improvement

`experience/bank.py` records trajectories and derives semantic lessons only when evidence is sufficient. It reports insufficient evidence instead of inventing lessons. `agents/evolution.py` enforces governed change flow:

```text
propose -> conformance -> gates -> owner approval -> apply
```

With `EvolutionVcsBackend`, proposals get isolated branches/worktrees. Nothing in the audited source silently rewrites live GENIE code.

## Q. Skills / Teaching

- `skills/service.py` exposes skills and teaching sessions.
- `skills/registry.py` stores versions/status.
- `skills/runtime.py` runs skill steps through capability execution.
- `skills/learning.py` handles candidate detection, generalization and sandbox validation.
- `teaching/recorder.py` records semantic events and redacts secrets.

Teaching does not automatically become a trusted skill; the lifecycle is record, analyze, candidate, validate, save/reject, with rollback/versioning support.

## R. Computer Engine

`computer/service.py` implements PTE check, safe mode check, execution, verification, audit and event publication. `computer/executor.py` handles action strategy. `computer/verifier.py` has verifiers for apps, windows, volume, files, clipboard, shell, input, capture, UIA, browser, workspace and process actions. Lack of verifier is not a verified success.

The wrong-action baseline slice passed in this audit. Source also explicitly prevents classification-time `reply_hint` from being reported as execution truth on failures.

## S. Browser

`browser/service.py` owns a CDP browser session with a dedicated profile under the workspace. It supports navigation, DOM query/click/type/extract, tabs, condition-based waits, forms, scroll, uploads/downloads, dialogs, history, accessibility, cookies, screenshots and read-only preview frames.

Ownership rule: `shutdown()` only terminates the PID GENIE launched or can prove it owns. GENIE should not kill unrelated owner browsers.

## T. Devices / Android

`devices/service.py` implements pairing, registry, explicit grants, HMAC/replay/idempotency concepts through `devices/protocol.py`, offline command queueing, TTL, result verification and audit. Android foundation exists under `android/genie-node`, with Kotlin protocol/service files. The source supports a mesh foundation; this audit does not claim a final Android product is complete.

## U. Perception / Proactive

`perception/service.py` implements zones, privacy-first sensing policies, camera activation gating, mandatory indicator checks, motion/presence/fusion, and optional gateway-based vision classification. `proactive/service.py` implements notification scoring, quiet hours, dedupe/preferences and pre-action risky-operation warnings.

Perception avoids always-on expensive vision by classifying behind events and intervals. Live sensor acceptance was not run in this audit.

## V. Plugins / Specialists / Donor Reuse

Installed plugin directories at audit time:

- `_test_crash`
- `_test_timeout`
- `home`
- `media`
- `vscode`

`plugins/service.py` isolates plugins in host processes, enforces declared permissions and PTE, audits invocations, and contains crash/timeout failures. External/donor capability currently visible:

- Prime/RLM vendored in `vendor/prime_rlm` and wrapped by `integrations/prime_rlm.py` / `prime_launcher.py`.
- Strix audit donor under `vendor/strix-audit` and wrapped by `integrations/strix_runtime.py` / `strix_launcher.py`.
- Scrapling adapters under `integrations/scrapling_*`.
- MiroFish worker under `integrations/mirofish_worker.py`.
- MCP runtime/supervision under `integrations/mcp_runtime.py` and `mcp_supervision.py`.

GENIE remains the parent system; these are donors/wrapped specialists, not replacement runtimes.

## W. Security

- Vault: `security/vault.py`; raw secrets are not returned to UI summaries.
- PTE/trust: `security/trust.py`; owner bootstrap grants, default deny for others.
- Policy: `security/policy.py`.
- Injection guard: `security/injection_guard.py`.
- Audit: `security/audit.py`, tamper-evident hash chain.
- Findings: `security/findings.py`.
- Skill scanner: `security/skill_scanner.py`.
- Receipts: `agents/receipts.py` distinguishes `accepted`, `rejected`, `unverified`.

Provider edit/save code preserves secret refs when replacement fields are empty, and the WPF UI labels stored keys as `Key stored` / `Replace API key`.

## X. Workspaces / Artifacts

`core/paths.py` is the path authority. `computer/workspace.py`, `core/isolation.py` and `core/staleguard.py` support workspace identity, scope, quotas, mission claims/releases, cleanup, stale-write protection and isolation reporting. Agent artifacts are referenced by id/path/hash rather than pasted through transcripts.

## Y. Native Process Lifecycle

Single frontend instance:

- Named mutex: `Local\Genie.Desktop.SingleInstance`.
- Activation event: `Local\Genie.Desktop.Activate`.
- Shutdown event: `Local\Genie.Desktop.Shutdown`.
- Pending shutdown event handles installer/uninstaller requests during startup.

Backend process:

- Started via `pythonw.exe`, hidden, `CreateNoWindow=true`.
- Current-build backend reuse is checked by `/api/status` build identity and runtime root/manifest commit.
- Foreign backend on the GENIE port can be stopped by exact listening PID after endpoint fingerprinting.
- Owned backend shutdown uses recorded PID/process tree, not image-name kills.

Risk: `App.xaml.cs` still uses `Thread.Sleep` while waiting for other frontend binaries to exit. It is startup-only, but still a UI-thread responsiveness smell.

## Z. Source vs Packaged Runtime

`scripts/build_backend_runtime.py` builds an embeddable Python runtime and copies backend packages, entrypoints and read-only data. `scripts/sync_backend_runtime.py` is the deterministic source-to-runtime sync/check path and writes `backend-dist/backend-runtime/RUNTIME_MANIFEST.json`.

Runtime check passed in this audit. The manifest records HEAD commit, not dirty tree identity; the hash list is still the real drift detector.

## AA. Current Test Architecture

- Unit: `tests/unit` (121 files at audit time).
- Contract: `tests/contract`.
- E2E: `tests/e2e`.
- Owner acceptance: `tests/owner`.
- External optional/live: `tests/external_optional`.
- UI tests: `ui/tests`.
- WPF smoke/compile tests: `ui/windows/Genie.Desktop.Tests` and `dotnet build`.
- Scripts cover packaged runtime, native client, installer/upgrade, voice/home, provider flows, shutdown/single-instance and responsiveness.

This audit intentionally did not run the full Python suite, installer matrices, upgrade loops, one-hour lifetime tests, or live owner acceptance.

## AB. Confirmed Currently Working

Based on current evidence:

- Packaged backend runtime matches source package set: `sync_backend_runtime.py --check` passed.
- WPF Release build compiles with 0 warnings/errors.
- Focused backend slice passed: provider failure behavior, security, director and wrong-action baseline (`41 passed, 1 deselected`).
- SSE client source preserves off-dispatcher async reads and terminal event completion.
- Multi-turn scroll crash fix is present in `ChatView.xaml.cs`.
- Provider/model fixes for empty capabilities, diagnostic offline probe and model-scoped failures are present in source.
- Provider secret presence and edit-preserve semantics are present in source/UI.

## AC. Confirmed Currently Broken

Confirmed during this audit:

- The PowerShell profile references missing `E:\work\openclaw-test-state\completions\openclaw.ps1`, producing noise on every shell command. This is environment drift, not GENIE product code.

No product runtime failure was reproduced in this audit.

## AD. Implemented But Not Live-Accepted

- Natural live voice session and one-greeting flow.
- Live microphone STT quality on this machine.
- Multi-turn continuous voice.
- Mission restart/resume/scheduler long-run behavior.
- Automatic competition use inside ordinary mission execution.
- Recent-success-rich routing across very large provider model pools.
- Windows 10 full acceptance, signing, installer/upgrade matrices.
- Permanent social account integrations.
- Live external provider integrations beyond inspected code paths.

## AE. Documentation Drift

Docs include many historical phase/RC reports (`PHASE*.md`, `RC15*`, `RELEASE_*`, `WINDOWS_NATIVE_*`, `RECONCILIATION_2026-09-20.md`). They should be read as historical evidence, not current truth. Current source has post-rc26 dirty changes and untracked files, so rc26-era claims are not automatically current.

`docs/REPO_MAP.md` predates several current files and surfaces, including mission runner/control/scheduler, model eligibility/provider HTTP, ThemeService and recent provider-flow scripts. It should be updated after owner review if this master doc is accepted.

## AF. Duplicate / Dead / Legacy Code Risks

- Legacy web UI still exists under `ui/web`; it is read-only data for the daemon/web surface, not the native parent UI.
- Old Electron tests exist under `ui/tests/electron`; treat as legacy coverage unless intentionally revived.
- `Services/BackendProcess.cs` and `Services/GenieBackend.cs` both contain backend process concepts; current lifecycle uses `GenieBackend`, while `BackendProcess` appears narrower/older.
- Large donor repos exist outside GENIE under `E:\G3\repos` and `E:\G3\tool`; they are not GENIE source authority.

## AG. Owner-Data Risks

- Development mode defaults `data_dir` to `E:\G3\GENIE\data`; packaged mode uses `%LOCALAPPDATA%\GENIE\data`.
- `GENIE_DATA_DIR` absolute override is supported and should be used for tests/scripts that run real daemons.
- Installed test plugins `_test_crash` and `_test_timeout` are present in `plugins/installed`; useful for plugin tests but should not be confused with owner-facing integrations.
- Never write fake providers, mock credentials, random missions or test memory to owner data.

## AH. Top 10 Architectural Risks

1. Natural-language action claims bypassing real execution evidence.
2. Source/runtime identity ambiguity when dirty-tree changes are packaged but manifest only records HEAD.
3. Voice falling back to `file-stt`, making UI appear voice-capable without real live transcription.
4. Recent-success routing not yet strong enough for providers with very large mostly-bad model pools.
5. Automatic competition not proven in mission runner.
6. Legacy web/Electron artifacts confusing the native WPF authority boundary.
7. Dispatcher or startup blocking work regressing responsiveness outside the fixed SSE path.
8. Owner data pollution by acceptance/debug scripts.
9. Plugin/device/perception adapters being configured but not actually reachable/live.
10. Historical docs overstating release or acceptance state after post-rc26 development.

## AI. Next Development Order

1. Add an evidence-gated action response layer so LLM prose cannot claim external action success unless execution state/receipt says it happened.
2. Make voice status explicitly distinguish real live STT, file STT fallback and unavailable STT in Home/Settings.
3. Add recent-success and known-bad model scoring to the gateway, with diagnostics explaining candidate ordering.
4. Decide and implement whether mission runner should invoke competition automatically for selected step types.
5. Update `docs/REPO_MAP.md` to match current source after owner review.
6. Add a dirty-tree/runtime manifest identity marker so Preview can display "runtime includes uncommitted changes" honestly.
7. Run targeted live acceptance for provider edit/discovery, voice, mission resume and full exit before any release candidate.

## AJ. File Map

- `backend_entry.py`: packaged backend entrypoint path bootstrap.
- `genie.py`: CLI commands.
- `core/paths.py`: app/data/log/model path authority.
- `core/config.py`: config defaults, user config, env overrides.
- `core/db.py`: SQLite connection and migrations.
- `core/lifecycle.py`: daemon boot and service graph.
- `core/orchestrator.py`: chat/action/mission turn orchestration.
- `core/ipc/server.py`: HTTP/SSE API.
- `core/contracts.py`: shared dataclasses/enums/contracts.
- `models/registry.py`: provider/model registry.
- `models/gateway.py`: model selection, completion, stream, discovery, testing.
- `models/eligibility.py`: runtime provider eligibility.
- `models/health.py`: provider/model health and breaker state.
- `models/provider_http.py`: OpenAI-compatible HTTP helpers.
- `director/nedle2.py`: NEDLE2 director.
- `director/heuristics.py`: local heuristic classification.
- `memory/service.py`: persistent memory.
- `context/builder.py`: model context packet builder.
- `missions/service.py`: mission state authority.
- `missions/planner.py`: objective-to-DAG planner.
- `missions/runner.py`: durable mission execution.
- `missions/scheduler.py`: recurrence normalization/wake.
- `missions/control.py`: chat/voice mission control commands.
- `agents/service.py`: agent team facade.
- `agents/team.py`: team DAG/mailbox/blackboard orchestration.
- `agents/runtime.py`: capability worker runtime.
- `agents/factory.py`: agent/profile factory.
- `agents/competition.py`: verified competing attempts.
- `agents/receipts.py`: receipt ledger.
- `agents/evolution.py`: governed self-evolution.
- `computer/service.py`: PTE/execution/verification facade.
- `computer/executor.py`: local action execution strategies.
- `computer/verifier.py`: action verification registry.
- `browser/service.py`: CDP browser provider.
- `browser/cdp.py`: CDP process and websocket helpers.
- `voice/service.py`: voice facade/provider chain.
- `voice/pipeline.py`: capture/STT/orchestrator/TTS flow.
- `voice/providers.py`: microphone, STT, TTS, output and realtime providers.
- `plugins/service.py`: plugin process/permission facade.
- `devices/service.py`: device mesh facade.
- `perception/service.py`: zones, camera/motion/presence perception.
- `proactive/service.py`: proactive notification and risky-action warnings.
- `security/vault.py`: secrets vault.
- `security/trust.py`: PTE grants/checks.
- `security/audit.py`: audit log.
- `security/injection_guard.py`: taint/prompt-injection guard.
- `skills/service.py`: skills facade.
- `skills/learning.py`: skill candidate validation/generalization.
- `teaching/recorder.py`: semantic teaching recorder.
- `ui/windows/Genie.Desktop/App.xaml.cs`: desktop startup/lifecycle/crash/theme.
- `ui/windows/Genie.Desktop/Services/BackendClient.cs`: HTTP/SSE client.
- `ui/windows/Genie.Desktop/Services/BackendLifecycle.cs`: single-instance/backend coordination.
- `ui/windows/Genie.Desktop/Services/ThemeService.cs`: dynamic theme/DWM service.
- `scripts/sync_backend_runtime.py`: source/runtime drift check and sync.
- `scripts/build_backend_runtime.py`: embeddable Python runtime builder.

## AK. Do-Not-Break Invariants

- Current source and current executable behavior outrank historical reports.
- WPF is presentation/control only; Python daemon owns product truth.
- Do not create a second memory service, model router, mission engine, agent runtime or browser authority.
- LLM text is never proof of execution.
- Actions must follow plan, act, observe, verify, audit/receipt, report.
- Secrets stay in the vault and are never sent back to WPF or printed.
- Empty model capabilities mean unknown/general, not unusable.
- Generic internet probes are diagnostic only, not provider-health authority.
- Model failures must stay model-scoped when the failure is model-specific.
- Saved models and discovered models are separate concepts.
- Search/filter UI must not clear hidden model selections.
- SSE reads must stay off the WPF dispatcher and must respect `done`/`error`.
- Chat auto-scroll must remain deferred.
- Unexpected dispatcher exceptions must not be silently swallowed.
- Tests must use isolated data and must not pollute owner data.
- GENIE must stop only owned backend/browser processes by proven PID/ownership.
- Plugins and donor engines are capability donors, not parent architectures.
- Missing evidence is `unverified`, not success.
- No release candidate, installer freeze or broad refactor belongs in comprehension-only work.

## Current End-to-End Flow Diagrams

Text:

```text
Owner
  -> ChatView/ChatViewModel
  -> BackendClient HTTP/SSE
  -> /api/chat or /api/chat/stream
  -> Orchestrator
  -> Director + memory/context hint
  -> ModelRequirement
  -> Gateway/provider OR action/mission path
  -> verification/mission state
  -> reply/SSE deltas
```

Voice:

```text
Microphone/WAV input
  -> VAD/capture in VoicePipeline
  -> STT provider
  -> VoiceService handler
  -> Daemon.chat
  -> same Orchestrator/Gateway/action path as text
  -> reply
  -> TTS provider
  -> sounddevice/WAV output
```

Action:

```text
Request
  -> Director classification
  -> semantic guard + proactive risk pre-scan
  -> ComputerService/CapabilityWorker
  -> PTE/trust check
  -> Executor/browser/device/plugin/skill
  -> observe
  -> verifier
  -> audit/action metrics/receipt-capable evidence
  -> truthful response
```

Mission:

```text
Objective
  -> Orchestrator durable mission creation
  -> MissionService state
  -> MissionRunner ensure_plan
  -> AgentService team
  -> per-step classify/act/reason
  -> verification/evidence checkpoint
  -> scheduler wait/resume if recurring
  -> completed/failed/waiting state
```
