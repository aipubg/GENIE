# GENIE Connectivity Repair - 2026-09-24

## Delivery Status

This is an incremental corrective build, not a claim of universal desktop control or completed Phase 1 acceptance. The owner requested that further manual screen testing stop and that the updated application be delivered for their own testing. No owner applications were uninstalled, no network settings changed, and no credentials replaced during this pass.

The heavily modified working tree was preserved. A tracked-file checkpoint is at `artifacts/checkpoints/before-universal-connectivity.patch`; this is not a complete backup of untracked files or user data.

## Actual Disconnections And Repairs

| Area | Before | Current implementation |
| --- | --- | --- |
| Live actions | Three tools, with ordinary actions routed into a narrow text heuristic | Shared typed bridge in `computer/tool_bridge.py`; Windows, app, browser, research and accessibility calls reach ComputerService |
| Typed actions | Reasoning responses explicitly said they could not execute tools | Bounded six-round tool dialogue for explicit action requests, using the same bridge as Live; ordinary conversation retains streaming |
| App discovery | App Paths, shortcuts and shallow executable scan; no MSIX Start registrations | Cached Get-StartApps index, normalized/fuzzy names, product suffix matching, ambiguity candidates, refreshed search before reporting not discovered |
| App reporting | Missing index match was called "not installed" | Precise not-discovered versus ambiguous result; no arbitrary first candidate |
| Explorer | Explorer shell process could satisfy visible-window verification | Shell surfaces are labelled and excluded from application-open verification; background process is not a File Explorer window |
| Named browser | A named YouTube browser could imply a separate profile | Named browser means owner mode unless a separate profile is explicit; basic navigation can use registered browser URL handoff |
| Default browser | GENIE/CDP Chromium choice could be confused with Windows default | Windows URL association handoff, distinct from page-load or browser-control verification |
| Third-party browsers | Fixed Chromium brand list | Windows StartMenuInternet registration discovery for named URL handoff; existing accessible windows use Windows UIA, not a second browser engine |
| Search | Generic search absent from Live | Shared search/read tools, source URLs, current-page extraction, access-gate inspection; no mission required |
| Search correctness | First live probe extracted Google's CAPTCHA and incorrectly marked search successful | Gate check added before extraction; Bing probe completed in 3.31 seconds in an isolated backend browser |
| Desktop interaction | Existing UIA functions inaccessible to voice | Window enumeration, bounded semantic control discovery, fresh element read/focus/invoke/value operations through existing executor |
| UIA safety | Expired handles could remain valid until another registration; ambiguous titles picked first window | TTL checked on lookup; multiple matching windows rejected; password controls excluded from bridge; literal typing no longer interpreted as pywinauto key syntax |
| Settings/devices | No conversational connection | Windows Settings page navigation, microphone/speaker enumeration, read-only network-interface status |
| Local folders | `files.mkdir` existed but neither conversational catalog exposed it | Shared create/list/find/location tools use the existing executor; redirected Desktop/Documents paths are resolved from Windows registration |
| Windows switches and tabs | Invoke/value calls did not express toggle or selection state | UIA absolute on/off and selection patterns, reported current states, and independent state verification; no blind repeat toggle |
| Sensitive desktop controls | Permanent confirmation-required response, with no continuation | Exact-action WPF confirmation resumes the waiting conversational tool; cancellation, expiry, agent requests and changed targets cannot execute it |
| Stale capability claims | Prior assistant refusals persisted in recent Live history | Current tool catalog is explicitly authoritative; matching typed tools precede the older `perform_task` fallback |
| Uninstall routing | Explicit app removal could match the installed-app open branch and relaunch the target | Removal requests stay on the inspect-and-confirm reasoning path; they cannot be classified as app-open actions |
| UI Automation errors | Enumeration swallowed COM/Windows permission errors and returned an empty list | Per-request UIA errors now preserve `access_denied`, provider-unavailable and no-match as different results |
| Hindi/Hinglish action routing | Common toggles, app management, files and controls could fall through to a generic answer | Typed action detection now covers owner-reported verbs and forces an available tool call for explicit requests |
| Screen sharing | Settings control existed but was difficult to discover | Home checkbox clearly names Gemini cloud sharing; inspect-desktop can immediately deliver authorized previews; existing monitor redaction/consent retained |
| History | Bounded recent context, no history UI | Separate durable archive, migration of surviving turns, paged older messages; empty historical voice input rows hidden |
| Selection/menu | TextBlock messages, unreadable native menu | Read-only selectable TextBoxes and explicit themed Copy/Paste/Cut/Select All menu |
| Missions | Pause/cancel only | Delete settled missions, refusal while a step is running; corrected paused-state Resume visibility |
| Provider UI | Raw JSON/HTML could appear as a status message | Compact readable provider error summaries |

## Verification Evidence

- Final full unit suite: 1086 passed in 106.22 seconds. Two warnings: audioop deprecation and pywinauto COM initialization. These are not zero-warning acceptance.
- Native WPF validation build: zero warnings and errors before final packaging.
- Final Release WPF build: zero warnings/errors; packaged runtime smoke 30/30; source/runtime manifest matched across 252 files.
- Packaged interpreter enumerated 31 audio-library devices and four Windows network adapters without changing any setting.
- Focused post-suite regressions: 120 passed after the folder, toggle, selection and confirmation changes. Includes a real directory creation in an isolated redirected-Desktop fixture, toggle idempotency/state mismatch, changed targets, approval expiry and replay rejection. No personal messages sent or network changes made.
- Screenshot follow-up regressions: 50 focused connectivity tests pass after explicit-action routing, uninstall-route and UIA access-error reporting changes.
- Current local database has standing owner grants for app, UIA, files and browser scopes. The latest Preview log recorded UIA read calls as allowed; no permission-engine denial was logged for the supplied screenshots. Screenshots predate the latest refreshed Preview, so they do not identify which post-build operation Windows itself denied.
- Actual owner app index found WorkBuddy AI at its Start Menu shortcut, with `WorkBuddyAI.exe` as the resolved process name.
- Actual owner app index found WhatsApp's MSIX AppsFolder registration.
- Actual registered browsers found: Mozilla Firefox, Brave, Microsoft Edge. No claim that all their profiles/private windows have been controlled.
- Gemini Live handshake succeeded using the owner's current saved key and configured model. This is not a microphone-to-spoken-action acceptance test.
- Before the owner stopped further screen testing, the actual Preview displayed restored historical messages and a readable dark Copy/Paste menu.
- Search probe uses the existing ComputerService and browser authority with a disposable owned browser profile. It is not proof of owner-profile browser control or a complete multi-source research conversation.

## Laya

Original source: `E:/G3/references/laya`, revision `1e28ac20c0896b1c37a744cd11f740eb98f8b178`.

Multilingual checkpoint: `E:/G3/models/laya-pinned/multilingual`, model revision `aa8c91ca088ec597df95a0d1c76b3063cb2ae5e8`.

Local CPU benchmark: model load 8274.5 ms; four inferences 96.7, 98.3, 102.2 and 102.7 ms. Three classifications matched the expected class. "Every morning check the weather and send me a summary" was incorrectly classified as simple_action at confidence 0.6348.

Therefore Laya is integrated as optional shadow-only observation, disabled by default. It cannot execute tools, change permissions or replace the authoritative route. NEDLE2 was not removed. Configure `director.laya.shadow`, `python`, `source`, and `model` only for developer evaluation. The isolated shadow process has a 30-second deadline and one in-flight sample; it is not a warm production router. Do not advertise these four examples as a general Hindi accuracy benchmark.

## Important Remaining Limits

1. URL handoff proves Windows accepted a request, not that a page loaded, the right account is signed in, or media played. Receipts explicitly separate these facts.
2. Existing/private browser control depends on authorized CDP or accessible Windows controls. Arbitrary private windows, notifications and all third-party browser interactions have NOT passed owner acceptance. No cookies or browser secrets are copied.
3. The existing browser authority cannot silently switch from an attached owner session into a separate profile. This now returns a precise blocker rather than navigating the owner session accidentally.
4. Sensitive desktop controls now pause for a WPF Yes/No confirmation tied to the waiting action, expire after 90 seconds, and recheck target identity before proceeding. This follows the existing local desktop IPC trust model; it is not an authenticated remote approval service. Browser DOM consequential actions still require direct owner interaction. Verified software installation/uninstallation, Windows default microphone switching and actual Wi-Fi/airplane/Bluetooth acceptance remain unproven. Accessible toggle/selection controls can now be operated and checked, but the actual installed application's accessibility support determines availability.
5. Typed Chat has bounded tool turns; it is not a fully general asynchronous multi-agent desktop controller. Partial operations and budget exhaustion must not be called completed work.
6. Screen sharing sends authorized previews to Google during Live voice. Local storage does NOT make cloud voice/vision processing offline. Continuous previews are periodic, not a frame-perfect live video stream. Camera consent stays separate.
7. Old turns already compacted before this migration cannot be reconstructed. New archived messages are separate from the model's bounded context budget.
8. The current bridge is a shared conversational catalog, not a completed schema/availability manifest for every legacy capability. Some legacy capabilities remain agent/executor-only.
9. No blanket deletion of disabled roadmap components was performed. Moving the new voice-only bridge into the computer layer removed a duplicate ownership boundary; speculative removal of existing agents, memory or providers would risk regressions.

## Follow-up Live Runtime Check

The running Preview exposed a concrete packaging defect after the original repair report:

- Its embedded Python runtime contained `pywinauto`, `win32api.pyd`, and pywin32 DLLs, but did not process the runtime's `pywin32.pth`. UI Automation therefore reported `No module named 'win32api'`; the conversational desktop-control tools could not operate any UIA controls even though their code and binaries were present.
- `backend_entry.py` now processes the embedded runtime's `site-packages` `.pth` files before importing GENIE. The packaged runtime was synchronized and a subprocess acceptance test verified `win32api`, `pywinauto`, and real UIA window enumeration. This fixes the shared desktop UIA import blocker; it does not bypass Windows integrity/UAC boundaries.
- The live Gemini Live handshake succeeded for the configured model/key without opening the microphone. The voice service itself was idle (`connected=false`, `screen_shared=false`) during inspection, so no audio-turn or continuous remote screen-sharing acceptance is claimed.
- At the time of inspection, `/api/metrics/actions` showed zero actions in that daemon's current metrics window. That means these logs cannot validate the owner's cited refusal turns; those messages may have been answered without an executor call or in another Preview session.
- The updated runtime files are on disk, but the already-running backend had imported the prior startup code during this inspection. Close and reopen GENIE to load the bootstrap fix; do not interpret the current process's cached `uia` status as post-fix.

The installed runtime still cannot promise unrestricted access. Windows UAC/elevated applications, protected desktop surfaces, disconnected devices, missing owner sign-in, and high-impact operations remain subject to OS boundaries or explicit confirmation. We must report each capability's real readiness instead of describing the whole machine as fully accessible.

## Completion Of Earlier Follow-Ups

- Typed Chat and Gemini Live both dispatch through the same `computer.tool_bridge` catalog and `ComputerService`; Live call-ID receipts prevent repeating an uncertain action. Existing focused tests cover both routes. Voice and Chat write to the same persisted owner conversation history.
- History pagination and terminal mission deletion were already present. Chat now reloads history when an already-loaded view gets a new data context; mission delete is disabled in the UI until the row is terminal (the backend independently rejects active deletion).
- Chat message textboxes now explicitly retain inactive text selection and use the dark edit context menu; the menu itself has explicit surface/text/border colors instead of relying on the system popup palette.
- Laya's source and pinned checkpoint were benchmarked. Because the tested classifier had a mission/action misclassification, it remains optional shadow-only and cannot route or execute owner actions. NEDLE2 remains authoritative; no duplicate router was introduced.
- The latest validation was 119 focused Python tests passed (one Python `audioop` deprecation warning), followed by a native WPF Release build with 0 warnings and 0 errors. UIA packaged-runtime regression passed, as did runtime sync and build-match checks. The live desktop process was not restarted or manually interacted with; owner verification is still required after reopen.

## Owner Trial

Use the updated GENIE Preview. Try installed-app lookup/opening, system volume, a named/default browser, current web search, and an observed application control. On Home, enable "Share screen with Gemini" only when remote previews are intended. Try Chat history/text selection and deleting a cancelled mission. Report the exact request and actual result for any failure; Phase 1 remains open until those real scenarios pass.

## Follow-up Regression Results

- Fixed a real dry-run ordering bug: the computer planner previously resolved `application.open` against the installed-app index before the executor could enter dry-run, so a missing local Chrome installation made a no-side-effect simulation fail. `dry_run` now skips live app resolution while regular execution retains discovery and its honest not-found result.
- Updated legacy Chrome-open tests to the current direct-action contract: deterministic one-step actions complete without creating durable Missions. The IPC endpoint likewise must not add a routine browser launch to mission history.
- Focused regression command covering packaged-runtime bootstrap, Chat selection, desktop snapshot, computer contracts, chat IPC and Chrome-open flow: 15 passed.
- `verify_build_match.py`: 34/34 passed; source/runtime synchronization reports 252 files matched; `compileall` and `git diff --check` completed without errors. The native WPF Release build had already succeeded with zero warnings/errors after the UI changes.
- A broader `pytest -q` run before the dry-run/test-contract correction reported 1,636 passed, 16 failed, 79 deselected. Failures included outdated plugin tests that expect `_test_*` directories to be discovered even though the production registry intentionally excludes those folders, legacy persistent-Mission expectations for direct actions, and isolated workspace/test-order assumptions. The four direct-action failures were addressed and the affected tests now pass; the full suite was not rerun after those corrections, so it is not claimed green.
- The packaged UIA smoke test verifies embedded-runtime imports; separate local inspection previously confirmed UIA could enumerate windows after bootstrap. Reopen the Preview to load the synchronized runtime. Real microphone turns, browser identity/session behavior, installed-app open, network/settings changes, camera and screen-share use still require owner-side acceptance and were not represented as passing here.
