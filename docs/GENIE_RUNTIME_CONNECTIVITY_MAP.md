# GENIE runtime connectivity map

## CURRENT ACCEPTANCE — 2026-10-09

Model-facing catalog: **44 tools**, generated from `computer/capability_manifest.py`. Generic browser interaction and disposable upload are **fixture accepted (19/19)**. Packaged runtime is synchronized at **263 files** for source commit `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`; parity check passed. The configured Faster-Whisper small model is provisioned in `%LOCALAPPDATA%\GENIE\models\faster-whisper-small`, pinned to `536b0662742c02347bc0e980a01041f333bce120`; packaged Python loaded it and `/api/status` reports STT ready/loaded. Build-match passed **34/34**. Full pytest passed **1,781**, 79 deselected, 1 `audioop` deprecation warning. WPF Release passed with 0 warnings / 0 errors.

Packaged daemon boot and `/health` and `/api/status` passed: ready, `director: needle` (official runtime verified and 12/12 smoke), Faster-Whisper STT ready/loaded, and Laya `shadow_only` with sampling disabled. A source-built Preview process was launched from `ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe`; the native UI automation surface was unavailable, so no GUI workflow is accepted. A real packaged `/api/chat` C: query used the `files.disk_usage` capability, returned 563.9 GB total / 470.9 GB free, and matched `Get-Volume` (563,876,982,784 total bytes / 470,873,829,376 free). Ordinary Chat returned an explicit mock/demo response (trace `trace_b8f67bbc7476`), not a real model answer. Laya is provisioned and **not active routing**. Real owner workflows remain pending: Preview visual identity/health, WPF Chat action, existing authenticated Brave YouTube workflow, WhatsApp send, real UIA-to-visual fallback, physical Gemini Live interaction, and second-monitor test if connected. See `docs/OWNER_FINAL_ACCEPTANCE.md` for exact owner tests.

## HISTORY — retained repair records

The runtime authority paths and repair records below are retained as history;

# GENIE — DO NOT STOP: COMPLETE ALL NON-OWNER-BLOCKED WORK

Continue from the EXACT current working tree.

> **HISTORICAL snapshot (2026-10-07):** the shared catalog contains **43 tools** generated from `computer/capability_manifest.py`. Earlier 30/15/123 counts are HISTORICAL. See 'History / Repair Log - CURRENT STATE' at the end.

Your previous autonomous pass stopped too early.

The latest evidence proves the daemon is healthy and multiple real production

routes work, but several items were incorrectly classified as BLOCKED_OWNER or

BLOCKED_DEPENDENCY even though further independent engineering/testing is still

possible.

DO NOT perform another broad audit.

DO NOT return another partial progress report.

Continue looping until every independently solvable item is complete.

Use:

IMPLEMENT

→ RUN

→ OBSERVE FAILURE

→ ROOT-CAUSE

→ FIX

→ RETEST

→ PRODUCTION ACCEPTANCE

→ NEXT ITEM

A dependency that can be installed is NOT automatically a blocker.

A local workflow that can be tested with a disposable fixture is NOT an owner blocker.

A failing implementation is NOT an owner blocker.

==================================================

1. FIX THE DURABLE MISSION FAILURE

   ==================================================

Current real acceptance is 11/14.

The generated durable plan uses a boilerplate:

Inspect

Dry run

Execute

Confirm

Report

and the first step fails.

This is an implementation defect.

Trace:

WPF / API Mission creation

→ schedule normalization

→ ensure_plan

→ generated plan

→ AgentService

→ TeamOrchestrator

→ \_act

→ canonical execution

→ verification

→ mission state

Find exactly why the first step cannot execute.

Determine whether the failure is:

- invalid generated capability
- planner objective mismatch
- bad task arguments
- capability not exposed
- permission issue
- AgentService context loss
- task classification error
- artificial boilerplate step that should not exist

Repair the actual plan-generation/execution contract.

Do not weaken verified-receipt requirements.

PASS requires a safe real durable Mission to complete end-to-end through the

actual production MissionRunner and update its state correctly.

==================================================

# 2. COMPLETE LAYA PROVISIONING NOW

Network is available and the Laya repository has already been cloned.

Therefore Laya is NOT currently owner-blocked.

Reference:

<https://github.com/NandhaKishorM/laya.git>

Proceed with isolated provisioning:

- pinned source revision
- isolated compatible Python environment
- torch
- transformers
- safetensors
- huggingface_hub
- pinned Hugging Face weights
- checksums/revision recording

Do not pollute the primary GENIE runtime until compatibility is established.

Run the actual Laya worker.

Measure:

- cold startup
- warm latency
- RAM
- CPU/GPU
- repeated inference stability
- Hindi accuracy
- Hinglish accuracy
- English accuracy

Use the existing GENIE held-out routing evaluation.

Do NOT globally promote Laya if accuracy remains poor.

Instead determine the safest useful deployment:

- shadow-only
- assist for specific high-accuracy classes
- or task-specific fine-tuning required

If some classes achieve strong accuracy, enable only those classes behind a

feature flag.

Keep deterministic heuristics first and NEDLE2 fallback available.

A failed pip/model download attempt with reproducible evidence may become

BLOCKED_NETWORK/DEPENDENCY. Simply not installing the dependencies is not a blocker.

==================================================

# 3. RESOLVE THE PYTHON / AUDIO RUNTIME DRIFT

The current daemon reports Python 3.13 behavior where `audioop` is absent.

First establish the intended packaged Python version from the project/runtime

contract and current packaging scripts.

Do not blindly install compatibility packages until you understand why this runtime

is on Python 3.13.

If the authoritative GENIE runtime is intended to be Python 3.12.x:

- restore/build the packaged backend using the intended runtime,
- verify all embedded dependencies,
- rerun runtime sync/build-match,
- confirm sounddevice/audio stack.

If Python 3.13 is now intentionally supported:

- add the correct maintained compatibility dependency where required,
- provision sounddevice,
- update packaging,
- test imports using the actual packaged interpreter.

In either case, fix:

- `audioop` compatibility
- `sounddevice`
- microphone enumeration
- speaker enumeration
- VoiceService startup

Do not mark physical microphone acceptance complete without hardware,

but software dependency readiness must be completed independently.

==================================================

# 4. COMPLETE GENERIC WINDOWS APP ACCEPTANCE

"Needs a real app target" is not an owner blocker.

Use Notepad or another harmless locally installed application.

Run through the ACTUAL Preview:

open

→ bind ApplicationInteractionContext

→ observe real HWND

→ locate editable control

→ type unique text

→ verify

→ save to temporary approved path

→ independently read saved file

→ verify exact contents

Then perform a second workflow with at least one button/menu/select interaction.

PASS means ApplicationInteractionContext survives the multi-step task and does

not rediscover the app incorrectly.

==================================================

# 5. COMPLETE GENERIC WEBPAGE ACCEPTANCE WITHOUT OWNER BROWSER

Owner-authenticated browser acceptance may remain owner-blocked.

Generic webpage automation does NOT.

Use GENIE-owned browser and an isolated local test website.

Verify:

navigate

→ same tab

→ observe

→ fill text

→ click

→ dynamic state change

→ scroll

→ select

→ second action

→ verify result

Then use one public non-authenticated website where safe.

Record exact tab/session identity through every step.

Do not mark generic webpage automation BLOCKED_OWNER merely because the owner's

personal browser is unavailable.

==================================================

# 6. COMPLETE FILE/PHOTO UPLOAD ACCEPTANCE LOCALLY

A real personal website is not required for implementation acceptance.

Create/use:

- an isolated local upload fixture
- a generated disposable test image/file

Run:

authorized file resolve

→ browser session

→ upload control

→ upload

→ resulting filename/preview

→ independent verification

Verify filesystem restrictions remain enforced.

Only owner-personal-site acceptance remains BLOCKED_OWNER afterwards.

==================================================

# 7. COMPLETE LOCAL VISUAL FALLBACK ACCEPTANCE

If an eligible configured vision provider can be used with current permissions,

run the real path.

If owner approval is required, drive the normal ActionApproval UI and wait only

for that approval while continuing all other tracks.

Use an isolated UIA-incomplete test app.

Required proof:

UIA fails

→ automatic visual escalation

→ fresh screenshot

→ grounded target

→ click

→ re-observe

→ type

→ re-observe

→ scroll/select where available

→ verified state

Move/resize the target between observation and action and prove stale coordinates

are rejected.

If no eligible vision provider is configured after genuine discovery, mark only

the remote-vision execution BLOCKED_OWNER/PROVIDER.

The local escalation, frame identity, geometry validation and approval path must

still be independently verified.

==================================================

# 8. REAL OBSERVATION-MEMORY WORKFLOW

The real DB test passes.

Now test it through production behavior rather than only a script.

Create benign repeated local evidence, for example an application preference.

Verify:

real LocalMonitor observation

→ ObservationStore

→ ObservationMemory

→ ContextCompiler

→ later Chat request

→ relevant preference affects the response/selection

Then provide an explicit correction and confirm it overrides the inferred preference.

Do not save raw screenshots/titles into the compiled context.

==================================================

# 9. BROWSER ACCEPTANCE — SPLIT GENERIC FROM OWNER-SPECIFIC

Do not use one `BLOCKED_OWNER` status for everything.

Complete:

A. GENIE-owned browser — MUST be independently accepted.

B. actual default-browser handoff — independently verify launch identity.

C. named browser handoff — independently verify executable identity.

Only:

D. owner-existing authenticated same-tab session

may remain BLOCKED_OWNER when CDP/approval/authentication truly requires the owner.

For D, prepare the exact acceptance workflow so no additional code changes are

needed once owner authorization is provided.

==================================================

# 10. WHATSAPP — COMPLETE EVERYTHING BEFORE EXTERNAL SEND

Without sending an unauthorized real message, independently complete as far as possible:

WhatsApp discovery

→ existing window identity

→ search

→ result identification

→ open intended authorized test chat if available

→ verify conversation header

→ composer detection

→ exact draft

→ verify exact draft

→ frozen transaction

→ confirmation UI

Only the final consequential external send may remain BLOCKED_OWNER if explicit

owner authorization has not been provided.

Do NOT classify the entire WhatsApp workflow as blocked merely because Send needs approval.

==================================================

# 11. FIX STALE CONNECTIVITY DOCUMENTATION

The main body of `GENIE_RUNTIME_CONNECTIVITY_MAP.md` still contains pre-repair

facts such as old tool counts and old broken descriptions even though addenda say

they were repaired.

Do not keep stacking addenda indefinitely.

Rewrite CURRENT-state sections so they reflect the actual current implementation:

- current tool count
- E29 automatic escalation
- E31 current policy path
- E37 current browser session behavior
- E47 current Live escalation
- E53 current Mission handoff
- E60 current ObservationMemory connection
- current authorities table
- current duplicate/shadowed-path table
- current root causes

Retain historical information in a clearly separated History/Repair Log section.

There must be no contradiction where the main map says BROKEN/PARTIAL and a later

addendum says CONNECTED.

==================================================

# 12. FINAL FULL REGRESSION

After ALL code/dependency changes:

- full applicable pytest
- real-machine tests
- all focused P1 suites
- Laya evaluation
- Mission acceptance
- generic app acceptance
- generic browser acceptance
- local upload acceptance
- visual fallback acceptance where possible
- runtime sync
- build-match
- WPF Release build
- packaged daemon boot
- /health
- /api/status
- source-built Preview launch

Do not rely on earlier test totals after final edits.

==================================================

# 13. STRICT BLOCKER POLICY

You may mark BLOCKED_OWNER only when completion genuinely requires:

- personal authenticated account/session
- explicit consequential owner approval
- physical hardware not present
- secret credential only the owner can provide

You may mark BLOCKED_DEPENDENCY only AFTER trying to provision the dependency

in an isolated/reversible way and recording the actual failure.

You may mark BLOCKED_NETWORK only AFTER an actual network operation fails.

These are NOT valid blockers:

- "needs a real app target" when Notepad exists
- "needs a website" when a local fixture can be created
- "package not installed" before attempting installation
- "model weights absent" before attempting download
- a failing Mission plan
- a missing test harness that can be written locally

==================================================

# FINAL STOP CONDITION

DO NOT send another progress report.

Continue until:

1. every independently solvable software issue is fixed,
2. every locally testable workflow has been run,
3. dependencies that can be provisioned are provisioned,
4. final full regression is complete,
5. current documentation is internally consistent.

Then return ONE final report containing only:

- PASS workflows
- genuine remaining BLOCKED_OWNER/HARDWARE/NETWORK items
- exact Laya production status
- exact Mission status
- voice runtime status
- complete regression totals
- real measured latencies
- files/artifacts produced

If one subsystem becomes genuinely blocked, record it and continue all others.

KEEP LOOPING UNTIL THE STOP CONDITION IS MET.

## Final Non-Owner Completion Pass (2026-10-07, fourth pass)

Every root cause below was reproduced, fixed, and then re-verified.

### 1. `window.close` verification (was: no verifier registered)

`window.close` had no `computer/verifier.py` entry, so `verify()` returned  
"no verifier registered" and the action could never complete a mission.  
`_verify_window_close` now requires the window to be genuinely DESTROYED:

| after-snapshot                      | result                                |
| ----------------------------------- | ------------------------------------- |
| window gone, `IsWindow(hwnd)` false | VERIFIED "closed (destroyed)"         |
| window present, `minimized` true    | REJECTED "is minimized, not closed"   |
| window present, `visible` false     | REJECTED "is hidden, not closed"      |
| window still present and visible    | REJECTED "still exists after close"   |
| no hwnd supplied                    | REJECTED "no window handle to verify" |

HWND recycling is handled. Evidence: `p1_final_repairs_test.py`.

### 2. GENIE-owned browser CDP lifecycle (was: launched, no reachable endpoint)

ROOT CAUSE, measured directly: a **RELATIVE `--user-data-dir` makes Chromium exit  
immediately with code 0 and never open the remote-debugging port.** On Edge 154:  
`--user-data-dir=browser-profile-rel` -> exit code 0 after 4403 ms with no CDP  
endpoint; an absolute path -> CDP reachable in 518-522 ms.  
`BrowserService.__init__` defaulted to the relative `Path("browser-profile")`, the  
exact path used by `BrowserService()` and any caller that did not pass  
`workspace_root`.

Fixes:

- `cdp.launch` resolves `user_data_dir` to an absolute path (covers every caller).
- `BrowserService.__init__` and `get_browser`'s workspace rebind resolve  
  `profile_dir` to an absolute path.
- NEW `cdp.profile_in_use(profile)` detects a live Chromium already holding the  
  profile and fails fast so the caller rotates. Chromium's per-profile process  
  singleton would otherwise swallow the launch and leave the endpoint dead. It is  
  read-only and never attaches to or signals the other browser.

A SECOND independent defect was exposed by the real acceptance: **Chrome silently  
drops synthetic `Input.dispatchMouseEvent` unless the page is the browser's ACTIVE  
target.** `elementFromPoint` returned the button, but the click never landed  
(`out.textContent` stayed `idle`); calling `Target.activateTarget` first made the  
identical dispatch work. Fixes in `BrowserService.act`:

- re-activate the target immediately before dispatching input (connect-time  
  activation does not persist), only when GENIE owns the browser - activating a  
  target in the owner's browser would switch the owner's visible tab;
- if the dispatch still yields no observable change, fall back to a real DOM  
  activation of the SAME element (tagged only after it has passed the visible-bounds,  
  hit-test, unique-match and enabled checks) and re-verify. Success stays gated on an  
  observable change; the receipt reports `dispatch: "input" | "dom"`.

TWO further real defects found by the same acceptance:

- `upload_file` read only `path`, but the canonical contract is `files` (list).  
  `Path("")` resolves to `.` and *exists*, so a caller using the documented contract  
  silently attached the literal current directory and reported an attempted upload.  
  It now accepts `files` or `path` and rejects empty, blank, missing and directory  
  arguments outright.
- `select_option` matched case-sensitively and never consulted the option VALUE, so  
  selecting "beta" failed against an option whose text was "Beta". It now matches  
  value or text, exact then case-insensitive then partial.

`browser.select` was fully registered (scope + planner chain + verifier) but absent  
from the runtime capability manifest, so the model could never reach it. Now exposed  
as `browser_select` (catalog 43 -> 44 tools).

Real acceptance: `scripts/p1_browser_acceptance.py` **19/19** - launch, CDP endpoint,  
session binding, navigate, page identity, observe, fill, click, verified dynamic  
state change, scroll, select, second action, upload with filename verification,  
empty-path rejection, session still bound, same-tab continuation, default-browser  
identity, installed-browser enumeration.

### 3. B02 action-intent source (found by this pass, fixed)

`action_intent` in `core/tool_dialogue.py` scanned `messages[-1]`, which is the  
**assembled context packet** (capability list, context, history) - not the owner's  
utterance. Because that packet contains action verbs, every question looked like an  
action and ordinary conversation ended in "I could not complete that action: no tool  
ran". `run()` now takes `user_text` and the orchestrator passes the owner's actual  
text; mission objectives pass their objective. Verified live: "what is the capital of  
France" now returns the model's prose while "open notepad" with no receipt is still  
blocked.

### 4. Laya provisioning retry

VERDICT: **PROVISIONED AND RUNNING, SHADOW-ONLY (not activated for routing).**

Provisioning (all verified, not assumed):

- source: `https://github.com/NandhaKishorM/laya.git` cloned and checked out at the  
  pinned revision `1e28ac20c0896b1c37a744cd11f740eb98f8b178`, which is the revision  
  `LayaRuntime.start()` itself enforces.
- isolated environment: `C:\tmp\laya_env` (Python 3.12, torch 2.14.1,  
  transformers 4.57.6, laya 0.3.29).
- weights: downloaded with the documented public Hugging Face `resolve` endpoint at  
  pinned revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, with resume and  
  Content-Length/size verification, into `C:\tmp\laya_weights`.  
  model.safetensors              842,609,210 bytes  OK  
  multilingual/model.safetensors 643,835,514 bytes  OK  
  rl_agent_config.json / encoder/config.json / tokenizer/*  OK  
  typed-decisions/model.safetensors  ABSENT (optional third checkpoint)
- license: **apache-2.0** (read from the model card at the pinned revision).

The previous `BLOCKED_NETWORK` verdict is superseded: `snapshot_download` had been  
killed by the environment's bulk-delete guard while cleaning its own  
`.cache/huggingface/download/**/*.lock` files, which left `model.safetensors` absent.  
The direct `resolve` endpoint avoids the HF cache entirely and succeeded.

Measured on real weights (CPU, 2 threads):

- cold load 4.22 s (worker `load_ms` 5.5-6.8 s), warmup 1.04 s, RSS ~2110 MB.
- warm inference median **338 ms** on the 45-case held-out set  
  (min 323.5, max 390.3); ~750 ms per call in the single-shot worker.
- stability: repeated inferences returned consistent answers; no crashes.

GENIE held-out routing accuracy (`scripts/laya_real_eval.py`,  
`artifacts/laya-routing-eval-20261007-real.json`):

| class             | Laya              | heuristic baseline |
| ----------------- | ----------------- | ------------------ |
| conversation      | 1/6               | 6/6                |
| simple_action     | 6/6               | 6/6                |
| browser_action    | 1/5               | 4/5                |
| research          | 0/5               | 1/5                |
| multi_step        | 0/4               | 0/4                |
| durable_mission   | 4/4               | 2/4                |
| **routable-only** | **40.0% (12/30)** | **63.33% (19/30)** |

3 catastrophic misroutes (a real action request answered as pure conversation).  
Laya also emits its own warning that this checkpoint ships temperatures outside  
[0.5, 5], so its confidence values are uncalibrated.

DECISION: **do not activate.** 40% is below the existing heuristic baseline, and the  
only class with a clear gain (`durable_mission`, 4/4 vs 2/4) rests on four cases with  
uncalibrated confidence at ~338 ms versus the heuristic's 0.11 ms. It is therefore  
configured `director.laya.mode = "shadow"`: the warm worker samples turns, records  
`{choice, confidence, baseline, match}` and **cannot change routing** (`route()`  
returns `None` unless the mode is `assist`). Missing or moved paths degrade to  
`state: unavailable` with an explicit reason. A larger evaluation and, most likely,  
task-specific fine-tuning are required before any activation.

### Regression totals after the pass

`p1_final_repairs_test.py` 32/32 (new) - plus capability_manifest 11/11,  
session_affinity 9/9, visual_policy 17/17, visual_escalation 13/13,  
tool_result_budget 15/15, execution_parity 16/16, message_transaction 28/28,  
observation_memory 26/26, mission_objective 20/20, app_context 35/35,  
desktop_awareness 31/31, website_task 23/23, injection_boundary 16/16,  
browser_mode 22/22, app acceptance 13/13, browser/upload acceptance 19/19.  
Runtime sync 262 files, build-match 34/34, WPF Release 0 warnings / 0 errors,  
daemon healthy on the packaged runtime with `laya_shadow: ready`.
