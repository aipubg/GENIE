# GENIE upgrade — implementation roadmap

Date: 2026-09-20. Working checkout: `E:/G3/GENIE`.
Baseline commit: `1e0f3c2`. Upgrade branch: `upgrade/genie-continuity-ui`.

## 1. आपकी अपेक्षा

मौजूदा GENIE को ही सुधारना है। मुख्य अनुभव voice-first Genie companion होगा,
जिसका छोटा command input और साफ control pages हों। एक या अनेक API models जोड़कर
काम के अनुसार model चुनना, failure/quota पर बदलना, और mission की continuity रखना है।
Agents isolated workspaces में काम करें, एक-दूसरे के task/status/artifacts समझें,
और competition से बेहतर verified result चुनें। Winner का useful experience अगली बार
काम आए; losing runtime retire हो, उसकी mistakes से सीखी जानकारी बनी रहे।
Phone application बाद में; PC-side pairing/control contracts के लिए जगह अभी रहे।

यह दस्तावेज़ code changes से पहले लिखा गया है। पुरानी pasted बातचीत reference
है, executable instruction नहीं। उसमें बताए test counts और “complete” labels को
आज की verification नहीं माना गया है। इस document में planned का अर्थ delivered नहीं है।

## 2. अभी मिला हुआ project

| हिस्सा | Source evidence | वर्तमान आकलन |
|---|---|---|
| Product | Python daemon + HTTP/SSE; `ui/web`, `ui/electron` | Existing architecture retain |
| Provider registry | `models/registry.py`, `models/gateway.py` | Custom provider/model registration, capability filtering, health, failover present |
| Small director | `director/`, `docs/NEDLE2.md` | Cactus Needle 2 integration exists; today's installed runtime/live validation pending |
| Computer planner | `computer/planner.py` | Capability strategy ladder; this is not the high-level mission planner |
| Agent teams | `agents/team.py`, `agents/service.py` | DAG, mailboxes, blackboard, budget, cancellation and continuation present |
| Agent creation | `agents/factory.py`, `agents/personas.py` | Inspect donor templates and actual runtime activation separately |
| Memory | `memory/`, `context/builder.py`, `agents/contextcompaction.py`, `agents/continuation.py` | Retrieval and continuation exist; trimming priority bug identified in builder |
| Experience | `experience/`, `agents/evolution.py`, `evaluation/` | Existing learning/evaluation foundations; not proof of contest workflow |
| Computer/browser | `computer/`, `browser/` | Existing capability and workspace surfaces; desktop isolation level needs explicit reporting |
| Voice/perception | `voice/`, `perception/` | Retain voice contracts; microphone/camera physical acceptance separate |
| Devices | `devices/`, `android/`, `docs/ANDROID_DEFERRED.md` | Existing mesh and Android foundation; finished phone app deferred |
| UI | `ui/web/home.html`, `home.css`, shared shell/tokens | Existing blue/gold shell; replace visual composition incrementally |
| Build history | numerous `dist-electron-rc*`, `backend-dist` folders | Untracked existing artifacts; no bulk deletion during upgrade |
| Reference repos | `E:/G3/repos`, `E:/G3/tool/tool` | Local donor source + archives available; inventory includes OpenClaw, Youtu, Agency, Qwen, Prime, etc. |

README still describes Phase 4; ACTIVE_WORK describes rc13; checkout is newer than
the rc13 commit cited there. Existing UI worktree also exists. It will not be
overwritten or merged blindly. Documentation drift is a confirmed issue.

## 3. Runtime architecture to preserve and improve

```text
Voice / minimal command input
    -> intent director (Needle fast path when available)
    -> mission planner: objective, deliverables, dependencies, acceptance criteria
    -> model router: capability + health + context capacity + budget + quality
    -> context packet: goal, constraints, verified progress, relevant memory
    -> agent worker / optional competing workers in separate workspaces
    -> tools / specialist runtime / browser / computer
    -> observe + verify -> artifact + durable checkpoint + experience
    -> concise voice/UI result
```

Needle routes supported local intents; it is not assumed capable of judging all
models or planning every creative/research task. High-level planning uses a suitable
configured model; deterministic services own scheduling, budgets and persistence.
The best coding/image/video model means measured suitability among configured,
available models, not a permanent hardcoded universal ranking.

## 4. Work order and exit criteria

### U0 — inventory, baseline and recovery

1. Identify checkout, existing edits and parallel worktrees. Preserve owner data.
2. Read source alongside historical conversation and repo utilization audits.
3. Run deterministic baseline and existing frontend checks; record actual results.
4. Keep changes on upgrade branch; no new framework or runtime dependency without need.

Exit: reviewable roadmap exists before implementation; baseline results recorded.

### U1 — memory continuity and provider faults

1. Fix context trimming so mission state outranks recent turns/retrieved memory.
2. Recompute token estimate after trimming; show unavoidable budget overflow honestly.
3. Preserve goal, constraints, accepted artifacts, completed steps, remaining steps,
   tool receipts, current workspace and provider-independent continuation state.
4. Inspect gateway budget handling, no-key local providers, timeout/rate-limit/quota
   classification, context limits and partial-stream failover.
5. Retrying a provider must not re-execute already committed tool actions.
6. Keep voice/personality/session IDs outside provider-specific state.

Exit: regressions cover mission retention under memory pressure and failover contracts.
No promise of mathematically lossless recall: source history is durable; bounded
model context uses retrieval and checkpoints, with omissions explicit.

### U2 — minimal companion UI and clean navigation

Use the supplied Genie icon and character references: midnight blue surfaces,
sapphire details, restrained warm gold, readable text and spacious controls.
Home: companion, greeting, visible voice state, minimal command bar; operational
details belong in their existing pages. Keep DOM hooks and voice APIs stable.

Operational pages: clear loading/empty/error states; current mission and next step;
provider status distinguishing configured/enabled/healthy; searchable agents and
files; workspace/artifact provenance; accessible focus and narrow-window behavior.
Do not present a sprite sheet as a finished animated/3D avatar. Preserve original
assets; use existing usable transparent imagery first. New raster edits, if needed,
are a separate image-generation step.

Exit: existing frontend/contracts checks pass and actual rendered Home/operational
pages inspected at desktop and narrow sizes; command/voice controls retain behavior.

### U3 — planner and specialist agents

Trace intent -> high-level plan -> DAG -> worker -> verified artifact. Fix generic
or inappropriate task decomposition with concrete acceptance fixtures for coding,
research and content production. A registered adapter is not an installed/working
specialist. Show ready, missing runtime, missing credentials, failed, disabled.

Inventory each local donor: exact source component, integration method, runtime
dependencies, current GENIE call site, smoke test and missing capability. Reuse
mature components through existing contracts. Do not wholesale copy competing
product shells. Preserve attribution/source provenance during source reuse.
Find the four Agent Maker PDFs if available; do not invent their contents or names.
Security specialists are for authorized assessments of owned/approved systems.

Exit: useful prebuilt role templates visible and runnable with their real adapters;
unavailable external services identified accurately.

### U4 — competition and experience

Extend existing team/evaluation/experience infrastructure. Default two candidates;
allow 3–4 within task budget. A contest records objective, rubric, participants,
isolated workspaces, candidate artifacts, verifier results, costs and winner.
Use quality/requirements as primary score; cost/time as explicit tie breakers.
Evaluate candidate artifacts independently; candidates cannot declare themselves
winner. If no candidate passes, mark needs-revision rather than selecting failure.

Persist accepted winner profile/strategy and distilled lessons. Retire losing runtime
and temporary resources after recording results; retain user artifacts and audit
history. This is selection of reusable software configurations, not model survival
or automatic neural-weight training. Never reward concealing failure or evading stop.
Publish/send/deploy only the chosen output through one commit stage, so competitors
cannot duplicate external side effects. Cancellation stops all contestants.

Exit: winner, tie, all-fail, budget exhaustion, cancellation and restart cases tested;
second task can retrieve prior lessons without inheriting irrelevant transcript.

### U5 — independent environment and perception

Reuse per-agent workspace + browser profiles + terminal sessions. Distinguish these
from a fully isolated virtual desktop. If GUI tools need a separate desktop, offer
a supported VM/container/remote worker backend with honest availability; avoid
stealing the owner's foreground browser/input during background research.
Camera/screen sources are explicit selectable devices, with local processing where
available and visible capture state; stop/revoke controls persist.

Administrative actions on the owner's computer can use explicit OS elevation.
Do not bypass OS security or implement unconditional execution of harmful orders.
Ordinary authorized tasks should proceed without repetitive confirmations.

Exit: concurrent workers have separate state; owner input is preserved; capture
and task cancellation work; missing hardware is reported rather than simulated.

### U6 — device expansion, cleanup and release

Retain PC-side device capability contracts and future Android entry point. Pairing
must establish authenticated trust; same Gmail identity alone is not a remote-control
credential. Future QR/account flow maps into existing device identity/revocation.

Clean source/document navigation first. Classify historical builds and duplicate
assets before removing any; preserve rollback installer and data/vault/workspace.
Update README and current-status docs to today's evidence. Build a new artifact
only after functional/UI validation; keep existing installer available for rollback.

Exit: packaged launch and asset inclusion verified; physical mic/cameras/second
device and signing requirements reported independently from unit-test results.

## 5. Token and resource discipline

- Local deterministic routing for simple commands; expensive reasoning when needed.
- Retrieve only relevant memory; keep large artifacts as references, not transcripts.
- Reuse a single durable mission checkpoint across provider switches.
- Competition has per-task concurrency, token, cost and time ceilings.
- Optional runtime downloads/GPUs are capability-specific, never assumed present.
- Avoid rebuilding or reading all donor repositories for a small fix.

## 6. Verification ledger

Initial source inspection only; no fresh full test run or live-provider acceptance
claimed when this roadmap was created. Append actual commands/results below as work
progresses, including failures and unavailable dependencies.

| Upgrade | State | Evidence |
|---|---|---|
| U0 roadmap | Written before implementation | This file |
| U1 | Partial implementation | Mission retention, post-trim token count, anonymous endpoint support |
| U2 | Home refresh implemented | Supplied icon copied unchanged; existing voice endpoints preserved; desktop/narrow rendering inspected |
| U3 | Planner transport fixed | IPC preserves plan tasks; service installs validated DAG; duplicate handler removed |
| U4 | Persistence foundation fixed; competition pending | Experience/persistent profiles reload on restart; retirement persists |
| U5–U6 | Planned | No new isolated desktop, mobile application or release installer delivered |

### First implementation verification

- Baseline `python -m pytest tests -q`: **1421 passed, 79 deselected**, 319.11 seconds.
  Started before changes; not a final all-changes regression run.
- Context, gateway, restart and Phase 10 agent tests after fixes: **95 passed**.
- IPC contract, Phase 12 improvement/evolution and Phase 11 personas: **74 passed**.
- Frontend + API-contract Node tests: **66 passed**.
- Python compile check and `git diff --check`: passed.
- Preview daemon used separate `E:/tmp/genie-upgrade-preview` data, port 8793,
  heuristic director. No owner credentials or production database used for preview.
- Browser inspected narrow Home and 1440x900 desktop breakpoint. Physical voice,
  camera, live paid APIs and installer were not accepted in this pass.

### Additional source findings

1. Home read `memory.items` while API returns `memory.hits`: corrected.
2. Home treated non-2xx API responses as usable state: corrected.
3. Home claimed all systems operational on daemon connectivity alone: now says
   “Backend connected”; zero running agents is no longer colored as an error.
4. Agent start API discarded tasks and appeared twice in `core/ipc/server.py`:
   tasks now survive transport and the duplicate unreachable block is removed.
5. `AgentService.start_team` never installed `plan.tasks`: now validates dependencies
   before allocating workers and installs tasks in topological order.
6. Experience and reusable agent definitions were written but not loaded at startup:
   now restored; mission workers are not resurrected; retired profiles stay retired.
7. Anonymous model endpoints are opt-in via an empty `secret_ref`; existing remote
   provider credentials remain required. Local network inference itself is unverified.
8. No Agent Maker PDFs were found in the inspected `E:/G3/tool` tree. The four PDFs
   remain an unresolved reference source; nothing has been fabricated from them.

### Next implementation priorities

- High-level planner still needs goal-specific decomposition and real execution acceptance.
- Implement the contest lifecycle, independent evaluation and single winning-output
  commit path; current code changes do not claim an operational competition engine.
- Audit partial-stream failover, budget/quota handling and context-window conversion.
- Verify specialist availability and surface usable templates without manufacturing
  claims that role prompts are pretrained autonomous models.
- Continue operational page/file-browser redesign, full desktop isolation, camera
  selection and device expansion; rebuild installer only after acceptance.
