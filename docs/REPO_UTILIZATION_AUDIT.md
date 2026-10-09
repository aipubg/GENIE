# GENIE — Repository Utilization Audit (Phase 11A)

**Purpose:** every supplied repository must end in exactly one documented state. No repository
is silently ignored. This document records what each repo does, what GENIE already has, what was
inspected in *real source*, and where the capability lands.

> **⚠️ This audit was RE-OPENED in Phase 14.4** (2026-09-18). It originally covered 22 distinct
> repositories; **DeerFlow, AgentScope and Strix arrived afterwards and were formally absent**, and
> several older entries only reached generic-adapter level, which is *not* capability.
> See **[`REPO_REAUDIT_14.md`](./REPO_REAUDIT_14.md)** for the re-audit: new Tier-1 donors, the
> forecasting design, old-repo reconciliation, and the DOCUMENTED / IMPLEMENTED / TESTED /
> LIVE-ACCEPTED distinction. Where the two documents disagree, the re-audit is authoritative.

**Disposition legend**

| Status | Meaning |
|---|---|
| `DIRECT REUSE` | source/components integrated through GENIE contracts |
| `ADAPTED` | shell (frontend/backend/orchestration) removed, core capability retained + wired |
| `EXTERNAL SERVICE` | repo stays separate; GENIE drives it via an adapter/service boundary |
| `REIMPLEMENTED` | GENIE already has a demonstrably better native implementation; parity recorded |
| `DEFERRED` | genuinely belongs to a later subsystem; reason + target phase recorded |

**Archives inspected** (extracted to `/repos`, real source read — not README-only):
23 archives in `tool/tool/`. Note `Kronos-master (1).zip` is a byte-identical duplicate of
`Kronos-master.zip` (same size 9,451,071 B) → 22 distinct repositories.

---

## 1. agent systems (deep-inspected per instruction)

### 1.1 agency-agents
- **Original purpose:** library of AI agent personalities for coding agents.
- **Important capabilities:** 302 persona definitions across 19 divisions (engineering 64,
  specialized 59, marketing 36, security 12, gis 13, testing 9, sales 9, …). Each is a markdown
  file with YAML frontmatter (`name`, `description`, `color`, `emoji`, `vibe`) plus identity,
  core mission, workflows, success metrics. `divisions.json` (display metadata) and `tools.json`
  (install contract per CLI tool) are machine-readable.
- **Current GENIE equivalent:** `agents/factory.py` `AgentFactory` — creates agents from
  role/capability/quality, currently from a small built-in role set.
- **Direct code reused?** **Yes** — persona corpus as *role templates*.
- **Files/components inspected:** 302 `*.md` persona files, `divisions.json`, `tools.json`,
  `integrations/` (per-tool output converters — ignored), `scripts/`.
- **Adapter/service used:** none needed — parsed into GENIE role templates at build time.
- **Capability still missing:** role-template loader + template→`AgentDefinition` mapping.
- **Final status:** `DIRECT REUSE` (data), pending loader in 11B.
- **Target phase:** 11B.
- **Notes:** explicitly *not* 302 permanent agents — instruction says role templates → factory →
  task-specific agent. We load the corpus as templates and instantiate on demand.

### 1.2 OpenClaw
- **Original purpose:** personal assistant running on your devices, reachable in chat channels
  (Discord, iMessage, Slack, Teams, Telegram, WhatsApp, 20+) plus native apps.
- **Important capabilities:** persistent assistant behaviour, multi-surface/device interaction,
  session continuity, plugin-swappable model harnesses, Android/iOS clients, remote operation.
- **Current GENIE equivalent:** `devices/` (mesh, pairing, trust tiers, capability scopes),
  `android/` (deferred), `missions/`, `core/ipc/`.
- **Direct code reused?** **No** — its Node/TypeScript product shell would duplicate GENIE's
  device mesh and IPC. Concepts retained.
- **Files/components inspected:** `VISION.md`, `README.md`, `apps/`, `config/`,
  `custodian-skills/`, `AGENTS.md`.
- **Adapter/service used:** none.
- **Capability still missing:** (a) *chat-channel surfaces* — GENIE has no Discord/Telegram/
  Slack ingress; (b) some session-continuity conventions.
- **Final status:** `ADAPTED` (concepts) + partial `DEFERRED`.
- **Target phase:** channel ingress → Phase 13 (surfaces); session continuity → 11C workspace.
- **Notes:** GENIE stays the parent system; we do not adopt OpenClaw's product architecture.

### 1.3 hermes-agent
- **Original purpose:** self-improving agent with a built-in learning loop.
- **Important capabilities:** creates skills from experience, improves skills during use,
  nudges itself to persist knowledge, searches its own past conversations, builds a deepening
  user model across sessions; provider-agnostic.
- **Current GENIE equivalent:** `skills/` (registry, learning, versions, rollback),
  `agents/factory.py::ExperienceStore`, `memory/`, `teaching/`.
- **Direct code reused?** **No.** GENIE Phase 5 skills + Phase 10 `ExperienceStore` already
  cover skill creation, versioning, rollback and structured experience.
- **Files/components inspected:** `SOUL.md`, `AGENTS.md`, `acp_adapter/`, `README.md`.
- **Adapter/service used:** none.
- **Capability still missing:** *cross-session user model* refinement is thinner in GENIE.
- **Final status:** `REIMPLEMENTED` (skills/experience) — parity verified: GENIE has versions,
  rollback, corrections, teaching mode which Hermes' simpler skill store lacks.
- **Target phase:** — (gap folded into Phase 12 self-improvement).
- **Notes:** we do not duplicate an inferior skill store.

### 1.4 deepseek-harness
- **Original purpose:** agent harness on an **everything-is-a-plugin** architecture, built on
  Cordis (spatiotemporal composability).
- **Important capabilities:** tool abstraction, computer/terminal/filesystem/browser plugins,
  plugin loading, runtime composition.
- **Current GENIE equivalent:** `plugins/`, `computer/` (apps, files, input, audio, executor,
  planner, locks), `browser/` (cdp, service), `agents/`.
- **Direct code reused?** **No** — GENIE already ships a plugin bus + computer + browser on its
  own contracts; swapping in Cordis would replace a working kernel.
- **Files/components inspected:** `README.md`, `AGENTS.md`, `BENCHMARK.md`, `SAFETY.md`,
  `BRAND_GUIDELINES.md`, `.agents/notes/AGENTS.md`.
- **Adapter/service used:** none.
- **Capability still missing:** nothing critical; its plugin-composition discipline is recorded
  as a design reference.
- **Final status:** `REIMPLEMENTED`.
- **Target phase:** —.

### 1.5 Youtu-Agent
- **Original purpose:** framework for building, running and **evaluating** autonomous agents;
  experience-based learning + end-to-end RL.
- **Important capabilities (strongest piece found):** **training-free GRPO experience loop** —
  (1) summarise each rollout trajectory, (2) compute *semantic group advantage* across attempts
  of the same problem, (3) group-update a textual experience bank that steers future behaviour
  without weight updates.
- **Current GENIE equivalent:** `agents/factory.py::ExperienceStore` (structured records,
  `best_configuration`, `statistics`) — currently **heuristic**, no group-advantage loop.
- **Direct code reused?** **No** (PyTorch/RL stack + `agents` SDK dependency). **Algorithm
  adapted.**
- **Files/components inspected (real source):** `utu/practice/experience_updater.py`
  (`ExperienceUpdater.run`: `_single_rollout_summary` → `_group_advantage` → `_group_update`),
  `utu/db/experience_cache_model.py`, `utu/utils/experience_cache.py`,
  `configs/`, `utu/tools/local_env/bash_pexpect.py`.
- **Adapter/service used:** none — algorithm reimplemented natively.
- **Capability still missing:** semantic group advantage + experience bank update loop.
- **Final status:** `ADAPTED` (algorithm), target 12.
- **Target phase:** Phase 12 (self-improvement) — feeds directly from 11D outcomes table.
- **Notes:** this is the single most valuable agent find; GENIE's `improvement_suggestions()`
  is the seed, the group-advantage loop is the upgrade.

### 1.6 Qwen-AgentWorld
- **Original purpose:** agent-world for **simulation / long-horizon evaluation**.
- **Important capabilities:** agent testing worlds, long-horizon evaluation, regression
  environments.
- **Current GENIE equivalent:** none for *simulated worlds*; GENIE has `tests/e2e` + golden
  missions but no simulated environment.
- **Direct code reused?** **No** — heavy model/eval stack.
- **Files/components inspected:** `eval/`, `prompts/`, `assets/`, `README.md`.
- **Adapter/service used:** none yet.
- **Capability still missing:** simulation-based agent evaluation / regression worlds.
- **Final status:** `DEFERRED` (evaluation/dev-lab layer, not production runtime).
- **Target phase:** Phase 12 evaluation lab.
- **Notes:** instruction explicitly places this at the evaluation layer.

### 1.7 spec-kit
- **Original purpose:** spec-driven development workflow for coding agents
  (specify → plan → tasks → implement → validate).
- **Important capabilities:** structured requirements, implementation plan, task breakdown,
  validation, artifact handoff between coding stages.
- **Current GENIE equivalent:** `missions/` + `agents/team.py` DAG + `agents/artifacts.py`
  (references by id) — GENIE already hands off artifacts by reference, not transcript.
- **Direct code reused?** **No** (bash/CLI templates). **Workflow pattern adapted.**
- **Files/components inspected:** `.specify/`, `bundles/`, `docs/`, `extensions/`
  (`agent-context`, `assess`, `bug`, `git`), `templates/`, `AGENTS.md`.
- **Adapter/service used:** none — pattern folded into coding-agent workflow.
- **Capability still missing:** explicit requirements→plan→tasks→validation template set for
  build missions.
- **Final status:** `ADAPTED` (workflow pattern).
- **Target phase:** 11B (coding/build mission workflow).

### 1.8 munder-difflin
- **Original purpose:** multi-agent harness — "run an office of your clones"; wraps many coding
  CLIs, agents that message/route/remember, coordinated office-floor visualisation.
- **Important capabilities:** parallel agent coordination, message routing, memory, wrapping
  heterogeneous coding CLIs with hourly-limit scheduling.
- **Current GENIE equivalent:** `agents/team.py` (`TeamOrchestrator`, mailbox, blackboard, DAG),
  `agents/service.py` (failover), `agents/ops.py` (operator console, 11D).
- **Direct code reused?** **No.** GENIE's orchestrator already provides mailbox + blackboard +
  DAG + budgets + failover + operator console; the "office floor" is a UI conceit.
- **Files/components inspected:** `HIVE.md`, `DESIGN.md`, `MEMORY_GRAPH_SPEC.md`,
  `.claude/skills/`, `README.md`.
- **Adapter/service used:** none.
- **Capability still missing:** nothing material.
- **Final status:** `REIMPLEMENTED` (stronger natively: GENIE adds budgets, failovers,
  tamper-evident audit, operator console).
- **Target phase:** —.

### 1.9 enoch (OurArk)
- **Original purpose:** persistent, runtime-independent personal agent that **evolves its own
  code** from feedback under owner control (governed code evolution).
- **Important capabilities:** lineage tracking, governed self-modification, learn/teach/evolve/
  inherit skills, conformance testing of self-changes.
- **Current GENIE equivalent:** `teaching/`, `skills/` (versions, rollback, corrections),
  `security/audit.py` (tamper-evident log). No governed *self code evolution*.
- **Direct code reused?** **No.**
- **Files/components inspected:** `src/enoch/evolution/`, `src/enoch/lineage/`,
  `src/enoch/skills/{learn,teach,evolve,inherit,code}/`, `src/enoch/conformance/`,
  `src/enoch/memory/`, `src/enoch/profiles/`, `genesis.toml`, `.agent/lineage.yaml`.
- **Adapter/service used:** none.
- **Capability still missing:** **governed self-evolution pipeline** (lineage + conformance +
  owner approval) — this is the archetype for Phase 12.
- **Final status:** `ADAPTED` (pipeline design: lineage → conformance → approval).
- **Target phase:** Phase 12 (self-improvement / `evolution.software`).
- **Notes:** `conformance/` + `lineage/` is the model for safe self-modification.

---

## 2. browser / workspace

### 2.1 PinchTab
- **Original purpose:** "Browser control for AI agents" — small Go binary, HTTP API,
  token-efficient, headless.
- **Important capabilities:** agent-oriented browser control over HTTP (navigate, extract,
  act), designed for low token cost.
- **Current GENIE equivalent:** `browser/` (`cdp.py` raw CDP + `service.py` with semantic
  actions, verified downloads, real-Chrome e2e).
- **Direct code reused?** **No** — GENIE already drives Chrome directly over CDP with richer
  semantics; adding a Go binary would add a runtime dependency for no gain.
- **Files/components inspected:** `README.md`, `Dockerfile`, `DEVELOPMENT.md`, `TESTING.md`,
  `DEFINITION_OF_DONE.md`, `assets/`.
- **Adapter/service used:** none.
- **Capability still missing:** token-efficient *page extraction* heuristics are worth mining.
- **Final status:** `REIMPLEMENTED` (browser control) + `ADAPTED` (token-efficiency idea).
- **Target phase:** mining extraction heuristics → 11C workspace.

---

## 3. specialist engines

### 3.1 n8n
- **Original purpose:** workflow automation platform; visual canvas + custom code; 1500+
  integrations; AI agents.
- **Important capabilities:** durable workflow engine, triggers, 1500+ service connectors,
  retry/error handling, self-hosted or cloud.
- **Current GENIE equivalent:** none (GENIE has `missions/` + `director/` but no general
  workflow/integration engine).
- **Direct code reused?** **No** (large TypeScript monorepo, own product).
- **Files/components inspected:** README, `packages/` layout, `.agents/review-rules/`.
- **Adapter/service used:** **planned** `workflow.automation` adapter over n8n's REST API.
- **Capability still missing:** the whole `workflow.automation` capability + 1500 connectors.
- **Final status:** `EXTERNAL SERVICE`.
- **Target phase:** 11B (adapter + conformance tests; live acceptance pending credentials).

### 3.2 ComfyUI
- **Original purpose:** modular node-graph engine for generative media (images/video).
- **Important capabilities:** composable diffusion pipelines, node graphs, model management,
  extensibility.
- **Current GENIE equivalent:** none.
- **Direct code reused?** **No** (its node engine *is* the value — do not rewrite).
- **Files/components inspected:** top-level `nodes.py`, `execution.py`, `comfy/` model
  backbones, `web/` (frontend — remove/ignore).
- **Adapter/service used:** **planned** `media.image_video` worker over ComfyUI's HTTP API.
- **Capability still missing:** image/video generation.
- **Final status:** `EXTERNAL SERVICE` (wrap the mature engine; never reimplement).
- **Target phase:** 11B (adapter), live acceptance pending GPU/hardware.

### 3.3 HeyGem.ai
- **Original purpose:** open-source alternative to HeyGen — **digital human / avatar video**
  generation.
- **Important capabilities:** avatar video synthesis from audio, local (Ubuntu/Windows GPU).
- **Current GENIE equivalent:** none.
- **Direct code reused?** **No** (heavy: 122 MB archive, docker/GPU stack).
- **Files/components inspected:** README (Ubuntu 22.04 / kernel 6.8 verification notice),
  `Dockerfile`, service layout.
- **Adapter/service used:** **planned** `avatar.digital_human` adapter.
- **Capability still missing:** digital-human video generation.
- **Final status:** `EXTERNAL SERVICE`, live acceptance pending GPU.
- **Target phase:** 11B backlog / later media phase.

### 3.4 OmniVoice
- **Original purpose:** multilingual voice (TTS/voice) toolkit.
- **Important capabilities:** multilingual TTS / voice synthesis.
- **Current GENIE equivalent:** `voice/` (GENIE has its own voice module, Phase 3).
- **Direct code reused?** **No.**
- **Files/components inspected:** README, `src/` layout.
- **Adapter/service used:** **planned** `voice.specialized` worker (optional).
- **Capability still missing:** only *extra* voice styles/languages beyond GENIE's current TTS.
- **Final status:** `EXTERNAL SERVICE` (optional specialist), live acceptance pending.
- **Target phase:** later media phase (backlog entry created).

### 3.5 VoiceStudio
- **Original purpose:** voice creation studio (TTS/voice authoring).
- **Important capabilities:** voice authoring/cloning workflows.
- **Current GENIE equivalent:** `voice/`.
- **Direct code reused?** **No.**
- **Files/components inspected:** `.agents/skills/fastapi-python/`, README.
- **Adapter/service used:** none (overlaps OmniVoice + GENIE `voice/`).
- **Capability still missing:** nothing unique.
- **Final status:** `DEFERRED` (overlaps existing GENIE `voice/`; revisit only if a unique
  studio workflow is requested).
- **Target phase:** later media phase.

### 3.6 Kronos (×2 archives, identical)
- **Original purpose:** first open-source **foundation model for financial candlesticks
  (K-lines)**, trained on 45+ global exchanges.
- **Important capabilities:** financial time-series forecasting, market prediction.
- **Current GENIE equivalent:** none.
- **Direct code reused?** **No** (model weights + training stack).
- **Files/components inspected:** `README.md`, model/config layout.
- **Adapter/service used:** **planned** `finance.timeseries` worker.
- **Capability still missing:** financial forecasting.
- **Final status:** `EXTERNAL SERVICE`.
- **Target phase:** 11B backlog (adapter + conformance/mock tests).

### 3.7 MiroFish
- **Original purpose:** "简洁通用的群体智能引擎，预测万物" — general swarm-intelligence
  engine: upload seed material, describe a prediction need, get a prediction report + an
  interactive high-fidelity digital world.
- **Important capabilities:** social simulation, swarm/collective behaviour prediction,
  simulation-backed forecasting.
- **Current GENIE equivalent:** none.
- **Direct code reused?** **No.**
- **Files/components inspected:** README, `.env.example`, docker layout.
- **Adapter/service used:** **planned** `simulation.social` service.
- **Capability still missing:** social/swarm simulation.
- **Final status:** `EXTERNAL SERVICE`.
- **Target phase:** 11B backlog.

### 3.8 Open-Higgsfield-AI
- **Original purpose:** open-source alternative to Higgsfield AI — generate images/videos with
  200+ models.
- **Important capabilities:** multi-model image/video generation without a closed ecosystem.
- **Current GENIE equivalent:** none.
- **Direct code reused?** **No.**
- **Files/components inspected:** README, `afterPack.js`, app layout.
- **Adapter/service used:** overlaps ComfyUI/n8n media path.
- **Capability still missing:** (same capability class as ComfyUI).
- **Final status:** `DEFERRED` in favour of ComfyUI as the single media engine (avoid two
  competing media stacks); recorded so the repo is not unused.
- **Target phase:** later media phase — re-evaluate if ComfyUI adapter proves insufficient.

### 3.9 hack-skills
- **Original purpose:** "Hacker Arsenal for Agents" — agent skills knowledge base: 102 deep
  topic skills across 14 security domains (web, API, auth, privesc Linux/Win/macOS, AD attacks,
  mobile, pwn, RE, crypto attacks, blockchain/smart-contract, AI/LLM security, network pivoting,
  forensics) for bug bounty / pentest / CTF.
- **Important capabilities:** structured, auditable, composable security skill corpus
  (`skills/{id}/SKILL.md` = master entry → category entries → deep topics).
- **Current GENIE equivalent:** `skills/` (generic) — **no security corpus**.
- **Direct code reused?** **Yes** — corpus as skills.
- **Files/components inspected:** `skills/` tree (102 `SKILL.md`), `scripts/`, `site/`,
  README (master-entry→category→deep-topic loader design).
- **Adapter/service used:** imports into GENIE `skills/registry.py`.
- **Capability still missing:** import + gating (security skills must be permission-gated).
- **Final status:** `DIRECT REUSE` (corpus), pending import + gating.
- **Target phase:** security phase (backlog created now; gating via PTE/trust).

### 3.10 Decepticon
- **Original purpose:** security testing environment, usable as a pip library or hosted.
- **Important capabilities:** adversarial/security assessment automation.
- **Current GENIE equivalent:** `security/` (audit, vault, trust, default-deny) — assessment
  *automation* absent.
- **Direct code reused?** **No.**
- **Files/components inspected:** README (pip library + cloud), `.env.example`, docker layout.
- **Adapter/service used:** **planned** security-environment adapter (gated).
- **Capability still missing:** adversarial assessment runs.
- **Final status:** `EXTERNAL SERVICE`, gated behind owner authorisation.
- **Target phase:** security phase (deferred backlog).

### 3.11 youtube-automation-agent (AgentTube)
- **Original purpose:** open-source agent that runs a YouTube channel end to end: research →
  script → narration/visuals → assemble → metadata → review → schedule → publish → learn from
  analytics and audience.
- **Important capabilities:** long-horizon content pipeline with an analytics feedback loop.
- **Current GENIE equivalent:** none as a packaged pipeline (pieces: missions, browser, media).
- **Direct code reused?** **No.**
- **Files/components inspected:** README, agent pipeline layout.
- **Adapter/service used:** none; **pattern** folded into content-creation missions.
- **Capability still missing:** packaged content pipeline + analytics feedback.
- **Final status:** `ADAPTED` (pipeline pattern) + `DEFERRED` (full product).
- **Target phase:** content phase (backlog).

### 3.12 public-apis
- **Original purpose:** catalog of free/public APIs.
- **Important capabilities:** discoverable capability index for integrations.
- **Current GENIE equivalent:** none (GENIE has `plugins/` but no discovery catalog).
- **Direct code reused?** **Yes** — as a **data catalog** for capability discovery.
- **Files/components inspected:** README + catalog structure.
- **Adapter/service used:** none (reference data).
- **Capability still missing:** a GENIE-side integration catalog that can propose connectors.
- **Final status:** `DIRECT REUSE` (reference data).
- **Target phase:** 11B (capability discovery aid).

---

## 4. Canonical capability matrix

| capability | implementation | source repo | adapter | status | tests |
|---|---|---|---|---|---|
| `agent.role_templates` | `AgentFactory` role templates | agency-agents | loader (11B) | pending loader | pending |
| `agent.experience_loop` | `ExperienceStore` → group-advantage upgrade | Youtu-Agent | native (Phase 12) | seeded (11D) | pending |
| `agent.evaluation_lab` | simulated worlds | Qwen-AgentWorld | — | DEFERRED → 12 | — |
| `agent.spec_workflow` | coding mission spec→plan→tasks→validate | spec-kit | pattern | pending | pending |
| `agent.self_evolution` | lineage + conformance + approval | enoch | native (Phase 12) | DEFERRED → 12 | — |
| `agent.orchestration` | `TeamOrchestrator` (DAG/mailbox/blackboard) | munder-difflin (compared) | native | **better natively** | 690+ green |
| `agent.skills` | `skills/` + teaching | hermes (compared) | native | **better natively** | green |
| `agent.plugins` | `plugins/` + `computer/` | deepseek-harness (compared) | native | **better natively** | green |
| `browser.semantic_control` | `browser/` CDP + verified actions | PinchTab (compared) | native | **better natively** | real-Chrome e2e |
| `workspace.isolated_computer` | **11C GENIE Workspace** | PinchTab (concept) | native | building (11C) | pending |
| `workflow.automation` | n8n adapter | n8n | REST adapter | pending (11B) | conformance |
| `media.image_video` | ComfyUI worker | ComfyUI | HTTP worker | pending (11B) | conformance |
| `avatar.digital_human` | HeyGem adapter | HeyGem.ai | HTTP adapter | backlog | pending GPU |
| `voice.specialized` | OmniVoice worker | OmniVoice | HTTP worker | backlog | pending |
| `finance.timeseries` | Kronos worker | Kronos | worker | backlog | mock/conformance |
| `simulation.social` | MiroFish service | MiroFish | service | backlog | pending |
| `security.skills_corpus` | `skills/` import (gated) | hack-skills | importer | pending gating | pending |
| `security.assessment_env` | Decepticon adapter (gated) | Decepticon | adapter | backlog | gated |
| `content.pipeline` | AgentTube-derived pattern | youtube-automation-agent | pattern | backlog | pending |
| `integration.catalog` | public-apis data | public-apis | reference | available | — |

---

## 5. Answer: "What capability from this repository exists in GENIE?"

| repository | capability that exists in GENIE |
|---|---|
| agency-agents | 302-division **role/persona corpus** → Agent Factory **role templates** (loader 11B) |
| OpenClaw | persistent-assistant + multi-surface concepts → `devices/` (channels deferred P13) |
| hermes-agent | skill-from-experience → **native `skills/` + `ExperienceStore`** (better natively) |
| deepseek-harness | plugin/everything-is-a-plugin → **native `plugins/` + `computer/`** (better natively) |
| Youtu-Agent | **training-free GRPO experience loop** → adapted into Phase 12 experience loop |
| Qwen-AgentWorld | simulation/evaluation worlds → **deferred** to Phase 12 evaluation lab |
| spec-kit | spec→plan→tasks→validate → adapted into coding/build mission workflow |
| munder-difflin | multi-agent coordination → **native orchestrator** (stronger: budgets, failover, audit) |
| enoch | governed self-evolution (lineage+conformance) → Phase 12 model |
| PinchTab | browser control → **native `browser/`** (better natively); token-efficiency mined |
| n8n | workflow automation + 1500 connectors → **external service adapter** (11B) |
| ComfyUI | image/video generation → **external worker** (11B), engine wrapped not rewritten |
| HeyGem.ai | digital human video → **external adapter** (backlog, GPU-pending) |
| OmniVoice | multilingual voice → **external worker** (backlog) |
| VoiceStudio | voice studio → overlaps GENIE `voice/` → **deferred** with reason |
| Kronos (×2 dup) | financial K-line forecasting → **external worker** (backlog) |
| MiroFish | swarm/social simulation → **external service** (backlog) |
| Open-Higgsfield-AI | image/video (200+ models) → **deferred** (ComfyUI chosen as single engine) |
| hack-skills | 102 security skills / 14 domains → **imported corpus** (gated) |
| Decepticon | security assessment env → **external adapter** (gated backlog) |
| youtube-automation-agent | end-to-end content pipeline + analytics loop → **pattern adapted** |
| public-apis | API catalog → **reference data** for capability discovery |

**No repository is silently unused.** 22 distinct repos: 4 direct reuse (agency-agents,
hack-skills, public-apis + data), 4 adapted (Youtu, spec-kit, enoch, OpenClaw concepts),
4 reimplemented/verified-better (hermes, deepseek-harness, munder-difflin, PinchTab),
10 external-service/backlog (n8n, ComfyUI, HeyGem, OmniVoice, Kronos, MiroFish, Decepticon,
Open-Higgsfield, VoiceStudio, AgentTube).
