# GENIE master context

## CURRENT ACCEPTANCE — 2026-10-09

Model-facing catalog is **44 tools**. Generic browser/upload is fixture accepted. Faster-Whisper small is provisioned and pinned at revision `536b0662742c02347bc0e980a01041f333bce120` in the canonical persistent model store; packaged Python loaded it and fresh daemon status reports ready/loaded. Laya is provisioned, shadow-only, and does not participate in active routing. The source-built Preview process launched, but no native UI automation surface was available; owner-facing visual identity/Chat/browser/WhatsApp/visual/physical voice/monitor checks remain pending. Ordinary Chat returned a clear mock/demo response instead of a real model answer. C: storage completed through the packaged daemon and independently matched `Get-Volume`. See `OWNER_FINAL_ACCEPTANCE.md`.

Current technical gate: full pytest **1,781 passed, 79 deselected, 1 warning**; focused P1/workflow set passed 167 tests and device E2E passed 20; runtime sync passed at **263 files** for commit `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`; build-match **34/34**; WPF Release **0 warnings / 0 errors**. The packaged daemon booted ready, `/health` and `/api/status` passed, `director: needle` verified with 12/12 routing smoke cases, Faster-Whisper STT ready/loaded, Laya shadow-only and not routing. No rc27; no Phase 2.

## HISTORY — retained repair records

Previous project context and repair notes are retained below as history.

# GENIE — MASTER PROJECT CONTEXT (permanent continuity)

> **Authority rule:** the *current code* is the authority for implementation truth.  
> Reports and conversations explain intent and history. Never assume an old  
> completion claim is still valid without checking the working tree.
>
> **Last updated:** 2026-10-05 (workflow, audio-format and model-catalog repair)
> **Repo:** `E:\G3\GENIE` · **Branch:** `upgrade/genie-continuity-ui`  
> **Release:** `genie-v0.1.0-rc26` (HEAD). **rc27 must NOT be created.** Phase 2 must  
> not start until Phase 1 acceptance is complete.

---

## Current Workflow Pass: 2026-10-05

Preserve the previous bridge repair and dirty tree. Checkpoint:
`artifacts/workflow-before-20261003.zip`. No credential overwrite or architecture
replacement. New canonical `message.prepare`/`message.send` and
`desktop.visual_observe`/`desktop.visual_click`; 123 IDs, 30 shared tools, 32 Live
declarations. `desktop_focus_window` reuses existing window.focus.

Generic exact-message transaction binds owner/device/session/agent and three
distinct real controls in one HWND/PID. Reject unrelated existing draft. Confirm
exact recipient and content, then recheck and acquire shared desktop lock. Invoke
once, never retry uncertain outcome. Visible outgoing text plus cleared composer
means submitted, not delivered/read. Actual Preview GUI trace_5238b2faa103 passed
in disposable GENIE Acceptance Editor; independent publish/messages.txt exact.
Earlier trace_7f7a67d12568 expired safely. Editor replace/save GUI
trace_fe4577151231 also passed with independent acceptance.txt proof. Real WhatsApp
contact ambiguity remains; no personal send authorized or performed.

Visual fallback uses existing awareness/gateway/executor, physical-pixel DPI,
single fully visible foreground window, exclusion/overlap/password masking,
opaque owner-bound targets and exact fresh-pixel validation. One confirmed click
only, before/after verification, no guessed coordinates or model-generated code.
Fixture-tested but live visual gate OPEN. Provider upload needs separate explicit
destination approval and SENSITIVE policy eligibility; provider-constrained
gateway requests cannot fail over to another endpoint or mock. Gemini checkbox
does not approve other providers. Current Gemini policy excludes SENSITIVE;
do not silently relax it. Screenshot-only typing/scrolling remains unfinished.

Live fix: negotiate supported rate/channels on same selected audio device,
streaming PCM conversion to/from 16/24 kHz, flush conversion on interruption,
truthful capturing/device_verified. Real packaged default Realtek rejected wire
rates; 44.1 kHz callback output passed 150 ms silence. Default input Line In needs
owner microphone selection. Physical 3-turn Hindi/Hinglish Voice NOT accepted.
Latest Live diagnostic: Oct4 uia_scan_incomplete + 352800 audio bytes; Oct5 timeout
without receipts/audio. Preserve both failure artifacts and earlier Oct2 PASS.

Routing guards now retain Hinglish compound app intent and prevent software/store
install/download requests from turning into media playback. Media keyword matches
use word boundaries. Ordinary replies no longer instructed to call tasks missions.

Original pinned Laya CPU worker added to Director off/shadow/assist, warm isolated
process, offline model, bounded timeout/cooldown, heuristic-first/NEDLE2 fallback.
20-case real eval: 50% accuracy, cold 22.274 s, warm median 94 ms vs Needle 836.72 ms.
DO NOT PROMOTE: high-confidence wrong routes. Production remains OFF; NEDLE2 kept.
Source revision 1e28ac20c0896b1c37a744cd11f740eb98f8b178,
model revision aa8c91ca088ec597df95a0d1c76b3063cb2ae5e8.

Gemini REST probe found old 2.5 Flash unavailable to this account (HTTP404).
Added official 3.8 Flash template without removing old/user models or changing
policy. Real REST test PASS (8485 ms); successful test clears stale auth_required.
Sources: https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash and
https://ai.google.dev/gemini-api/docs/pricing. Review template pricing Jan2027.

Final suite 1760 passed, 79 deselected, one audioop warning, 376.36 s:
artifacts/workflow-final-pytest-20261005.xml. Runtime 258/258, final build-match
34/34, WPF 0 warnings/errors, source-built Preview restarted, daemon ready.
Earlier concurrent sync had 3 dependency probes fail despite separate import PASS;
unloaded repeat passed; root cause of transient probes is not established. GUI helper after
restart failed stale handle then foreground PID; no final GUI repeat claimed.
Evidence collector appends workflow_runs to connectivity-production-evidence.json,
retaining prior records. Two TXT reports include scope and remaining gates.
No rc27/Phase2. Next: reliable physical Voice, real recipient workflow, live visual
grounding/provider-policy approval, owner-browser acceptance and remaining
universal-control gaps. Full objective is not complete.

## Historical Tool Bridge Pass: 2026-10-03

Preserve the dirty tree, credentials and Laya work. Critical raw-tool-JSON defect
reproduced in actual GUI before fixing: trace_8bf6c32943d8 returned prose plus
find_application JSON instead of dispatching; older trace_c2ace75fe4b6 printed two
objects. Adapter now normalizes native Chat/Responses calls and strict textual
compatibility envelopes into Completion.tool_calls. Core dialogue no longer
parses arbitrary completion text. Unknown/schema-invalid/mixed/truncated batches
fail closed. Shared bridge validates/cancels/authorizes, executes existing
ComputerService and returns bounded iterative results to the selected model.
Call IDs are retained/deduplicated. Progress is separated in SSE, WPF and history.
Old saved JSON failures are deliberately preserved as history.

Actual GUI original-request replay trace_04f49c535165: WhatsApp opened, real calls
ran, natural final response, no new raw JSON; search targeting failed. Follow-up
trace_dc4ca25c7cb1 found its rendered-window Search Edit. Continuation
trace_3605b365d36d set physics and independently observed real search results.
Multiple chats match, so exact recipient was requested from owner. No Hyy draft
or external message sent. UIA result names still incomplete; visual fallback is
NOT implemented. Do not claim full WhatsApp acceptance or unrestricted access.
Generic ambiguity candidates now include process/PID/class, with tool guidance
to inspect rendered content and broaden name filters by control type.

GUI trace_75fdd51a9c48: local webpage observe/fill/Apply draft/reobserve PASS,
output Applied: Bridge verified 2026-10-02. Separate production SSE
trace_2196622e9ecd executed inspect_desktop and desktop_windows with one progress,
one natural final and done (13.43 s). Metadata only, not pixel understanding.
Real Gemini Live production diagnostic: two native calls, verified control state,
151680 bytes reply audio. No microphone/playback acceptance; Home lacks microphone.
Xkiro currently uses compatibility mode (models declared general-only), with Qwen
fallback after Minimax HTTP404 temporary-unavailability. Native Chat/Responses
wire handling fixture-tested; native tool-event streaming is not implemented.

Final suite: 1722 passed, 79 deselected, one audioop warning, 375.90 s. Focused
120 passed. Real browser fixture PASS, 22.08 s. WPF 0 warnings/errors, sync 253/253,
build-match 34/34. UI SHA256 e6b23014e5be3977477cfd24e76a9350f1ed9ba451e7152a2942cb13d53e19cd.
Reports and artifacts/connectivity-production-evidence.json account for 119 IDs,
25 shared tools and 27 Live declarations; per-ID registration is not live acceptance.
Notepad fresh repeat hit external test-helper capture timeout; no owner note edited.
Public search/YouTube/tab switching were not repeated on final GUI bridge.
Next: exact-recipient/grounding/confirmation acceptance, absent visual fallback,
physical Voice and unverified owner workflows in GENIE_UNCONNECTED_CAPABILITIES.txt.
No rc27, Phase 2, broad deletion, credential overwrite or duplicate authority.

## Historical Corrective Pass: 2026-09-28

The two primary interaction gates passed through the real WPF Preview composer:
independent Windows editor inspect/type/save/read-file and local webpage
observe/fill/click/read-result. This supersedes older statements that only HTTP
Chat or direct service tests were performed. Full owner acceptance is NOT done.

Reproduced WhatsApp open failure in GUI before repair (trace_8b8a7ad8adf8,
99.64 s approximate backend interval). Packaged app entries lacked an expected
EXE, so the existing-window path missed them and repeated a launch/wait four times.
`windows_api.application_user_model_id` now resolves the real process AUMID;
`Executor._app_windows` reuses the matching window and verification checks its
HWND/PID/package. Unknown post-launch outcome stops retries. Identical GUI request
after repair passed (trace_e49e3867c77b, 16.15 s), observing one WhatsApp Search Edit.
Host HWND 722130/PID 16308 and content HWND 1443096/PID 4896 share the title.

UIA now resolves native HWND before calling the accessibility provider, traverses
immediate children breadth-first to depth 32 with budgets, isolates bad subtrees
and deduplicates runtime IDs. This avoids pywinauto's eager whole-subtree scan and
reaches deeply nested WebView controls. Ambiguous titles return exact HWND choices
through shared desktop_controls. Existing persistent MTA worker is preserved.

GUI Windows test trace_4372a05fe2e7 (17.93 s): `uia.find`, `uia.set_value`,
`uia.invoke` Save fixture, fresh saved status. Independently read exact text from
tests/fixtures/desktop_editor/bin/Release/net8.0-windows/win-x64/acceptance.txt.
GUI webpage trace_15c17a3aabb3 (21.10 s): fill Search box, click Go and read
`clicked:GENIE webpage interaction verified 2026-09-28` on the isolated fixture.
Public GUI trace_a7089beac776 (20.98 s): example.com observed link click -> IANA.
Bing trace_381e184d1026 filled its field and read real results, but used URL
navigation instead of the requested Search button; keep that scenario PARTIAL.
Times are first trace log to persisted reply, not microphone/UI-input latency.

Browser fill now replaces full content, targets unique visible editable controls,
supports contenteditable and verifies exact value. Observe/click label precedence
is aligned; real CDP reproduced and then passed an aria-label/inner-text mismatch
and input[type=button]. Password values are excluded from observation labels.
Consequential website clicks use existing owner confirmation, reobserve and bind
tab/URL/document identity before action; no new browser/controller authority.

Real Gemini Live function calls plus 107040 bytes reply audio pass through the
production LiveSession/Daemon bridge using an isolated ComputerService. Typed
diagnostic input only; WPF microphone/audio hardware acceptance still pending.
25 shared tools / 27 Live declarations / 119 executor IDs remain distinct layers.
See both TXT reports and artifacts/connectivity-production-evidence.json for
exact synthetic GUI prompts, final responses, tool/permission logs and per-ID scope.

That full-suite repeat completed with 1699 passed, 79 deselected, one warning;
its final XML records 371.818 s. The current tool-bridge result is recorded above.
Runtime sync 252/252; build-match 34/34; WPF Release zero warnings/errors.
No credentials changed, no uncommitted work discarded, no rc27 or Phase 2.

Remaining: approved WhatsApp contact/message and send acceptance; complete
recipient/draft-bound desktop confirmation; real microphone/speakers; owner
browser/profile/private tabs; YouTube playback; visual-grounded fallback (still
not implemented, planner vision=False); universal settings/app controls; other
unexercised IDs. The generic UIA fix is not a claim of human-level universal control.

## Historical Corrective Pass: 2026-09-27

Current state supersedes the historical sections below. Source Preview is running
with the synchronized packaged backend. No rc27 or Phase 2 was created.

The production daemon returned empty UIA controls even while standalone UIA worked.
`computer/uia.py` now owns pywinauto/element handles on one persistent MTA worker;
per-request errors cross back to the request thread. Unknown outcomes after a
worker timeout stop executor retries. Actual HTTP Chat read 71 GENIE controls
(trace_b5f67aa0449d), selected the Chat navigation item and independently observed
selected=true (trace_3af7076fc5a3). This proves one real desktop interaction, not
universal app support. Owner messages were not sent through the GUI send button.

`computer/tool_bridge.py` now honors ActionResult.verified and propagates nested
executor errors. Browser clicks verify same-page content/control changes and new
tab identity; ambiguous/occluded targets are rejected. Real headless fixture
acceptance passed menu opening, draft fill, navigation, no-effect rejection and
ambiguous-label rejection. See artifacts/browser_bridge_acceptance.json.
Final public HTTP Chat click also passed directly (trace_830ac2492010):
example.com -> IANA, verified browser.act then fresh observation, about 20 seconds.

Reasoning requests have shared tools without depending on English/Hinglish action
keywords. Greetings/history are excluded. The semantic guard reroutes app-name
interaction and negated-action plans instead of executing an app-open shortcut.
WhatsApp discovery passed; WorkBuddy AI remains undiscovered, not proven absent.
installed_browsers now reads actual Windows HTTP/HTTPS defaults. Both are Brave
here; production Chat verified this (trace_2972bb49dfe4). This does not prove
existing Brave profile/tab control. inspect_desktop is shared with typed Chat;
counts are now 25 shared tools, 27 Live declarations, 119 registered executor IDs.

Fresh Gemini Live handshake PASSED. A real Gemini session called desktop_controls
and desktop_control(state) through production LiveSession/Daemon tool dispatch,
received verified receipts and returned 314402 bytes of audio and a matching
transcript. See artifacts/live_tool_bridge_acceptance.json. The diagnostic used
typed input; no microphone, speakers, screen pixels or camera were used. Earlier
invalid-key health history must not be reported as the current Live status.

Focused regression: 164 passed. Final configured full suite: 1686 passed,
79 deselected, one audioop warning, 367.29 seconds; no failures. Evidence is in
artifacts/connectivity-final-pytest.xml. Existing hardware/owner/external-optional
exclusions remain untested, not passed. Runtime 252/252,
build-match 34/34, WPF Release zero warnings/errors. The two TXT reports have
current sections listing remaining real-owner acceptance and unimplemented APIs.
No claim of every capability being fully operational is warranted.

## Historical Corrective Pass: 2026-09-25

### Browser / Chat connectivity follow-up

The 2026-09-24 report sections are historical where they conflict with this
follow-up. Current source exposes 24 shared Chat/Live bridge tools and 27 Gemini
Live declarations over the same 119 ComputerService IDs. Added draft-only
`browser_fill`; repaired unsupported multi-step website clauses to fall back to
bounded Chat tool reasoning; added parsing for a single provider-wrapped JSON
tool call; made browser click success depend on observable effects including a
new tab; switched clicks to trusted CDP mouse events; added one fresh post-click
observation without repeating the action; and contained per-window errors inside
the native EnumWindows callback.

Live production Chat reproduced the old planner failure, then a final public
acceptance passed: the observed `example.com` “More information...” link was
clicked once; fresh page observation verified title `Example Domains` and URL
`https://www.iana.org/help/example-domains` (trace `trace_a309e8137504`, 76.4 s).
This proves only the GENIE-owned public-browser flow. No WhatsApp private session
or message was accessed; its owner-visible UIA interaction remains unverified.
Google rejected the saved Gemini credential as invalid (HTTP 400); its value is
not recorded. Current full suite: 1666 passed, 79 deselected, one `audioop`
deprecation warning; focused connectivity set 91 passed. Runtime sync 252/252,
build-match 34/34, WPF Release build 0 warnings/errors; current Preview PID
6672. Details are in the two capability TXT reports.

## Historical Corrective Pass: 2026-09-24

### Complete Codebase Connectivity Audit (2026-09-24)

That report's final results reflected the source-built WPF Preview and packaged
backend. It recorded 119 ComputerService IDs, 23 shared Chat/Live tools and 26
Gemini Live declarations; these are overlapping architecture layers, not
additive independent feature counts. Its configured suite was 1658 passed, 79
deselected, one `audioop` deprecation warning, 370.41 s.
Packaged runtime sync matched 252 files; build-match passed 34/34; WPF Release
build passed with zero warnings/errors. All 119 capability IDs are accounted
for in the connected/unconnected reports.

Live backend status was ready on the source-matched runtime/owner session.
Packaged UIA initially appeared unavailable due to cached COM initialization
failure; retrying that transient error and restarting fixed live status to
`pywinauto available=true`. A live typed Chat request now routes open-window
inventory to `window.list` in 384 ms. WorkBuddy lookup truthfully returned no
match in the refreshed index. Live web research returned source URLs but took
about 50 s due two HTTP 500 failures from active Xkiro before fallback. Gemini
Live handshake passes, but the physical microphone was not opened. Manual WPF
Chat interaction, actual Gemini function call/audio turn, personal browser
session, screen pixel transfer, camera and destructive system operations remain
unverified or owner-gated. First-class Wi-Fi/Bluetooth mutation, default audio
endpoint selection and universal install/uninstall are not implemented. Never
claim unrestricted computer access. Laya remains disabled/shadow-only;
NEDLE2/heuristic routing remains authoritative.

Read `docs/GENIE_CONNECTIVITY_REPAIR_2026-09-24.md` before relying on older completion claims below.
Current Chat and Gemini Live use `computer/tool_bridge.py` for exposed conversational actions; typed action reasoning has a bounded loop in `core/tool_dialogue.py`. Existing ComputerService permissions/execution remain authoritative. Installed-app discovery now includes MSIX Start registrations. Named/default browser URL handoff is distinct from CDP control and page verification. Desktop UIA control is connected but universal owner/private browser acceptance is not complete.

The screenshot follow-up adds conversational folder creation/list/find with redirected Windows user folders, verified UIA toggle/selection state, and an exact-action WPF confirmation for sensitive desktop controls. Approval requests are short-lived, cannot be answered by a model tool, and recheck the observed target after approval. This uses the existing local IPC trust model. Personal messaging and network changes were not performed during verification; owner acceptance remains pending.

The next report added broader English/Hinglish/Hindi action triggering, routed uninstall requests away from the app-open shortcut, and preserved Windows UIA access-denied details instead of returning an empty control list. The current local DB has owner grants for app/UIA/files/browser, and the last Preview logs show UIA reads allowed; no PTE denial for the screenshots is present in the available logs. A Windows integrity/UAC denial remains distinguishable and needs the exact app/action to resolve.

Conversation archive migration 042 preserves new transcripts separately from bounded model context. WPF now has selectable chat, themed editing menus, settled-mission deletion, and a Home cloud-screen-sharing checkbox. Cloud permission is separate from local awareness.

Historical follow-up: `backend_entry.py` processes embedded Python `site-packages` `.pth` files before backend imports, repairing packaged `win32api` discovery. The later live acceptance also repaired transient COM failure caching and inventory routing. Chat history reload, text selection/edit menu styling and settled-mission deletion remain in the current WPF build. The source Preview and packaged backend were started and verified in the current pass; focused and full-suite results are recorded above and in the two capability reports.

Original Laya source/checkpoint are pinned and locally evaluated. One recurring request was misclassified in a four-case CPU probe, so Laya shadow mode remains optional and disabled. NEDLE2 and deterministic routing are retained. No rc27 / Phase 2 promotion. Owner requested code/backend verification rather than further manual screen interaction; remaining real-use acceptance must be stated explicitly.

Older sections below are historical and must be checked against current source.

## 1. Owner's vision and architecture

GENIE is a **local-first Windows desktop AI assistant** (Python backend + WPF  
shell), owner-operated, privacy-respecting, with:

- **Voice-first Home** (Jarvis-like circular core), plus typed Chat.
- **Computer/device authority** — one executor per capability (`computer/executor.py`),  
  CDP-based browser control, UIA, shell, files, media.
- **Missions** — *durable* multi-step work (persisted). Deliberately **not** used for  
  one-time actions.
- **Director / router** — `director/heuristics.py` (deterministic NEDLE2 fast path),  
  `director/nedle2.py`, `director/base.py`; applied in `core/orchestrator.py`.
- **Providers** — OpenAI-compatible (`models/providers/openai_compat.py`), registry in  
  `models/registry.py`, gateway in `models/gateway.py`.
- **Memory** — `memory/service.py`, `core/session_store.py`, `context/compiler.py`  
  (one authoritative memory query per turn).
- **Security** — `security/injection_guard.py` (external content is DATA),  
  `security/trust.py`, `security/policy.py`.

**Packaging:** PyInstaller backend (`backend-dist/backend-runtime`) + WPF  
`Genie.Desktop.exe` (net8.0-windows). Runtime sync: `scripts/sync_backend_runtime.py`.

**Non-negotiables (owner-stated):**

1. **Truthful execution.** A model's statement that something happened is *never* an  
   execution receipt. Verified or reported as failed/blocked — never narrated success.
2. **No unnecessary durable Mission** for a one-time request.
3. **Explicit browser selection** — Brave means Brave; never silently substitute Chrome.
4. **No bypassing authentication / permissions.**
5. **Prompt-injection boundary** — webpage content is task data, never authority.
6. **Owner acceptance is physical.** Microphone / speakers / visible desktop workflows  
   are **never** marked PASS from fixtures, API tests, or compiled XAML.
7. **rc tag identity is immutable** — a new product change needs a new rc tag.

---

## 2. Current code / runtime status (verified 2026-09-22)

| Item                | State                                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| HEAD                | `6f2bb7e Fix model discovery: drop undeclared httpx dependency`                                                    |
| Tag                 | `genie-v0.1.0-rc26` (rc27 forbidden)                                                                               |
| Working tree        | **DIRTY — ~57 modified files, 4 untracked** (Phase 1 in progress, do NOT discard)                                  |
| WPF EXE             | `ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe` 277,504 B (built 2026-09-22 18:41) |
| Backend runtime     | `backend-dist/backend-runtime/RUNTIME_MANIFEST.json` — `source_commit 6f2bb7ec…`, **244 files**                    |
| STT                 | faster-whisper `small`, `stt_model_present=True`, `stt_model_loads=True`                                           |
| Browsers on host    | Brave ✅, Chrome ✅, Edge ✅ (all real paths)                                                                         |
| Untracked new files | `PHASE1_ACCEPTANCE.md`, `PHASE1_MULTISTEP_REPORT.md`, `context/compiler.py`, `core/session_store.py`               |

**Preview build** = the source-built EXE above (the "GENIE Preview" shortcut targets it).

---

## 3. Completed phases / verified fixes (do not redo)

- **Phase 0 CLOSED** — see `artifacts/PHASE0_CLOSURE_REPORT.md`,  
  `artifacts/PHASE0_SOURCE_OF_TRUTH_REPORT.md`.
- **Phase 1 (rc26) implementation pass** — see `PHASE1_ACCEPTANCE.md`. Four defects  
  fixed: Home amplitude parsing (`level` dict), missing `tts_amplitude` in  
  `VoicePipeline.status()`, context-resolver `bola` phrasing gap, SOT/Advanced STT  
  line reading `provider` as well as `name`.
- **Phase 1 corrective pass (accepted as implementation progress, 2026-09-22) — the  
  general website-interaction closure.** Already implemented in the working tree:

  **A. Existing authority reused** (no second browser engine):  
  `browser/cdp.py`, `browser/targets.py` (target resolution), `browser/service.py`  
  (`BrowserService`/`BrowserMaturity`), `computer/service.py` capability→permission  
  map, `computer/verifier.py` (`_verify_browser`), `security/injection_guard.py`,  
  `computer/planner.py` strategies, PTE permission strings.

  **B. New reusable primitives** (all in `browser/service.py`, registered in  
  `BrowserService.handle()`):  
  `browser.observe` (url/title/controls/headings/text/gate),  
  `browser.detect_gate` (sign-in/captcha/permission),  
  `browser.act` (activate best-matching button/link, observes effect),  
  `browser.fill` (type into a *verified* editable field, optional submit),  
  `browser.verify` (selector / text / url_contains / real image),  
  plus the pre-existing `browser.navigate/dom_query/click/type/extract/tabs/screenshot`,  
  `browser.wait`, `browser.media.play`, `browser.fullscreen`, and the Phase-4 maturity  
  set (tabs, select, checkbox, scroll, upload, download, dialog, history,  
  accessibility, cookies, leases).

  **C. Routing (no `application.open` for websites)** — `director/heuristics.py`:  
  `WEB_SERVICES`, `BROWSER_ALIASES`, `find_web_service()`, `find_browser()`,  
  `split_clauses()`, `plan_web_action()` returning an ordered bounded plan;  
  unsupported clauses become `plan.unsupported` (reported BLOCKED, never dropped).  
  `core/orchestrator.py::_apply_web_action_plan()` applies it **after every director**  
  (like `_apply_conversation_override` / `_apply_mission_gate`), so no director can  
  bypass it, and sets `mission_required = False` (no durable Mission).  
  Site adapters: `SITE_ADAPTERS = {"chatgpt": "browser.chatgpt.image", "openai": …}`.

  **D. Thin ChatGPT adapter** `browser/service.py::chatgpt_image()` — composed **only**  
  from the primitives: browser → navigate → observe → gate → composer → new chat →  
  fill/submit → bounded wait → **NEW image artifact** verification (snapshots existing  
  images so logos/avatars cannot be mistaken for a generated image). Prompt submission  
  alone is never success.

  **E. Cancellation** `core/orchestrator.py::_run_tasks()` — per-plan  
  `cancel_event.clear()` first (a stale Stop cannot cancel the next request); checked  
  *between* steps; distinguishes `cancel_requested`, `cancel_boundary`  
  (`before_step_N` / `after_all_steps`), `not_attempted`, and a step that *completed  
  despite* the request is never marked CANCELLED.

  **F. Injection** — `observe`/`dom_query` route page text through `_tag_untrusted()`  
  → `security.injection_guard.get_guard().tag(...)`.

**Verified test results (this environment):**

- `scripts/p1_web_primitives_test.py` → **10/10** (local page + `example.com` reuse  
  proof: observe/fill/act/verify/detect_gate).
- `scripts/verify_build_match.py` → 31/31 (earlier pass); WPF build 0 warnings / 0 errors.

### Phase 1 general website-interaction closure (2026-09-22, this session)

**New:** `browser/website_task.py` — the bounded website plan executor  
(PLAN → ACT → OBSERVE → VERIFY → NEXT STEP). Plan executor ONLY: it dispatches  
through the existing capability system; it is not a second browser/planner.

- `director/heuristics.py`: `_plan_web_action_raw` + `plan_web_action(annotate=True)`
  - `annotate_web_plan()` → every step gets `id`, `depends_on`, `expect`, `verify`.
- `browser/service.py`: capability `browser.website_task`; `fill` hardened  
  (detached-node detection, caret placement, 3 insertion strategies, verified by  
  read-back); `_detect_gate` now detects logged-out surfaces (`log in` + `sign up`,  
  no `log out`); `navigate` retries once on the active page after a browser switch;  
  `observe` returns fenced page text; `chatgpt_image` waits for the composer,  
  records a submission receipt and captures the site's reply when no image appears.
- `core/orchestrator.py`: web plans are delegated to the engine (same reply/state  
  semantics); the annotated plan rides on `decision.raw["web_plan"]`.
- `computer/{service,verifier,planner}.py`: permission, verifier and strategy  
  entries for the new capabilities.
- `security/injection_guard.py`: `wrap_untrusted()` fencing + `navigation_lure`  
  and extra `instruction_override` patterns.
- New fixtures: `tests/fixtures/login_page.html`, `tests/fixtures/injection_page.html`.
- New suites: `scripts/p1_website_task_test.py` (23/23),  
  `scripts/p1_injection_boundary_test.py` (16/16), `scripts/p1_login_gate_test.py` (8/8).

**Headline results (real evidence):**

- Chat→YouTube playback **verified through the real Preview GUI** — GUI reply  
  `browser.media.play: succeeded — currentTime 0.545245 -> 3.595593, paused=False`,  
  a GENIE-owned browser appeared, screenshot  
  `artifacts/p1_gui_evidence/chat_browser_action.png` (`p1_gui_browser_action_test.py` 8/8).
- Daemon path `p1_multistep_test.py` **13/13**; no durable Mission (0 → 0).
- Brave→ChatGPT: Brave honoured, page verified, then **BLOCKED at `sign_in`** —  
  the GENIE Brave profile has **no ChatGPT session** ("Log in / Sign up for free");  
  nothing is submitted. Owner must sign in.
- `PHASE1_MULTISTEP_REPORT.md` (A–N) rewritten; the injected content is gone.

### Phase 1 corrective pass — browser profile selection (owner mode, 2026-09-22)

**Owner clarification (Mode 1 vs Mode 2):**
- **Mode 1 — owner's existing browser:** when the owner asks GENIE to work in their
  *already-running, signed-in* Brave (or another browser), use that browser/profile
  wherever technically possible. For the Brave→ChatGPT example, this is the owner's
  normal Brave where ChatGPT is already signed in.
- **Mode 2 — GENIE-owned browser:** when the owner explicitly asks GENIE to work
  *independently / in its own browser*, GENIE launches a separate persistent profile;
  background work must not interrupt the owner's active desktop.

**Diagnosis (why the earlier test reached a logged-out ChatGPT):**
- `browser/cdp.py::launch()` always passes `--user-data-dir=<workspace>/browser-profile`
  (a GENIE-*owned* profile) and `--remote-debugging-port`. It never sees the owner's
  signed-in profile. So "Brave→ChatGPT" previously opened a *fresh, logged-out* Brave.
- The owner's actual Brave is running (PID 18600) at
  `C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe` using the default
  profile `C:\Users\ghostt\AppData\Local\BraveSoftware\Brave-Browser\User Data`, and has
  **no `--remote-debugging-port`** — so CDP cannot attach to the live process without
  relaunching it (which is forbidden).

**Implemented (`browser/mode.py` + `browser/service.py`):**
- `browser/mode.py` (NEW, pure detection + decision, no side effects):
  - `detect_owner_browser(name, lister=, reach_probe=, port_owner=)` — inspects the owner's running
    browser and decides whether GENIE may attach over CDP WITHOUT ever navigating or modifying that browser. Reliable Windows `Get-CimInstance Win32_Process` (CIM) enumeration returns exe+pid+cmdline; the deprecated `wmic` is only a fallback. Identity is confirmed from the debug port's OWNING PID (`Get-NetTCPConnection`, injectable `port_owner`): the port must be reachable AND owned by the detected process. A GENIE-owned `browser-profile` is never mistaken for the owner's; an unidentifiable reachable port is reported as `unidentifiable` -> `needs_owner_action` (no attach). Reports `found` / `attachable` / `genie_owned_conflict` / `unidentifiable`
    and the exact `owner_action` instruction when attachment is impossible.
  - `decide_browser_mode(text, browser, service)` — `owner_existing` for a named browser
    + a signed-in site (chatgpt/openai/gmail) or explicit "my Brave"; `genie_owned`
    otherwise (generic browsing/YouTube, or explicit "your own browser").
- `browser/service.py`:
  - `_ensure_requested_browser(requested, mode=)`:
    - `owner_existing` → detects the owner browser; if attachable (debug port reachable)
      attaches via `_attach_existing()` (NO launch/close/relaunch, never copies creds);
      if running-without-port or not-running → returns `needs_owner_action=True` with the
      exact instruction and **does NOT silently launch a logged-out GENIE profile**.
    - `genie_owned` → existing launch behaviour. A previously-blocked owner request for
      the same browser is NOT silently downgraded (owner must explicitly choose Mode 2).
  - `_connect_page()` / `ensure()` are owner-aware: when attached to the owner's browser
    they verify liveness on the adopted port and never relaunch a GENIE profile.
  - `shutdown()` when attached to the owner's browser DISCONNECTS only (never kills it).
- `director/heuristics.py::plan_web_action()` now injects `browser_mode` into every step's
  params; `browser/website_task.py::_with_browser()` forwards it to switchable capabilities;
  `chatgpt_image` / `navigate` honour it.

**Exact owner action (the only way to let GENIE drive the signed-in Brave):**
close Brave and relaunch it once from a shortcut whose target ends with
` --remote-debugging-port=9222` (or enable remote debugging), then re-send the request.
Alternatively say **"use your own browser"** and GENIE opens a separate Brave profile
(logged out — the owner signs in there).

**Verified (`scripts/p1_browser_mode_test.py` → 22/22):**
detection (not-running / running-without-port / running-with-port), mode routing
(`owner_existing` for Brave+ChatGPT, `genie_owned` for YouTube / "your own browser"),
no launch when owner attachment impossible, no silent downgrade after a block, separate
profile launches for Mode 2, shutdown disconnects owner browser without killing it. **+ 8 new browser-identity safety tests (2026-09-22):** (a) a real owner Brave with a reachable, process-owned debug port is correctly identified and attachable; (b) a GENIE-owned Brave (`browser-profile`) is rejected; (c) an unrelated Chrome holding the port is rejected; (d) an unidentifiable reachable endpoint is rejected in `owner_existing` mode; plus port-owned-by-another-process and executor-level rejections (never launch, never attach).

`p1_multistep_test.py` D-section now reports the honest requirement
("Your brave is not running. Start it (signed in) so GENIE can attach…") instead of a
false pass.

**Live verification (continuation, 2026-09-22):** A read-only re-detection found the
owner's Brave process list unavailable in this runtime (`wmic` missing from the agent
shell) but CDP port **9222 reachable** with a `https://chatgpt.com/` tab open. The earlier
runtime fallback that auto-attached to ANY reachable debug port (flagged `binary_unconfirmed`)
was a correctness AND security defect and has been REMOVED (2026-09-22 identity correction):
`detect_owner_browser` now confirms, from reliable Windows process info (`Get-CimInstance
Win32_Process` + `Get-NetTCPConnection` port->PID), that the listening debug port is owned by
the owner's actual browser process before attaching. A live re-detection found port 9222 owned
by an unrelated Chrome (not the owner's Brave), so GENIE now correctly reports `unidentifiable`
-> `needs_owner_action` and attaches to nothing - it never navigates or modifies that browser.
The executor attaches via `_attach_existing` ONLY after identity is confirmed (NO launch/close). `scripts/p1_chatgpt_live_probe.py` drove the real adapter:
`browser ok=True` (attached) → `navigate ok=True` → `observe` → `gate=sign_in`
("no authenticated session"). Result: `verified=False, blocked=True, needs_owner=True` —
GENIE stopped at the login gate and submitted nothing. The open ChatGPT tab was **not
signed in**, which proves this 9222 browser is **not the owner's personal signed-in
Brave** (a signed-in session would be authenticated); the GENIE fixture tabs also point
to a GENIE-managed profile. The adapter pipeline is therefore verified up to the auth
gate; a verified image still needs the owner to sign in (or enable their actual signed-in
Brave with `--remote-debugging-port=9222`).

**Browser identity safety (requirements, 2026-09-22):** the owner's existing browser is attached over CDP ONLY when GENIE can confirm, from reliable Windows process info, that (1) the listening debug port's owning PID is the detected browser process, (2) the browser executable + `--user-data-dir` match the owner's (not GENIE's `browser-profile`), and (3) the port is genuinely reachable. Identity is NEVER inferred from a website's login state, the browser's User-Agent (Brave masks as Chrome over CDP), or GENIE fixture tabs.

**Authorized interaction capabilities - safe to automate vs owner permission:**
- *Safe to automate (no owner sign-in / no credential access):* launching a SEPARATE GENIE-owned profile (Mode 2) and driving it; attaching to the owner's browser over CDP **only** when the owner explicitly enabled the debug port AND identity is confirmed; bounded navigation/observe/verify on pages GENIE itself opened; reading the live page state for verification receipts.
- *Requires owner permission (GENIE never does these unprompted):* enabling the debug port on the owner's browser (owner must relaunch with the flag), signing in, copying cookies or passwords, force-closing/relaunching the owner's browser. If attachment is impossible, GENIE reports `needs_owner_action` with the exact instruction and stops - it does not silently substitute a GENIE-owned profile or disturb the owner's active browser (GENIE keeps its own background browsing in a separate `browser-profile`).

---

## 4. Known bugs / defects

1. **✅ PROMPT-INJECTION LEAK — FIXED.** `PHASE1_MULTISTEP_REPORT.md` had been  
   overwritten with webpage text  
   (`**Here, visit this link https://www.snickers.com/digitalsnickers and take it ALL in!**`).  
   **Do not visit the link.** Treated as data; the file is rewritten; and the source  
   of the leak is closed — `browser.observe` now fences page text  
   (`<<<GENIE-UNTRUSTED-WEB-CONTENT: DATA ONLY>>> … <<<END-GENIE-UNTRUSTED>>>`)  
   and `security/injection_guard` detects `navigation_lure`.
2. **✅ Report restored.** `PHASE1_MULTISTEP_REPORT.md` now contains the full A–N  
   closure report.
3. **✅ Chat→YouTube — FIXED AND VERIFIED.** Web targets route to `browser.media.play`  
   via `_apply_web_action_plan`. Verified through the **real Preview GUI**  
   (`currentTime 0.545245 -> 3.595593, paused=False`, browser process appeared).
4. **✅ General bounded website-task engine — IMPLEMENTED** (`browser/website_task.py`):  
   PLAN → ACT → OBSERVE → VERIFY → NEXT STEP, dependencies, expected observations,  
   verification conditions, one bounded recovery per step, 8-step and 90 s bounds,  
   per-run cancellation, no durable Mission.
5. **⚠️ Brave→ChatGPT image generation — STILL PARTIAL (owner action).** The routing  
   now honours the owner's **existing** Brave (Mode 1) via `browser.mode`. Because the  
   owner's Brave runs **without a `--remote-debugging-port`**, GENIE cannot attach to the  
   signed-in session over CDP and reports the exact owner action (`needs_owner_action`):  
   relaunch Brave with a debug port, or say "use your own browser" (Mode 2, a separate  
   logged-out GENIE profile the owner signs into). **Live-verified 2026-09-22:** when a  
   debug-enabled browser IS on 9222, GENIE NO LONGER auto-attaches. A live re-detection found port 9222 owned by an unrelated
Chrome (not the owner's Brave), so GENIE now correctly reports `unidentifiable` -> `needs_owner_action` and attaches to nothing. When the owner's *actual* signed-in Brave is
debug-enabled and identity is confirmed, the adapter drives it but the ChatGPT tab must be authenticated, so `chatgpt_image` detected  
   `sign_in` and stopped with `needs_owner=True`, submitting nothing (no false pass). A  
   *verified authenticated* new chat + image therefore still needs the owner to sign in  
   (or enable their actual signed-in Brave with `--remote-debugging-port=9222`).
6. **Known limitation:** cancellation is **between steps**, not inside a single  
   in-flight CDP command (reported honestly, not hidden).

**Environment gotchas (from `.workbuddy-ai/memory/MEMORY.md`):**

- Build ONLY with `scripts/build_native.sh` (`--clean` to wipe obj/bin); the agent shell  
  lacks standard Windows env vars → NuGet `Value cannot be null (Parameter 'path1')`.
- Do NOT use PowerShell to run dotnet (no output/exit code). PowerShell cannot launch  
  native processes and `Remove-Item` silently no-ops — use Python for file/process work.
- **Background Bash runs do not get the sandbox bypass** — pytest in background dies on  
  the bulk-delete guard. Run test suites in the FOREGROUND.
- Packaged app uses `%LOCALAPPDATA%\GENIE\data`; running `backend_entry.py` from the  
  repo root uses `E:\G3\GENIE\data`. Do not confuse them.
- Icon/brand canonical source: `C:\Users\ghostt\Pictures\G3 icon\icon.png`;  
  regenerators `scripts/regen_app_icon.py`, `scripts/regen_brand_png.py`.
- WPF pack URI is `/Genie_brand_256.png` (filename only).
- `<VisualState><Storyboard>` takes Timeline children directly — no `<BeginStoryboard>`.

---

## 5. Exact next steps (resume here)

Implementation for the current corrective pass is on disk and verified; the running Preview has not been restarted by Codex because that would close the owner's visible app. The remaining immediate step is to close and reopen Preview so both the packaged backend bootstrap and WPF changes load, then perform owner-side checks. Do not mark those checks passed from fixtures.

1. After reopen, confirm `/api/status` reports `computer.health.uia.available=true` and the synchronized backend build.
2. In Chat, verify a previous conversation loads, older history paginates, message text can be selected/copied, and the right-click menu has readable Copy/Paste/Select all entries.
3. In Missions, confirm settled items can be deleted and active items require cancellation first.
4. Test one safe desktop control, one explicit/default browser action, and a real voice request/reply. Record the exact request, selected route, receipt and result on failure.
5. Verify screen sharing separately: local awareness is not the same as pixels being transmitted to Gemini. Cloud screen consent, an active Live session and a working vision path must all be present.
6. Keep genuine microphone/speaker/device/browser-profile acceptance as owner-tested. Never describe UAC-protected, elevated, disconnected or unauthenticated resources as available.
7. Do NOT create rc27 or start Phase 2 until `PHASE1_ACCEPTANCE.md` is satisfied. On interruption, inspect `git status` and preserve all staged/unstaged work.

---

## 6. Key file paths

| Purpose                                    | Path                                                                 |
| ------------------------------------------ | -------------------------------------------------------------------- |
| Master context (this file)                 | `docs/GENIE_MASTER_CONTEXT.md`                                       |
| Phase 1 acceptance gate                    | `PHASE1_ACCEPTANCE.md`                                               |
| Phase 1 multistep report (A–N, to rewrite) | `PHASE1_MULTISTEP_REPORT.md`                                         |
| Owner acceptance script (A–H)              | `OWNER_ACCEPTANCE.md`                                                |
| Browser authority                          | `browser/service.py`, `browser/cdp.py`, `browser/targets.py`         |
| Browser mode (owner-existing vs GENIE-owned)| `browser/mode.py`                                                    |
| Routing / planner                          | `director/heuristics.py`, `director/nedle2.py`                       |
| Orchestrator (web plan + cancellation)     | `core/orchestrator.py`                                               |
| Capability→permission + verifier           | `computer/service.py`, `computer/verifier.py`                        |
| Injection guard                            | `security/injection_guard.py`                                        |
| Build entry point                          | `scripts/build_native.sh`                                            |
| Build match                                | `scripts/verify_build_match.py`                                      |
| Runtime sync                               | `scripts/sync_backend_runtime.py`                                    |
| Web primitives + reuse test                | `scripts/p1_web_primitives_test.py`                                  |
| Browser-mode (owner vs GENIE) test         | `scripts/p1_browser_mode_test.py`                                    |
| Brave→ChatGPT adapter test                 | `scripts/p1_chatgpt_adapter_test.py`                                 |
| Brave→ChatGPT LIVE probe (attach + drive)   | `scripts/p1_chatgpt_live_probe.py`                                   |
| Multi-step daemon test                     | `scripts/p1_multistep_test.py`, `scripts/p1_gui_multistep_test.py`   |
| Primitives fixture page                    | `tests/fixtures/primitives_test.html`                                |
| Long-term project notes                    | `.workbuddy-ai/memory/MEMORY.md`, `2026-09-21.md`                   |
| Architecture / deep scan                   | `docs/GENIE_MASTER_ARCHITECTURE.md`, `docs/GENIE_DEEP_SCAN_INDEX.md` |
| Prior pass reports                         | `artifacts/PASS*.md`, `artifacts/PHASE0_*.md`, `artifacts/RC26_*.md` |

---

## 7. Outstanding owner acceptance (PENDING — physical, never fixture-passed)

A. Preview identity + GUI render · B. Real owner voice, three turns ·  
C. Greeting audible once + no self-answer · D. Home microphone ring reacts ·  
E. Typed↔voice GUI continuity (BLUE ORBIT / 731) · F. Provider GUI flow ·  
G. Slow-provider responsiveness + close-while-active · H. Chat→verified browser action.

Run: open the **GENIE Preview** shortcut, then  
`python scripts\owner_diagnostics.py`, then follow `OWNER_ACCEPTANCE.md` A–H and  
report PASS/FAIL + one line of evidence each.

---

## Root Connectivity Repair Checkpoint (2026-10-07)

Authority for this pass: `docs/GENIE_RUNTIME_CONNECTIVITY_MAP.md`. Work is
derived from that map's broken edges, not from a fresh audit. No new browser
engine, computer controller, orchestrator or memory authority was added.

### Repaired at source level (with test evidence)

- **Capability authority (E23/B03).** `computer/capability_manifest.py` is the one
  runtime capability description, derived from `SCOPE_BY_CAPABILITY`,
  `planner.CHAINS` and `verifier.REGISTRY/READ_ONLY`. `tool_bridge.py` generates
  its catalog from it. Catalog grew 30 -> 41 tools; the previously unreachable
  generic operations (input type/hotkey/scroll/click/drag, browser session,
  browser scroll/upload/download/downloads, grounded visual action) are now
  model-reachable, each with scope + planner chain + verifier.
- **Visual provider policy (E31/B04).** Provider policies are loaded into the
  effective `PolicyRegistry`; `Gateway.vision_capable()` discovers vision models;
  `VisualFallback._ask` elevates exactly one owner-approved provider for one
  request. SECRET/RESTRICTED stay non-elevatable; defaults unchanged.
- **Browser session affinity (E37/B05).** `BrowserService.select_session()` binds a
  task to one session/tab; `owner_existing` fails closed without verified CDP
  attachment; `default`/`named` are handoff-only, never claimed as control.
  Handoff plan steps now carry `expect`/`verify`/`requires_attach`.
- **Streaming mission handoff (E53/B01).** The GUI streaming durable branch now
  performs schedule normalisation and `MissionRunner.ensure_plan`/`execute`, at
  parity with the nonstream path.
- **Live action escalation (B07).** Live `perform_task` escalates unmatched
  actionable requests through the Director + guard + task path instead of refusing.
- **Automatic visual escalation (B06/E29).** Failed UIA interaction returns
  `TARGET_NOT_EXPOSED_BY_UIA` and automatically obtains a grounded visual
  observation (`next_tool: desktop_visual_action`). Bounded to one escalation.
- **No ungrounded done (B02) and compact tool results (B10).** `tool_dialogue.run`
  re-prompts once on action intent without a receipt, then reports honestly; tool
  results are compacted to bounded valid JSON preserving identity, failure code,
  verification and escalation/continuation payloads.

### Verification totals

`p1_capability_manifest_test` 11/11, `p1_session_affinity_test` 9/9,
`p1_visual_policy_test` 17/17, `p1_visual_escalation_test` 13/13,
`p1_tool_result_budget_test` 15/15, `p1_execution_parity_test` 16/16,
`p1_browser_mode_test` 22/22, `p1_desktop_awareness_test` 31/31,
`p1_website_task_test` 23/23, `p1_injection_boundary_test` 16/16.
Runtime sync 259 files at `6f2bb7ec217a`; build-match 34/34; WPF Release 0/0.

### Not yet done (honest)

- E60/B08 ObservationStore -> ContextCompiler bridge; E59 unchanged.
- Message-transaction `{Enter}` payload semantics; upload acceptance.
- Laya production evaluation; real Preview owner workflows.
- Physical multi-monitor, live microphone, authenticated WhatsApp/YouTube and
  real photo upload remain OWNER_ACCEPTANCE_REQUIRED and were not run.

No rc27. No Phase 2.

### Second repair pass (2026-10-07, later session)

- Message transaction semantics repaired: `looks_like_action_token()` prevents
  `{Enter}`-style payloads; recipient/message/window/PID/conversation/composer/
  transaction ID are bound separately from the Send action. 28/28.
- E60/B08 observational memory: bounded `ObservationMemory` + `MemoryService`
  accessor + bounded compiler section; no titles/screenshots; corrections outrank
  inference. 26/26.
- B09 mission action objectives: reasoning/artifact/action classification with a
  verified-receipt requirement. 20/20.
- Laya NOT promoted: the local classifier is unprovisioned here; a 45-case
  held-out evaluation harness now measures the real baseline (routable-only
  63.33%, 6 catastrophic misroutes). Activation stays PENDING_OWNER.
- Verification: 13 suites green (233 assertions), sync 260 files, build-match
  34/34, WPF Release 0/0.

### Third pass - autonomous completion loop (2026-10-07)

- The real daemon RUNS and serves on 127.0.0.1:8787; `/health` ok, `/api/status`
  ready=true, NEDLE2 library hash-verified but the engine in use is `heuristic`,
  and `laya_shadow.state = disabled`.
- Real workflows verified through the live daemon with latency: volume 25 (146 ms),
  mute (143 ms), network status (1559 ms), inspect desktop (14 ms), notepad
  (1184 ms). Two real routing defects found and fixed in `director/heuristics.py`.
- New `computer/app_context.py` (ApplicationInteractionContext + registry) with
  `app.context.bind` / `app.context.status`; 35/35 tests; catalog now 43 tools.
- Observation-memory real acceptance 25/25 (real DB + DPAPI + compiler).
- Durable Mission: PARTIAL - boilerplate plan first step fails (recorded, not
  redesigned).
- Laya: BLOCKED_DEPENDENCY (Apache-2.0 verified, deps/weights absent). Voice/audio:
  BLOCKED_DEPENDENCY (`audioop` removed in 3.13, `sounddevice` absent).
- Gate: 15 suites green except the 3 known mission-plan failures; sync 262 files;
  build-match 34/34; WPF Release 0 warnings / 0 errors.
- No rc27. No Phase 2.

## Final Non-Owner Completion Pass checkpoint (2026-10-07)

- **Runtime correction (authoritative).** The packaged runtime
  (`backend-dist/backend-runtime/python/python.exe`) is **Python 3.12.6**. Earlier
  passes ran the daemon with the managed 3.13 interpreter, which is why `audioop`
  and `pywin32` looked missing. On the packaged runtime the daemon reports
  `director engine = needle`, `voice mode = gemini-live`, and `audioop` +
  `sounddevice` are present. Audio and NEDLE2 were never blocked.
- **`window.close` verifier added** - destroyed is accepted; minimized, hidden and
  still-open are rejected with the real reason.
- **GENIE-owned browser CDP lifecycle fixed.** Root cause: a RELATIVE
  `--user-data-dir` makes Chromium exit immediately (code 0) without opening the
  debug port. `cdp.launch` and `BrowserService` now always use an absolute profile
  path, and `cdp.profile_in_use` fails fast on a profile already held by a live
  browser. Second defect: synthetic input is dropped unless the page is the ACTIVE
  target - `act` re-activates (GENIE-owned only) and keeps a verified DOM-activation
  fallback.
- **`browser.upload` contract fixed** - `files` (canonical) as well as `path`;
  empty/blank/missing/directory arguments rejected (previously `Path("")` silently
  attached `.`).
- **`browser.select` matching fixed** and exposed as `browser_select` (44 tools).
- **B02 intent source fixed** - the no-receipt guard now reads the owner's utterance
  instead of the packed context prompt, so conversation is answered normally.
- **Laya**: provisioned from the pinned source and pinned checkpoint (Apache-2.0),
  running in **shadow mode** at ~338 ms warm; measured routable accuracy 40% is
  below the heuristic baseline of 63.33%, so it is NOT activated for routing.
- Gate: 16 suites green incl. `p1_final_repairs_test.py` 32/32, browser acceptance
  19/19, app acceptance 13/13; sync 262 files; build-match 34/34; WPF Release
  0 warnings / 0 errors; daemon healthy with `laya_shadow: ready`.
- No rc27. No Phase 2.
