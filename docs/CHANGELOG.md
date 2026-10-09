# GENIE — CHANGELOG

Format: `date | type | scope | summary`. Types: `spec` · `feat` · `fix` · `contract` · `docs` · `decision`.

---

## 2026-09-18 — Phase 14: hardening & daily-use release (in progress)

### feat — backup & restore (14.1)
| Scope | Summary |
|---|---|
| `core/backup.py` | `BackupService` — SQLite **online backup API** snapshots (a live WAL db must never be `shutil.copy`'d), sha256 manifest per file, `list`/`verify`/`restore`. `restore()` verifies first and **refuses** a tampered archive; vault is opt-out and labelled `contains_secrets` |

### feat — safe mode, offline mode, crash recovery (14.2)
| Scope | Summary |
|---|---|
| `core/hardening.py` | `SafeMode` (deny real capability ids: `browser.`, `shell.`, `input.`, mutating `files.`, `clipboard.set`, `application.*`, `system.volume`, `process.kill`, `plugin.`, `devices.`), `OfflineMode` (network probe → route local-only, report `degraded`), `CrashRecovery` (unclean-shutdown flag, purge stale locks, report interrupted missions, never auto-resume) |
| `computer/service.py` | safe mode gate consulted in `execute()` **before** the PTE check |
| `core/lifecycle.py` | `Daemon.start()` → `CrashRecovery.recover(locks, missions)` + `mark_start()`; `Daemon.stop()` → `mark_clean()` |
| `genie.py` | global `--safe` flag |

### feat — updater & rollback (14.3)
| Scope | Summary |
|---|---|
| `core/updater.py` | `Updater` — releases are **directories**, never in-place overwrite; `stage()` copies + hashes, `activate()` records the previous version first, `rollback()` restores it and refuses when empty / missing / corrupt |

### fix
| Scope | Summary |
|---|---|
| `core/hardening.py` | **safe mode was matching PTE scopes (`browser:navigate`) instead of capability ids (`browser.navigate`)** — capability ids use dots, scopes use colons. Matching the wrong form matched nothing, making safe mode a no-op that merely looked defensive. Caught by tests asserting real capability ids |

### tests
`test_phase14_backup.py` (12) · `test_phase14_hardening.py` (19) · `test_phase14_updater.py` (14).
Deterministic suite **809 passed**.

| `models/gateway.py` | offline mode consulted in `candidates()` — remote providers dropped when offline, local + mock remain. `get_offline_status()` is **cached (30s)** so requests don't each pay a TCP timeout |
| `genie.py` | `backup`, `backup-list`, `backup-verify`, `backup-restore` subcommands |

| `genie.py` | `update-status` / `update-stage` / `update-activate` / `update-rollback` |
| `installer/GENIE.iss`, `installer/README.md` | Windows installer **spec** — explicitly labelled **UNVERIFIED**; needs Inno Setup 6 on a Windows build host. Per-user install; uninstall deliberately keeps owner data |
| `tests/e2e/test_golden_14_18.py` | **golden #14 (crash recovery)** and **golden #18 (offline)** — both were previously uncovered |
| `docs/GOLDEN_TASKS.md` | per-task status matrix for all 18 golden tasks (spec Appendix E) so the exit gate is measurable rather than a feeling |
| `core/action_metrics.py` | **wrong-action rate instrumentation** (exit gate had no way to measure it). Target 2% default. A correctly-refused action (denied / confirmation / safe-mode) is **not** a wrong action — otherwise the metric would punish the agent for asking permission |
| `core/db.py` | migration `013_metrics` → `agent_action_outcomes` |
| `computer/service.py` | records every execution, denial and safe-mode block into the meter |
| `core/ipc/server.py` | `GET /api/metrics/actions` |
| `tests/e2e/test_golden_6_memory.py` | **golden #6 (memory)** — correct episode retrieved under different wording; unrelated episode not returned; unknown query doesn't invent an answer. Was previously uncovered |
| `core/backup.py` | `prune(keep_last=N, dry_run=…)` — backup retention, so backups don't accumulate forever and quietly defeat the storage quota |
| `core/release_fetch.py` | **release fetching** — channel-agnostic (any manifest URL). Verifies per-file sha256; **refuses a signed manifest when no verifier is supplied**; discloses when a manifest is unsigned. Closes the last "not started" Phase 14 item |
| `genie.py` | `update-fetch <url>` |

### feat — Phase 14.4 repository capability reconciliation
| Scope | Donor | Summary |
|---|---|---|
| `agents/receipts.py` | DeerFlow | tool receipts + deterministic acceptance. **UNVERIFIED / REJECTED / ACCEPTED** — a claim citing a missing receipt is UNVERIFIED, never accepted, so self-improvement can't learn from fiction |
| `core/staleguard.py` | DeerFlow | read-before-write: content-hash versioned conditional writes. A lock alone doesn't stop A-reads / B-writes / A-clobbers |
| `agents/middleware.py` | AgentScope | middleware chain (PTE / Budget / Receipt / Audit); short-circuits recorded, a failing observer never loses the result |
| `agents/a2a.py` | AgentScope | A2A specialist boundary — `AgentCard` / `SpecialistAgentRegistry` / `A2AProvider`. No transport ⇒ `pending-live-acceptance`, never a faked result |
| `security/findings.py` | Strix | canonical security finding + dedupe. Fingerprint **excludes** cwe/cve/artifacts so one finding is not split into two; PTE still owns authorisation |
| `forecast/service.py` | MiroFish section | forecasting contract + engine routing. **No numeric probability without defensible evidence**; rounded (0.6237 → 60%, never 62.37%); Laplace smoothing; MiroFish/Kronos compose, never compete |
| `data/personas/`, `agents/personas.py` | agency-agents | **fix:** vendoring used `glob("*.md")` dropping 15 nested game-engine personas — switched to `rglob` (264 → 279 templates) |
| `agents/continuation.py` | DeerFlow | goal continuation + **no-progress breaker** — four gates (goal unfinished / no run failure / no owner-only blocker / progress + budget remain). `run_until_settled` re-checks the budget every pass, so it cannot self-loop |
| `agents/contextcompaction.py` | DeerFlow | context compaction with protected mission truth / receipts / artifacts / recent / compressible; **protected items never compacted**, even at `budget=50` |
| `security/findings.py` | Strix | SARIF 2.1.0 export (rules, results, levels, artifact locations, CWE/CVE/CVSS) for CI ingestion |
| `agents/outputstore.py` | DeerFlow + Strix | oversized tool output spills to the artifact store; the model gets **head + tail + reference** (head-only truncation hides tracebacks) |
| `core/jsonrepair.py` | AgentScope | structured-output repair — fences, surrounding prose, trailing commas, truncated output; reports the strategy used; **fails honestly instead of inventing an object** |
| `agents/batch.py` | DeerFlow | durable batch / subagent execution — capacity limit, lease-based claims, bounded retries, duplicate-delegation ledger, **expired leases re-queued so a dead worker costs one attempt, not the work** |
| `integrations/mcp_supervision.py` | Strix | MCP connection supervision — explicit states (new/connecting/live/degraded/quarantined/dead), quarantine cooldown preventing retry storms, pending-call cleanup on death, concurrency semaphore |
| `docs/REPO_REAUDIT_14.md` | — | new: new Tier-1 donors, forecasting design, old-repo reconciliation, DOCUMENTED/IMPLEMENTED/TESTED/LIVE-ACCEPTED |

### outstanding
Nothing *fetches* a release (no release channel decided); installer spec is uncompiled/untested;
roadmap exit gate (18 golden tasks + rollback on a **real machine**) not yet met — rollback
verified deterministically only.

---

## 2026-09-18 — Phase 14.4 Repository Capability Re-Audit

Re-opened the Phase-11 audit: DeerFlow/AgentScope/Strix arrived after it, and several older entries
only reached generic-adapter level. No completed phase rewritten; GENIE remains the parent platform.

### feat — new donor capability (all with deterministic tests)
| Scope | Donor | Summary |
|---|---|---|
| `agents/receipts.py` | DeerFlow | tool receipts + deterministic acceptance — **UNVERIFIED / REJECTED / ACCEPTED**; a claim citing a missing receipt is never accepted |
| `core/staleguard.py` | DeerFlow | read-before-write: conditional writes on content-hash version; locks alone don't stop stale clobbers |
| `agents/middleware.py` | AgentScope | middleware chain (PTE / Budget / Receipt / Audit); short-circuits recorded, failing observers never lose the result |
| `agents/a2a.py` | AgentScope | A2A specialist boundary — `AgentCard` / `SpecialistAgentRegistry` / `A2AProvider`; no transport ⇒ `pending-live-acceptance`, never faked |
| `security/findings.py` | Strix | canonical security finding + dedupe (fingerprint excludes cwe/cve/artifacts so one finding isn't split into two) |
| `forecast/service.py` | MiroFish section | forecasting with routing + calibration; **no numeric probability without defensible evidence**; rounded (0.6237→60%, never 62.37%) |
| `security/skill_scanner.py` | platform | static skill-install security scan (the install gate) — P0 BLOCK (rm -rf / del /S /Q / shutil.rmtree / fork bomb / dd→/dev), P1 CONFIRM (curl|sh, os.system, subprocess shell=True, secrets, prompt-injection), P2 info (egress); never executes code |
| `observability/tracing.py` | platform | self-contained OTel-style tracer — span tree w/ contextvars propagation, in-memory exporter, `configure_otlp()` OTLP/HTTP-JSON via stdlib (no SDK dep), `to_otel_json` OTel-compatible wire format |
| `integrations/mcp_runtime.py` | platform | MCP runtime/client — `initialize`/`tools/list`/`tools/call` over a supervised session; pluggable transport (real or in-memory fake), tool cache, MCP `isError` distinct from transport failure, death via exhausted reconnects | TESTED (11) |
| `skills/hub.py` | platform | skill registry / Skill Hub — records scanned skills with their safety verdict; BLOCK registers disabled (force to enable), CONFIRM flagged, OK enabled; enable/disable + capability lookup; pluggable storage (in-memory default) | TESTED (13) |

### fix — quiet-hours test flake (time-of-day)
| Scope | Summary |
|---|---|
| `proactive/notifications.py` | `NotificationPolicy.window_for` called `QuietHours.active()` with no time, so the quiet-hours active check used the wall clock, not the injected `now` — 3 tests only passed when the suite ran 22:00–07:00 local. Plumbed the injected clock through (`policy.now`, honoured by the Notifier) + a regression test. Production behaviour unchanged (defaults to `time.time`) |

### fix — agency-agents corpus (count inconsistency resolved)
| Scope | Summary |
|---|---|
| `data/personas/`, `agents/personas.py` | vendoring used `glob("*.md")`, silently dropping **15 game-engine personas** (godot/unity/unreal/blender/roblox). Re-vendored + loader switched to `rglob`: **264 → 279 templates / 18 divisions** |

### fix — tests brittle to an HTTP proxy
`test_phase11_integrations.py`, `test_phase14_release_fetch.py`: a proxy now returns `timed out` /
`HTTP 502` instead of `unreachable`. Behaviour was always correct (no fake success); assertions
relaxed to any transport-failure wording.

### docs
| Scope | Summary |
|---|---|
| `docs/REPO_REAUDIT_14.md` | new — new Tier-1 donors, forecasting design, old-repo reconciliation, DOCUMENTED/IMPLEMENTED/TESTED/LIVE-ACCEPTED states |
| `docs/REPO_UTILIZATION_AUDIT.md` | pointer + warning that the audit was re-opened |
| `docs/GOLDEN_TASKS.md` | corrected 13/18 → **17/18** (see correction note) |
| `docs/THREAT_MODEL.md` | security | threat model — trust boundaries, STRIDE-style catalogue, mitigations mapped to modules, explicit residual gaps (runtime prompt injection, no sandbox, telemetry scrubbing) |

### tests
persona reconciliation (5) · receipts (12) · staleguard (11) · middleware (11) · a2a (10) ·
security findings (16) · forecast (17). Deterministic suite **938 passed**.

---

## 2026-09-18 — Phase 13: UI polish + User Control Center

### feat — user control center
| Scope | Summary |
|---|---|
| `core/controlcenter.py` | `ControlCenter` — the three answers the roadmap's exit gate demands, from live services: (1) what GENIE remembers, grouped by type, each forgettable; (2) which agents run (teams, tasks, provider, paused/cancelled); (3) which provider sees my data, split **local vs remote** with `sees_my_data == remote AND enabled` |
| `core/ipc/server.py` | `GET /api/control-center`, `POST /api/control-center/forget`; `/ui/control` route |
| `ui/web/control.html`, `ui/web/control.js` | Control Center page — one click from the sidebar, all three answers on one page, 5s refresh, dark theme via CSS variables |
| `ui/web/index.html` | sidebar buttons: **Control Center** and **Operator console** |

### fix
| Scope | Summary |
|---|---|
| `core/controlcenter.py` | `registry.models()` returns `ModelSpec` **dataclasses**, not dicts — normalised via `_as_dict()` so model counts work |

### docs
| Scope | Summary |
|---|---|
| `docs/ROADMAP.md` | **synced** — phases 5–12 were still marked 🟥 although complete and documented in `ACTIVE_WORK.md`; now consistent, with a sync note |
| `docs/PHASE13.md` | new |

### tests
`test_phase13_controlcenter.py` (11). Deterministic suite **764 passed**.

---

## 2026-09-18 — Phase 12: self-improvement

### feat — experience distillation (12.1, adapted from Youtu-Agent)
| Scope | Summary |
|---|---|
| `agents/factory.py` | `ExperienceStore.distill()` — training-free group advantage: group attempts by task type, compare each (role, provider, strategy) against the task-type baseline, emit `prefer`/`avoid` with `advantage` + `evidence`. Guards: below `min_attempts` → no guidance, \|advantage\| < 0.05 → noise, unknown type → `None` |
| `agents/factory.py` | `AgentFactory.recommend_configuration()` (build the next agent with the configuration that actually won) + `improvement_report()` |

### feat — evaluation / dev lab (12.2, adapted from Qwen-AgentWorld)
| Scope | Summary |
|---|---|
| `evaluation/lab.py` | `Scenario` + `EvaluationLab`: suites, multi-dimensional scoring, run history, baselines and regression detection (drop beyond tolerance vs best-ever score) |
| `core/db.py` | migration `012_eval` — `agent_eval_runs`, `agent_eval_results` |
| `agents/providers.py` | fix: `_write_artifact` now creates the artifact directory (a missing root looked like "the agent did nothing") |

### feat — governed self-evolution (12.3, adapted from enoch)
| Scope | Summary |
|---|---|
| `agents/evolution.py` | `EvolutionEngine` — lineage tracking, mandatory conformance checks, owner-approval gate. propose → conformance MUST pass → owner MUST approve → apply. All transitions audited; **nothing auto-applies** |

### tests
`test_phase12_improvement.py` (7) · `test_phase12_evaluation.py` (8) ·
`test_phase12_evolution.py` (13). Deterministic suite **753 passed**.

---

## 2026-09-18 — Phase 11: repository integration, workspace & observability

### docs — repository utilization audit (11A)
| Scope | Summary |
|---|---|
| `docs/REPO_UTILIZATION_AUDIT.md` | all **22 distinct supplied repos** (23 archives, one duplicate) inspected in real source; each with purpose, capabilities, GENIE equivalent, disposition, integration point, target phase + canonical capability matrix and a per-repo "what capability exists in GENIE" answer |

### feat — specialist integrations & agent runtime composite (11B)
| Scope | Summary |
|---|---|
| `agents/personas.py` | persona corpus vendored as **role templates** (264 templates / 18 divisions, from agency-agents); name/division-weighted search; `to_agent_spec()` |
| `agents/factory.py` | `create_from_persona()` — role template → Agent Factory → one task-specific agent; degrades to the built-in worker if the corpus is absent; `persona_statistics()` |
| `integrations/specialists.py` | one `SpecialistAdapter` contract (`available`/`invoke`/`conformance`) for n8n, ComfyUI, HeyGem, Kronos, MiroFish, OmniVoice + authorisation-gated Decepticon; `capability_matrix()`; absent engines report `pending-live-acceptance`, never fake success |
| `data/personas/` | vendored persona corpus (19 divisions) — data, not 264 permanent agents |

### feat — GENIE workspace / background computer (11C)
| Scope | Summary |
|---|---|
| `computer/workspace.py` | identity + `ensure_identity`; mission ownership (`mission_claim`/`mission_release`/`missions`) with per-mission `downloads/artifacts/temp`; dedicated `session_dir`/`browser_profile_dir`; mission-scoped `downloads_dir`; **shell that refuses path escapes**; dry-run-by-default `cleanup`; quota (`quota`/`set_quota`/`check_quota`, default 2 GiB) |
| `computer/executor.py` | `workspace.*` capabilities wired into the Computer contracts |
| `computer/service.py`, `security/trust.py` | capability→scope registration; new `computer:workspace:{read,write,exec}` PTE scopes |
| `computer/verifier.py` | real verifiers for the mutating workspace capabilities (claim/release/quota/cleanup/shell) |

### feat — operator console, reclassified as supporting infrastructure (11D)
| Scope | Summary |
|---|---|
| `agents/ops.py` | `OperatorConsole` — live dashboard, 7 audited control actions (pause/resume/cancel/force_failover/retire_orphans/set_budget_cap/throttle), unknown actions refused |
| `agents/service.py` | `ops_dashboard/ops_control/ops_audit/ops_record_outcome/ops_outcomes/ops_improve` facade |
| `agents/team.py`, `agents/budget.py` | `max_concurrency` throttle guard, `retire_orphans()` (WORKING-only), `BudgetController.set_cap()` |
| `core/db.py` | migration `011_ops` → `agent_mission_outcomes` (the Phase 12 input) |
| `core/ipc/server.py`, `genie.py`, `ui/web/ops.{html,js}` | `/api/ops/*` endpoints, `genie.py ops*` CLI, vanilla-JS operator console at `/ui/ops` |

### decision
| ID | Summary |
|---|---|
| D-095 | every supplied repository ends in exactly one documented disposition |
| D-096 | never replace a stronger mature system with a weaker clone; wrap engines, prove parity before reimplementing |
| D-097 | ONE GENIE Agent Runtime — supplied agent systems are mined for components, not run in parallel |
| D-098 | personas are role templates, never permanent agents |
| D-099 | GENIE gets its own isolated, mission-owned workspace computer |

### tests
`test_phase11_workspace.py` (10) · `test_phase11_integrations.py` (9) · `test_phase11_personas.py` (8) ·
`test_phase11_ops.py` (8). Deterministic suite green.

---

## 2026-09-17 — Phase 10: agent factory, teams & cost control

### feat — agent teams
| Scope | Summary |
|---|---|
| `agents/contracts.py` | agent kind/lifecycle, task node, message, blackboard entry, artifact, budget, continuation packet, team plan |
| `agents/artifacts.py` | register/version/hash artifacts; read on demand; verify against tampering |
| `agents/budget.py` | budget hierarchy (global→mission→team→agent→task); a child cannot exceed a parent's remaining budget; GENIE-side estimates |
| `agents/team.py` | mailbox, blackboard, task DAG, `TeamOrchestrator` (scheduling, role-aware assignment, failure→retry→provider→replacement→replan, reviewer gated by complexity, cancel/pause) |
| `agents/factory.py` | `AgentFactory` (search-before-create, capability-not-vendor, cost-aware planning, versioned candidates) + `ExperienceStore` |
| `agents/providers.py` | **NEW.** `ModelGateway` (capability→tier, measurable escalation) + `LocalExecutor` (controlled offline provider that writes files, runs pytest, posts real messages) |
| `agents/service.py` | facade: teams, budgets, artifacts, continuation packet, failover (snapshot→circuit breaker→Provider B) |
| `core/contracts.py` | `AgentDefinition` extended (role, kind, `model_requirement`, budget, version) |
| `core/db.py` | migration `010_agents` |
| `core/ipc/server.py`, `genie.py` | `/api/agents*`; `agents`, `agents-plan`, `agents-failover`, `agents-cancel` |
| `agents/providers.py` + `tools/phase10_goldens.py` | golden #4/#5/#7 verified end to end with a real executor → `docs/PHASE10_GOLDENS.md` |

### test — categories
| Scope | Summary |
|---|---|
| `pytest.ini`, `tests/conftest.py` | categories `real_machine` / `hardware_optional` / `owner_acceptance`; the `real_machine` suite runs serially behind the shared `REAL_DESKTOP_TEST` lock |
| `docs/TESTING.md`, `tools/test_suites.py` | the three suites reported separately; the deterministic default is fully green |
| `tests/e2e/test_phase10_goldens.py` | 4 real-executor golden tests + a negative test (broken module rejected) |

### decision
| Scope | Summary |
|---|---|
| D-089…D-094 | capability-not-vendor, PTE authority over generated agents, real-executor proof, service-driven failover, bounded continuation packet, team-size ceiling |

---

## 2026-09-17 — Phase 9: proactivity & companion

### feat — proactivity
| Scope | Summary |
|---|---|
| `proactive/contracts.py` | **NEW.** Outcomes (`ignore…interrupt`), channels, candidate, decision, notification, quiet hours, delivery target |
| `proactive/scoring.py` | **NEW.** Multiplicative decision function with inverted interruption factors; score → outcome thresholds; urgent visibility floor |
| `proactive/notifications.py` | **NEW.** Duplicate suppression (yields to escalation), quiet hours that clamp, urgent override whitelist, presence-aware delivery, traceability, persistence |
| `proactive/service.py` | **NEW.** Facade: candidate sources, risky-action assessment, **pre-scan before execution**, preferences, `explain()` |
| `core/db.py` | migration `009_proactive` |
| `core/orchestrator.py` | destructive steps are pre-scanned; `risks` travel with the turn result |
| `core/lifecycle.py` | service wiring + status |
| `core/ipc/server.py`, `genie.py` | `/api/proactive*`; `proactive`, `proactive-quiet` |

### fix
| Scope | Summary |
|---|---|
| `proactive/scoring.py` | A-072 a neutral preference (0.5) halved every score; neutral now maps to ×1.0 |

### test
| Scope | Summary |
|---|---|
| `tests/e2e/test_phase9_proactivity.py` | 44 tests — scoring, quiet hours (wrap/daytime/zone scoping), duplicate suppression, traceability, **golden #13**, other candidate sources |
| `tests/contract/test_ipc_api.py` | +6 tests — `/api/proactive*` |

### docs
| Scope | Summary |
|---|---|
| `docs/PHASE9.md` | **NEW.** Decision function, MUST rules, golden #13, defects, verification |
| `docs/DECISIONS.md` | D-084…D-088, A-072 |

---

## 2026-09-17 — Phase 8: perception

### feat — perception
| Scope | Summary |
|---|---|
| `perception/contracts.py` | **NEW.** Zones, per-zone sensing policy, perception events, signals, `EnvironmentState` |
| `perception/motion.py` | **NEW.** Dependency-free frame-difference motion detection with hysteresis; reports rising/falling edges |
| `perception/camera.py` | **NEW.** Camera contract, bounded sessions, mandatory indicator (fails closed), honest unavailability |
| `perception/presence.py` | **NEW.** Presence engine: time decay + noisy-OR fusion; absence is "unknown", not "empty" |
| `perception/fusion.py` | **NEW.** Screen/calendar/device/camera sensors → one fused state with graceful degradation |
| `perception/service.py` | **NEW.** Zones, policies, camera lifecycle, events, retention, purge, vision gating |
| `core/db.py` | migration `008_perception` |
| `core/orchestrator.py` | the fused environment joins the context hint (§6.11) |
| `core/ipc/server.py`, `genie.py` | `/api/perception*`; `perception`, `perception-policy`, `perception-events` |

### fix
| Scope | Summary |
|---|---|
| `perception/service.py` | A-071 the vision provider is consulted **once per motion event** (rising edge + interval floor), not once per frame — per-frame classification is continuous streaming |

### test
| Scope | Summary |
|---|---|
| `tests/e2e/test_phase8_perception.py` | 52 tests — privacy defaults, fail-closed camera, real motion arithmetic, **golden #12**, no-streaming bounds, presence decay, fusion degradation, retention/purge, context integration |
| `tests/contract/test_ipc_api.py` | +6 tests — `/api/perception*` |

### docs
| Scope | Summary |
|---|---|
| `docs/PHASE8.md` | **NEW.** Pipeline, privacy model, honest limits, defects, verification |
| `docs/DECISIONS.md` | D-079…D-083, A-071 |

---

## 2026-09-17 — Phase 7: Pi / IoT / home

### feat — peripherals
| Scope | Summary |
|---|---|
| `devices/peripherals.py` | **NEW.** GPIO/relay/sensor contract + sysfs, serial-relay, null and (test-only) virtual backends; idempotent `relay.set`, bounded `relay.pulse`, honest `verified` reporting |
| `devices/node.py` | `PeripheralHandler` — a Pi/USB-relay node; `--peripherals` / `--peripheral-backend` CLI flags |
| `devices/service.py` | Local peripheral capabilities served in-process; `peripherals` in device status |

### feat — home automation bridge
| Scope | Summary |
|---|---|
| `plugins/installed/home/` | **NEW.** Controller bridge: `entities`, `state`, `turn_on`, `turn_off`, `set_brightness`, `activate_scene`, `reachable` — idempotent, read-back verified, no `toggle` |

### fix
| Scope | Summary |
|---|---|
| `plugins/installed/home/adapter.py` | A-069 disable HTTP proxying for LAN controller traffic; A-070 accept the host's fully-qualified capability name |

### test
| Scope | Summary |
|---|---|
| `tests/e2e/test_phase7_peripherals_home.py` | 44 tests — peripherals unit + **through the real mesh to a real Pi-style node over TCP**, and the home bridge against a **real HTTP controller** |
| `tests/contract/test_ipc_api.py` | +2 tests — `/api/peripherals` |

### docs
| Scope | Summary |
|---|---|
| `docs/PHASE7.md` | **NEW.** Peripherals, home bridge, safety semantics, defects, verification |
| `docs/ANDROID_DEFERRED.md` | **NEW.** Android deferred backlog (owner decision) |
| `docs/DECISIONS.md` | D-073…D-078, A-069…A-070 |

### decision — Android
| Scope | Summary |
|---|---|
| roadmap | **Android final application DEFERRED BY OWNER.** Phase 6 Device Mesh Core = GREEN; the Kotlin node stands as a foundation; the app is built later as its own project. Standing rule: Android-only needs are parked in `docs/ANDROID_DEFERRED.md` and development continues. |

---

## 2026-09-17 — Phase 6: device mesh (+ Android node)

### feat — device mesh
| Scope | Summary |
|---|---|
| `devices/contracts.py` | **NEW.** Manifest, trust tiers, device states, command/result envelopes, HMAC signing, replay window, capability scopes |
| `devices/registry.py` | **NEW.** Authoritative device list: state, capabilities, trust, pairings (secret in the vault), fingerprint |
| `devices/protocol.py` | **NEW.** TCP transport, pairing handshake, connection registry, pending-command futures, cancellation |
| `devices/service.py` | **NEW.** Routing, PTE permissions, idempotency, offline queue with TTL, verification, audit, status |
| `devices/node.py` | **NEW.** Node runtime + `ReferenceHandler` (real files/audio/clipboard) + `python -m devices.node` CLI |
| `android/genie-node/` | **NEW.** Kotlin node: protocol, capabilities, foreground service, Gradle project — *implemented, not compiled (no toolchain on this machine)* |
| `core/db.py` | migration `006_devices` (`devices`, `device_pairings`, `device_commands`) |
| `agents/runtime.py` | `TaskType.DEVICE_ACTION` routes to the mesh with verification surfaced |
| `core/lifecycle.py` | service wiring, router device vocabulary from the registry, transport shutdown |
| `core/ipc/server.py`, `genie.py` | `/api/devices*`, `devices`, `devices-pair`, `devices-unpair`, `devices-trust`, `devices-grant`, `devices-run`, `devices-node`, `devices-selftest` |

### fix — defects found by the Phase 6 tests
| Scope | Summary |
|---|---|
| `devices/protocol.py`, `devices/node.py` | A-065 pre-pairing frames use a shared handshake secret (the `pair_required` reply was unverifiable, so an unpaired node ignored it) |
| `devices/registry.py` | A-066 **a node could promote itself by reconnecting** — the stored trust tier is now authoritative |
| `devices/service.py` | A-067 pairing accepts declared capabilities; an unknown surface defers to the node instead of refusing queued work |
| `computer/state.py` | A-068 `foreground_window()` was read twice; a vanished window crashed the snapshot mid-command |

### test — Phase 6
| Scope | Summary |
|---|---|
| `tests/unit/test_devices_core.py` | 45 tests — contracts, signing, replay window, registry, trust, pairings, routing refusals, **protocol conformance vector** |
| `tests/e2e/test_devices_phase6.py` | 20 tests — **REAL node over REAL TCP**: pairing, execution, verification, default deny, idempotency, offline queue, TTL expiry, replay rejection, cancellation |
| `tests/contract/test_ipc_api.py` | +11 tests — `/api/devices*` |
| `tests/unit/test_computer_phase2.py` | +2 regression tests for the snapshot race |

### docs
| Scope | Summary |
|---|---|
| `docs/PHASE6.md` | **NEW.** Mesh architecture, security model, verification, Android status, deferred items with preserved hooks |
| `android/genie-node/README.md` | **NEW.** Build status and the exact commands to finish it |
| `docs/DECISIONS.md` | D-066…D-072, A-065…A-068 |

---

## 2026-09-17 — Phase 5: skills + teaching mode

### feat — skills
| Scope | Summary |
|---|---|
| `skills/models.py` | **NEW.** Skill schema: status/scope enums, transition machine, `SkillStep`, `Skill`, `${var}` substitution, `validate_skill` |
| `skills/registry.py` | **NEW.** Registration, versioning, rollback, status transitions, ranked `search`/`select` (0.45 threshold), duplicates, compatibility, statistics |
| `skills/runtime.py` | **NEW.** Execution through the shared capability worker: preconditions, state-based waits, verification, takeover, correction evidence, recursion guard |
| `skills/learning.py` | **NEW.** `CandidateDetector`, `Generalizer` (trace + demonstration), `SandboxValidator` (structure → dry run → real run) |
| `skills/service.py` | **NEW.** Daemon facade: selection, execution, learning, teaching, lifecycle, corrections |
| `teaching/recorder.py` | **NEW.** Semantic demonstration recorder with capture-time secret redaction |
| `core/db.py` | migration `005_skills` (`skills`, `skill_corrections`) |
| `director/tools.py` | One generic Needle tool `skill_execute(goal)` → `skill.execute` |
| `agents/runtime.py` | Skill routing + `_dispatch` recursion guard; `skills` bound to the worker |
| `computer/service.py` | Scopes `skill:read` / `skill:execute` |
| `core/lifecycle.py`, `core/ipc/server.py`, `genie.py` | Service wiring, `/api/skills*` + `/api/teaching/*`, `skills*` CLI commands |

### fix — defects found by the Phase 5 tests
| Scope | Summary |
|---|---|
| `skills/registry.py` | A-051 intent is a selection **gate**; A-052 withdrawing the active version clears the active flag; A-054 capabilities read from kwargs **and** context |
| `skills/models.py` | A-053 raw latency/cost accumulators are persisted (statistics were reset on every save/load) |
| `skills/runtime.py` | A-055 neutral `"running"` default status (**every** run previously short-circuited verification and reported failure); A-062 verification substitutes the whole check spec |
| `skills/learning.py` | A-058 `target` stays literal for app/window steps; A-059 inputs reconciled against real placeholders; A-060 inputs derived from created variables; A-061 typed/saved value reuses one variable; retry policy for idempotent steps |
| `teaching/recorder.py` | A-056 a sensitive target field taints the payload (passwords were recorded in clear) |
| `computer/apps.py` | A-057 Windows system directories win over a generic PATH hit (Git's POSIX `notepad` shadowed the real one and hung on stdin) |

### test — Phase 5
| Scope | Summary |
|---|---|
| `tests/unit/test_skills_core.py` | 35 tests — schema, registry, versioning/rollback, lifecycle, selection, duplicates, stats, scope |
| `tests/unit/test_skills_learning.py` | 27 tests — candidate detector, generalizer, validator, *demonstration is not a skill* |
| `tests/unit/test_teaching_recorder.py` | 13 tests — semantic capture, secret redaction, session shape |
| `tests/unit/test_skills_runtime.py` | 21 tests — correction evidence, takeover, preconditions, recursion guard, NEDLE2 surface |
| `tests/e2e/test_skills_phase5.py` | 5 tests — **REAL golden demo**: taught Notepad dated-note workflow replayed at changed window position, size and path |
| `tests/contract/test_ipc_api.py` | +10 tests — skill + teaching HTTP surface |
| `tests/conftest.py` | `SkillService` wired into the test stack |

### docs
| Scope | Summary |
|---|---|
| `docs/PHASE5.md` | **NEW.** Phase 5 architecture, golden demo, defects, verification, §11 routing guard |
| `docs/DECISIONS.md` | D-057…D-065, A-051…A-064 |

### fix — semantic routing guard (post-acceptance correction)
| Scope | Summary |
|---|---|
| `director/semantic_guard.py` | **NEW.** Deterministic post-routing validator: a media/device action requires real media grounding; continuation semantics never qualify, and **high confidence does not override an incompatible intent**. A refused route is withdrawn, escalated, pointed at memory/mission retrieval, audited and published as `POLICY_VIOLATION_BLOCKED` |
| `director/nedle2.py` | Every `classify()` result passes through the guard, so all consumers of NEDLE2 receive validated output |
| `core/orchestrator.py` | Guard applied again at the turn choke point (covers the heuristic fallback too) + `_audit_guard` |
| `director/base.py` | `DirectorDecision.to_dict()` surfaces the guard verdict |
| `director/normalize.py` | A-063 `continue`/`resume` added to the Hinglish command verbs |
| `tests/unit/test_semantic_guard.py` | **NEW.** 60 regression tests incl. the five owner-specified phrases and three real turn-loop tests |

**Result: the NEDLE2 routing smoke is now 12/12** (was 11/12 — `"mera latest project continue
karo"` was answered `media.play` at ~0.99 confidence). The test was not relaxed.

---

## 2026-09-17 — Phase 4: plugins + browser maturity

### feat — plugins
| Scope | Summary |
|---|---|
| `plugins/sdk.py` | **NEW.** Manifest schema + validation, capability specs, host protocol, `PluginAdapter` base |
| `plugins/host.py` | **NEW.** One plugin per process; initialize/health/invoke/shutdown over line-delimited JSON; `--self-test` |
| `plugins/client.py` | **NEW.** Supervision: request timeouts, crash counter, restart, disable-after-limit, structured errors |
| `plugins/registry.py` | **NEW.** Discovery, install/uninstall, enable/disable, permission grants (persisted in `plugins`) |
| `plugins/service.py` | **NEW.** Capability routing, two-gate permissions, audit, status/health |
| `plugins/installed/media/` | **NEW.** Native Windows media-key control + honest observation |
| `plugins/installed/vscode/` | **NEW.** VS Code via the official `code` CLI + workspace inspection |
| `plugins/installed/_test_crash`, `_test_timeout` | **NEW.** Fixtures proving crash isolation and timeout handling |
| `agents/runtime.py` | Plugin-first routing (`media.*` → `plugin.media.*`) with generic fallback |
| `core/lifecycle.py`, `core/ipc/server.py`, `genie.py` | Service wiring, `/api/plugins*`, `plugins` / `plugins-grant` / `plugins-selftest` |

### feat — browser maturity
| Scope | Summary |
|---|---|
| `browser/service.py` | Tabs (list/new/switch/close), condition-based `wait`, select/checkbox/scroll, upload, download with artifacts, dialogs, history, accessibility tree, cookies, session leases |
| `browser/cdp.py` | `/json/new` via PUT with GET fallback; generic `_http_request` |
| `security/injection_guard.py` | **NEW.** Taint tagging, injection pattern scan, high-risk action blocking, secret fencing |
| `computer/service.py`, `planner.py`, `verifier.py` | Scopes, strategy chains and verifiers for all new browser capabilities |
| `core/db.py` | migration `004_plugins` (`plugins`, `browser_downloads`) |

### fix
- Plugin protocol conflated "protocol ok" with "capability ok", so a failing capability lost its real error (now `response.ok` = protocol, `result.ok` = capability).
- Owner bootstrap had no `plugin:*:*` PTE scope, so every plugin invocation was denied at the trust layer.
- The media adapter's `INPUT` union declared only the keyboard member, making `sizeof(INPUT)` too small and `SendInput` fail outright.
- A closed browser tab left a dead CDP connection cached, so every later call blocked until timeout.
- `Page.setDownloadBehavior` (not the browser-level command) is what actually controls the download path; Chrome may still use the profile default and GENIE now reports the real location.
- Chrome blocks gesture-less downloads: `browser.download` now dispatches a trusted input event at the element's own rectangle.
- Single-threaded test HTTP server deadlocked the whole suite because Chrome keeps connections alive (threaded server now).
- Browser capability verifiers were missing, so the "every mutating capability has a verifier" contract test failed.

### decision
- **D-051** separate plugin host processes; **D-052** two-gate permissions; **D-053** plugins are an accelerator, not a requirement; **D-054** condition-based browser waits only; **D-055** downloads/uploads as artifacts; **D-056** untrusted-content fence.
- Assumptions **A-046 … A-050** recorded.

### test
- **242 tests passing, 1 skipped** (was 199): 22 plugin tests (discovery, permissions, timeout, crash isolation, restart, disable/re-enable, real media control, fallback, audit, lifecycle, manifest rules) and 22 browser tests (tabs, waits, forms, upload/download, redirect, history, a11y, cookies, cancellation, leases, injection fixture, audit).

### docs
- **NEW** `docs/PHASE4.md` — plugin architecture, permission model, plugins, browser capability/verification table, injection boundary, exit gate, repo-reuse notes, honest caveats.
- `ACTIVE_WORK.md`, `ROADMAP.md`, `DECISIONS.md`, `README.md`, `REPO_MAP.md` updated.

---

## 2026-09-17 — Phase 3: voice

### feat — voice
| Scope | Summary |
|---|---|
| `voice/devices.py` | **NEW.** Audio device discovery (sounddevice provider + dependency-free WinMM fallback) |
| `voice/vad.py` | **NEW.** Adaptive energy + zero-crossing VAD, speech start/end events, never calibrates on speech |
| `voice/providers.py` | **NEW.** Five contracts (VoiceInput / SpeechToText / RealtimeVoice / TextToSpeech / AudioOutput) + implementations: sounddevice capture, OpenAI-compatible HTTP STT/TTS, **real Windows SAPI**, Gemini Live (key-gated), sidecar/synthetic development providers, WAV I/O |
| `voice/turn_manager.py` | **NEW.** IDLE→LISTENING→THINKING→SPEAKING→INTERRUPTED, barge-in transitions, stop-latency measurement |
| `voice/communication.py` | **NEW.** Communication Brain: length, pace, humour, acknowledgements, interruption handling, proactivity scoring, spoken-text shaping |
| `voice/profiles.py` | **NEW.** Voice profiles with consent-gated cloned voices and safe revocation |
| `voice/metrics.py` | **NEW.** Per-stage latency metrics (memory + `voice_metrics` table) |
| `voice/pipeline.py` | **NEW.** Capture → VAD → STT → turn → brain → TTS, barge-in, honest degradation, bus events |
| `voice/service.py` | **NEW.** Provider selection, status, self-test, profiles, metrics |
| `core/lifecycle.py` | Voice service wired into the daemon (handler = the normal chat path) |
| `core/ipc/server.py` | `/api/voice`, `/api/voice/listen`, `/start`, `/stop`, `/say`, `/barge-in`, `/selftest`, `/devices`, `/metrics`, `/profiles` |
| `genie.py` | `voice-devices`, `voice-status`, `voice-selftest`, `voice-ptt`, `voice-say` |
| `core/db.py` | migration `003_voice` (`voice_metrics`) |
| `core/contracts.py` | voice event names (SPEECH_STARTED/ENDED, VOICE_TRANSCRIPT, VOICE_SPOKEN, BARGE_IN, …) |

### fix
- VAD calibrated its noise floor on frames that were already speech, so a person talking immediately was learned as noise (A-038).
- SAPI through raw ctypes vtable calls crashed the process (wrong indices — ISpVoice inherits ISpEventSource ← ISpNotifySource); replaced with the official COM typelib (A-037).
- SAPI created per thread meant `stop()` purged a different voice than the one speaking → a hanging test suite; now a single worker thread owns the COM object and all commands are queued (A-039/A-040).
- Async TTS was treated as finished as soon as synthesis started, so the turn left SPEAKING immediately and barge-in had nothing to interrupt (A-041).
- `speak()` returned a fabricated success result instead of the provider's real result (including `synthetic`).
- Cloned voice profiles could be added without consent; consent is now enforced on activation, and revocation falls back to the default profile (A-042).
- Profile persistence crashed on the derived `usable` field when reloading.
- The pipeline assumed dict handler results; it now normalises dict / ActionResult / `to_dict()` (A-043).
- Turn manager lost the turn when speaking started without a preceding listen, so interrupted turns were not recorded.

### decision
- **D-045** voice providers are optional and swappable; **D-046** dedicated COM owner thread; **D-047** barge-in through the turn manager for every provider; **D-048** speech never decides anything.
- Assumptions **A-037 … A-043** recorded.

### test
- **185 tests passing** (was 141): new voice unit suite (VAD, turn manager, communication brain, profiles, metrics, providers) and a real-machine e2e suite (microphone capture, VAD on live audio, real speech output, verified action through the voice pipeline, Hinglish routing, trust/audit not bypassed, barge-in during real speech, conversation resume, honest degraded mode, selftest).

### docs
- **NEW** `docs/PHASE3.md` — pipeline, provider contracts, barge-in, communication brain, Hinglish table, measured latency, exit gate, honest caveats.
- `ACTIVE_WORK.md`, `ROADMAP.md`, `DECISIONS.md`, `README.md`, `REPO_MAP.md` updated.

---

## 2026-09-17 — Phase 2: real computer engine (verified actions)

### feat — computer engine
| Scope | Summary |
|---|---|
| `computer/windows_api.py` | **NEW.** Win32 via ctypes: window/monitor enumeration, focus with foreground-lock fallbacks, SendInput (unicode-safe) keyboard+mouse, clipboard, GDI screen/window capture, idle detection, full API prototypes |
| `computer/state.py` | **NEW.** ComputerState snapshots: foreground, windows, monitors, processes, audio, clipboard, RAM/CPU |
| `computer/apps.py` | **NEW.** Installed-app discovery (registry App Paths + Start Menu + Program Files, 194 apps in 0.01 s) with launch-method cache and honest "not installed" failures |
| `computer/audio.py` | **NEW.** Core Audio (IAudioEndpointVolume) via ctypes COM — real volume **read**, set, mute (read-back is what makes volume verifiable) |
| `computer/uia.py` | **NEW.** UI Automation provider (optional pywinauto, lazily imported): find/invoke/set_value/get_value/focus/tree with a handle registry |
| `computer/input.py` | **NEW.** Keyboard/mouse with **USER_TAKEOVER** detection (GetLastInputInfo) |
| `computer/locks.py` | **NEW.** Exclusive desktop lease + takeover-aware pause + stale-lease recovery |
| `computer/files.py` | **NEW.** Workspace-first filesystem: atomic writes, recycle-bin delete (state-verified), find/copy(hash)/move/list |
| `computer/shell.py` | **NEW.** Hardened shell: safe allowlist tier, forbidden-pattern policy, timeouts, captured results |
| `computer/workspace.py` | **NEW.** GENIE workspace, path-escape prevention, passive isolation detection, resource profile |
| `computer/planner.py` | **NEW.** Per-capability strategy chains following the automation priority |
| `computer/verifier.py` | **NEW.** 33 verifiers + read-only set; **no verifier ⇒ never verified** |
| `computer/executor.py` | **NEW.** PLAN → ACT → OBSERVE → VERIFY → RECOVER loop with desktop lock, takeover checks, cancellation, per-attempt verification rows |
| `computer/events.py` | **NEW.** Polling event sources → bus (app opened/closed, window focused, file changed, takeover) |
| `computer/service.py` | **REWRITTEN** around the executor: PTE gate → verified execution → audit; 65 capabilities with scopes |
| `browser/cdp.py` | **NEW.** Stdlib WebSocket + Chrome DevTools Protocol client (no Node/puppeteer) |
| `browser/service.py` | **NEW.** BrowserProvider: navigate (render-verified), DOM query/click/type, extract, screenshot, tabs |
| `tools/benchmark.py` | **NEW.** Records NEDLE2 routing latency, command-to-action latency, verification cost, RAM/CPU |

### fix
- 64-bit ctypes handle truncation caused an access violation in clipboard writes → all Win32 calls now declare prototypes (A-026).
- `SHFileOperationW` reported failure after actually recycling the file → delete success is now state-verified (A-027).
- `Get-Date -Format o` was refused as "destructive" → word-boundary matching ignores `-Parameter` forms (A-028).
- `uia.focus/invoke/click` results lacked the element handle, so verification reported "no element handle" (A-029).
- `uia.set_value` used a pywinauto method that UIA wrappers do not have → now tries ValuePattern → set_text → focus+type_keys.
- A browser already holding the profile silently swallowed new launches → fresh profile + next port per attempt (A-034).
- Browser navigation reported success before SPAs rendered → verification now waits for rendered content (A-035).
- Hinglish verb-last order confused the router → verb-first normalisation (A-036).
- `input.hotkey`/`key`/`click` had no verifier and could never complete → delivery verification registered.
- Action failures lost their specific reason ("action failed") → the refusal/error reason is now surfaced.
- `computer.state` and other read-only payloads were filtered by an allowlist → full payload minus oversized internals.
- Browser profile no longer leaks into the working directory; the provider rebinds to the workspace profile.
- Removed the superseded Phase 1 `computer/windows.py` adapter.

### decision
- **D-040** built-in CDP browser provider (PinchTab optional). **D-041** UIA as an optional lazy provider.
- **D-042** COMPLETED requires verification — an unverifiable capability can never complete a mission.
- **D-043** routable NEDLE2 catalogue kept small (measured: 13 tools → 12/12, 20 tools → 9–11/12).
- **D-044** deterministic director fast path for web addresses.
- Assumptions **A-026 … A-036** recorded.

### test
- **141 tests passing** (was 86): new unit suite (discovery, planner, verifiers, workspace, shell safety, files),
  real non-dry-run e2e on Windows (app/window/volume/clipboard/shell/files/UIA/input/capture/takeover/cancel/audit),
  a golden multi-step mission, and NEDLE2 routing tests for the owner's local commands.

### docs
- **NEW** `docs/PHASE2.md` — full Phase 2 reference: loop, capabilities, discovery, takeover, isolation, browser, measured performance, exit gate, honest caveats.
- `ACTIVE_WORK.md`, `ROADMAP.md`, `DECISIONS.md`, `NEDLE2.md`, `README.md`, `REPO_MAP.md` updated.

---

## 2026-09-16 — Real NEDLE2 (Cactus Needle 2) validated

### feat — director
| Scope | Summary |
|---|---|
| `director/needle_runtime.py` | **NEW.** Official runtime provisioning: `cactus-needle` package, official engine `libneedle.dll` from `Cactus-Compute/needle2`, sha256 manifest, `verify()`, `activate()`, GENIE-managed dir `%LOCALAPPDATA%\GENIE\runtime\needle2\`. Falls back to a direct HTTPS download of the same official wheel when the HF xet bridge returns a 0-byte file (A-023) |
| `director/nedle2.py` | **REWRITTEN** around the real engine. Mirrors the official harness: `reset()` per request, reads `function_calls`/`validation`/`confidence`, drops `ungrounded`/`negation` calls, confidence gate 0.4, escalates on refusal. Adds `smoke_test()` and `large_catalogue_probe()` |
| `director/tools.py` | **NEW.** GENIE tool catalogue as official `@needle.tool` functions (13 tools) + single `TOOL_TO_TASK` mapping + device canonicalisation/inference |
| `director/normalize.py` | **NEW.** Hinglish/Hindi command normalisation in front of the director; values keep original casing |
| `core/lifecycle.py` | Boot provisions NEDLE2 in a background thread, runs the routing smoke test, swaps the director in when it passes; `needle` block added to `/api/status` |
| `genie.py` | New commands: `needle-setup` (provision + verify + smoke) and `needle-smoke [--large N]` |
| `core/config.py` | `director.needle.confidence_threshold`, `runtime_dir`, `auto_provision`; runtime default is now `python` |

### fix
- Device arguments were emitted as canonical ids (`phone_main`), which the model's grounding check rejected as *ungrounded* → tools now request the user's own word and GENIE canonicalises (A-021).
- Hinglish normalisation lowercased user values, breaking song/file/app names → case is preserved for unmapped tokens.
- Logs went to stdout, corrupting JSON output of CLI commands → logs now go to stderr (A-025).
- `genie.py needle-setup` crashed passing its argparse namespace to the smoke command.

### Verified (real engine, not mocked)
```text
package cactus-needle 2.0.15 · engine 2.0.4 · sha256 2955e284…
runtime dir  %LOCALAPPDATA%\GENIE\runtime\needle2\   (auto-provisioned)
routing smoke 12/12 stable   ·   large catalogue 73 tools still routes
E2E: "Chrome kholo" -> application.open -> COMPLETED
     "volume 30"    -> system.volume.set -> COMPLETED
     "phone ka next song" -> media.next -> clean Phase-6 failure
tests: 86 passed (real-Needle tests skip, never fake, when the runtime is absent)
```

### decision
- **D-035** answered & implemented (official runtime, automatic provisioning, `DirectorProvider` only).
- **D-038** Hinglish normalisation layer in front of the director.
- **D-039** a NEDLE2 tool call is a routing decision only — never execution, never completion.
- Assumptions **A-018 … A-025** recorded.

### docs
- **NEW** `docs/NEDLE2.md` — provisioning, contract, catalogue, Hinglish layer, validated behaviour table, honest caveats.
- `ACTIVE_WORK.md`, `ROADMAP.md`, `REPO_MAP.md`, `DECISIONS.md` updated. Phase 1 is green **only** because the real engine now passes routing tests.

---

## 2026-09-16 — Phase 1 implemented (working code)

### feat — core runtime
| Scope | Summary |
|---|---|
| `core/config.py` | JSON config with `GENIE_*` env overrides, data-dir bootstrap, user config persistence |
| `core/logging_setup.py` | JSON structured logs with trace binding, rotating file + console |
| `core/events.py` | Event bus: wildcard subs, worker pool, DLQ, ring buffer for UI streaming |
| `core/db.py` | SQLite WAL layer, thread-local connections, forward-only migration runner; tables: memory, FTS5, missions, steps, audit, grants, provider_state, artifacts, locks |
| `core/contracts.py` | All shared types + mission transition table + canonical event names |
| `core/orchestrator.py` | Full turn loop: director → memory → mission → capability → verify → audit |
| `core/lifecycle.py` | Daemon boot in spec §8.18 order, interrupted-mission recovery, status |
| `core/ipc/server.py` | REST + SSE local API; serves the UI bundle from `ui/web` |

### feat — security (Phase 1 requirement, D-007)
| Scope | Summary |
|---|---|
| `security/vault.py` | Secrets vault; DPAPI on Windows with scrypt+XOR fallback; `secret://` refs only; never in logs/context |
| `security/trust.py` | PTE: default deny, one_time/session/standing/conditional grants, scope wildcards, destructive-action confirmation matrix, every decision audited |
| `security/audit.py` | Append-only hash-chained audit log with tamper detection |
| `security/policy.py` | Model Policy Registry; `SECRET`/`RESTRICTED` never leave the machine |

### feat — memory & missions
| Scope | Summary |
|---|---|
| `memory/service.py` | Write pipeline with dedupe, **supersede (never overwrite)**, correct/forget/pin/history, privacy-scope reads, offline FTS + recency ranking, vector-cache hooks |
| `missions/service.py` | Mission state machine (validated transitions), steps, snapshot/resume, cancel (releases locks), cost accounting, `LockService` with lease expiry |

### feat — models & providers (D-023)
| Scope | Summary |
|---|---|
| `config/providers.json` | 10 predefined providers: DeepSeek, Gemini, OpenRouter, Together, GLM, Kimi, MiniMax, Tencent Cloud, TokenHub, Mock |
| `models/registry.py` | Runtime-editable provider/model registry persisted to `data/providers.user.json` — **no source changes to add a model** |
| `models/gateway.py` | Selection: policy → capability → health → cost/priority; automatic failover; budget enforcement; cost accounting; `test_connection` |
| `models/providers/` | `openai_chat`, `openai_responses`, `mock` adapters (stdlib `urllib`) |
| `models/health.py` | Circuit breaker with persisted state and cooldown/half-open |

### feat — director (D-024)
| Scope | Summary |
|---|---|
| `director/base.py` | `DirectorProvider` abstraction + `DirectorDecision` schema + strict validation of model output |
| `director/nedle2.py` | Cactus Needle 2 integration (cli/python/http runtime detection, JSON extraction, timeout) with documented heuristic fallback |
| `director/heuristics.py` | Deterministic Hinglish fast path: volume, media (device-aware), app open/close, memory proposal/query, escalation to remote models |

### feat — computer, agents, context
| Scope | Summary |
|---|---|
| `computer/service.py` | Capability dispatch with PTE gate, dry-run, verification flag, audit; app open/close, volume, processes, shell |
| `computer/windows.py` | Windows adapter: launch/close, tasklist, shell with timeout, Core Audio volume (+ key fallback), system capability detection |
| `agents/runtime.py` | Agent lifecycle with step/cost/wall budgets, cancellation, plus the first capability worker |
| `context/builder.py` | Budgeted context packet; safety block never trimmed; retrieval limited to top-k |

### feat — UI (D-031)
| Scope | Summary |
|---|---|
| `ui/web/*` | Basic ChatGPT-like UI: sidebar (conversations, active mission, providers, devices, activity), chat area, dry-run toggle, provider/model manager, settings drawer, SSE event feed |
| `ui/electron/*` | Electron shell that loads the daemon UI; no business logic; external links go to the system browser |

### fix
- `computer/service.py` — capability existence is now validated before the permission check (unknown capability reported as *unsupported*, not *denied*).
- Removed `computer/windows/` and `agents/workers/` empty packages that shadowed real modules (`A-016`, module import failure).

### decision
- **D-029** 113 micro-modules consolidated into real packages (module count is not a goal).
- **D-030** Zero third-party runtime dependencies (stdlib only).
- **D-031** UI is a replaceable thin client.
- **D-032** Mock provider guarantees offline/degraded operation.
- **D-033** Scaffolding generator removed.
- **D-034** License/provenance files preserved when adapting code.
- Assumptions **A-001 … A-017** recorded in `DECISIONS.md`.
- Owner answers recorded for **D-001, D-022 … D-028**.

### spec
- `GENIE_MASTER_SPEC_v1.md` status changed from *FROZEN* to **BASELINE / CONTROLLED SPEC**; §0.4 rewritten into two-level change control (implementation detail vs Frozen Invariants).

### test
- **61 tests passing**: unit (events, security, memory, missions, gateway, director, context), contract (computer, IPC API), e2e (`"Chrome kholo"` full loop with audit + verification assertions).
- Live verification: `python genie.py selftest`, plus daemon + HTTP API exercised with curl.

---

## 2026-09-16 — Phase 0 (earlier today)

| Type | Scope | Summary |
|---|---|---|
| `spec` | all | GENIE MASTER SPEC v1.0 — 88 sections + cross-cutting addendum merged into 8 permanent layers; 12 frozen invariants; appendices A–F |
| `docs` | `LICENSE_MATRIX.md` | Verified license scan of 23 uploaded repos (GPL-3.0 ComfyUI, AGPL-3.0 MiroFish/VoiceStudio, Sustainable Use n8n, commercial-threshold HeyGem.ai, **no license** Open-Higgsfield-AI) |
| `docs` | `ARCHITECTURE.md`, `CONTRACTS.md`, `REPO_MAP.md`, `ROADMAP.md`, `GENIE.md`, `MEMORY.md`, `SECURITY.md`, `DEVICES.md`, `PROVIDERS.md`, `PLUGINS.md` | Phase 0 documentation set |
| `decision` | `DECISIONS.md` | Decision log started |

### Notes / gotchas
- `Kronos-master (1).zip` is a duplicate of `Kronos-master.zip`.
- `munder-difflin` has a separate asset license (`LICENSE-ASSETS`).
- n8n zip is the `master` branch; non-main branches are unlicensed and `.ee` files need an enterprise license.
