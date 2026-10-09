# GENIE browser control and GitHub publish report

Current source: `E:\G3\GENIE` on `upgrade/genie-continuity-ui`. This report records source and build evidence only. No Preview, owner browser, WhatsApp, YouTube, or physical microphone acceptance was run in this pass.

## Repair evidence

| Owner symptom | Source-established cause and repair | Verification limit |
| --- | --- | --- |
| A different Chrome profile opened instead of the owner's browser | `computer/executor.py::_browser_action` prebound every unselected tool call to `genie_owned`; `director/heuristics.py::plan_web_action` converted a named existing browser navigation to an OS URL handoff and stamped unqualified follow-ups as `genie_owned`. Explicit existing-browser navigation now selects `browser.session`; follow-ups inherit the bound mode. `browser/service.py::select_session` requires an observed browser name instead of assuming Brave. | Existing browser CDP attach requires a verified PID/port and exact tab; ambiguous or unavailable attachment returns a specific block. UIA/consented visual fallback remains available through the existing desktop tools, subject to actual window grounding and grant. |
| Repeated profiles and Chrome restore prompt | `browser/service.py::ensure` rotated `browser-profile-2` and higher after launch failure. It now keeps one profile/port and stops on failure. `browser/cdp.py::profile_in_use` now parses quoted profile paths. `shutdown_owned` requests CDP `Browser.close` and does not hard-kill a running Chromium process when graceful close cannot be proved. | A conflicting profile holder remains a visible error. No owner profile is closed or copied. The owner's screenshot was not independently correlated to an exact process trace. |
| Reload refused; scroll command had no effect | `browser.reload` now flows through model declaration, bridge, scope, planner, executor, CDP, and verifier. It confirms the same tab and a new completed document. `browser.scroll` now consumes the bridge's actual `dy` key (previously ignored), accepts bounded `dx`, and verifies changed position; missing selector or boundary gives an unverified result. | A nested scrolling pane may still require an observed DOM/visual target. OS key delivery alone is never reported as a completed reload. |
| Wrong browser/tab or unverified response | Existing tab resolver, task lease, desktop authorization and receipt-based dialogue are retained. The selected task ID now reaches `browser.session`; direct owner-browser detection requires a resolved listening-port owner. The planner's `browser.session` step expects an attached session. | No GUI claim is made. Failed/ambiguous attach remains blocked with candidates or an exact reason. |
| WhatsApp/visual refusal | Existing `computer/messages.py` recipient/draft transaction, single-send approval, and visible-submission check were preserved. Existing `computer/executor.py` UIA-to-visual escalation was retained. `computer/visual.py` now checks remote consent before loading image processing. | No matching owner screenshot trace or live UI was available here. Exact WhatsApp/visual workflow is `PENDING_OWNER`; cloud image disclosure still needs application/provider-scoped consent. |
| `(No speech detected)` | No source trace tied the reported utterance to a dropped or altered command. Existing Voice-to-Chat routing and VAD were left intact. | Physical microphone acceptance is `PENDING_OWNER`. |

## Technical gate

- Python syntax/import: source `browser`, `computer`, `core`, `director`, `voice`, and `models` compile; packaged Python 3.12.6 imports the critical browser/computer/director modules from `backend-runtime/app` after source sync.
- Focused browser unit regressions: **53 passed, 6 skipped**. The new session/profile/scroll/reload cases: **11 passed**.
- The requested broader selection was first run with the packaged Python plus its user site packages: Pillow 12.3.0 and NumPy 2.5.3 loaded successfully. The original run was **93 passed, 11 failed, 0 skipped** in that selection; the earlier “12 failed, 6 skipped” count included six NumPy-gated cases and a system-Python Pillow mismatch. The corrected rerun is **110 passed, 0 failed, 0 skipped** (104 original cases plus six added negative authorization cases).
- Failure classification and fixes: the visual upload fixture expected the old generic approval path; it now provisions an application-scoped `screen_analysis` grant and retains the unauthorized-upload assertion. The owner-policy fixture omitted the required `application`; it now tests normalization, application/provider/destination pinning, expiry, and legacy-grant reauthorization. Message fixtures did not accept the production `allow_disabled` keyword and did not model Send being disabled before drafting; they now do, while wrong recipient, stale conversation, cancellation, exact draft, duplicate send, and delivery/read uncertainty assertions remain. The six apparent skips were dependency gates, not hardware: the packaged environment supplied NumPy and they all passed. No production authorization or visual code was weakened.
- Canonical `scripts/sync_backend_runtime.py --no-prune`: **264 source/runtime files match**; `--check --no-prune` passes. Source fingerprint: `da3ea4e96da5dffa0aedf997990961fd3ca7d6e5731c9036aec95759ecc14d72`.
- Build-match: **34/34**. WPF Release `net8.0-windows/win-x64`: **0 warnings, 0 errors**.
- Packaged model-facing tool catalog: **47** declarations, including the new verified reload operation.
- Preview: `E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe`. The executable SHA-256 is `2AF57AFD492FBCA6EB35E0CF5116FA63D45EF2F4EEF87AF19A988832A2DEE580`; DLL SHA-256 is `1241374B2508A9CA9259EC23778542D5E3343223FA7FF255FDDFD55AE71E2370`.
- No daemon/Preview process was launched because this request reserves GUI and live workflows for the owner.

## GitHub publication

- Target: `https://github.com/aipubg/GENIE.git`; the public repository was empty before this pass. The local branch/history is `upgrade/genie-continuity-ui`, starting from local HEAD `c188a00a8211c5aa7c2ce6c88362004ac25296cb` before the new commit.
- A new clean root snapshot was published to GitHub `main` because the older **local-only** history contains machine logs under `artifacts/` and agent memory under `.workbuddy-aii/`. The original local history and all working files remain intact; neither was force-pushed nor rewritten. The published snapshot has **1,048 files**, including Python source, tests, scripts, docs, UI source, installer source, safe assets and dependency manifests.
- Embedded Python runtime, build outputs, local browser profiles, logs, agent memory, memory/owner databases, credentials, and user model store remain excluded. A tracked generated WPF temporary project was removed from the Git index only; its working file was preserved. The clean snapshot omits the historical LFS model payloads; persistent provisioning scripts and model metadata remain in source. No LFS object transfer was needed for the published root.
- Complete reachable Git history was scanned for key-format signatures without displaying values. Three matches were documented pattern/test fixtures; no live credential was identified by that scan. Git LFS `fsck` passed. Laya and Vosk LFS artifacts are locally available in history; their upstream model metadata lists Apache-2.0. The current source tree excludes duplicated model payloads and uses persistent model provisioning.
- Initial safe source commit: `12f2a4bb64a74f727a38613c9efa77d75b120497`, pushed to `refs/heads/main` without force. Regression repairs and the updated report are published in final remote commit `e42de95195d527e6c916909c596b6682d10a75de`; `git ls-remote origin refs/heads/main` matches that SHA.

## Owner manual verification — PENDING_OWNER

1. In the current signed-in Brave window, say/type: “Mere existing Brave ke isi tab mein scroll down karo.” Verify the same tab scrolls and no Chrome window opens.
2. “Isi tab ko reload karo.” Verify the same URL/tab reloads and the page is freshly visible.
3. “Isi Brave tab mein YouTube par [exact video title] search karke video chalao.” Verify correct result and advancing playback.
4. “Isi page par [exact visible control] click karo.” Verify the requested control's actual state changed.
5. “Ab mere existing Edge/Firefox mein switch karo.” Verify the named browser only, or a precise unsupported/attachment result.
6. In WhatsApp, “Meri [exact recipient] conversation kholo.” Confirm the visible header is the exact recipient.
7. “Is conversation mein exact draft likho: [exact text], abhi mat bhejo.” Confirm the draft and recipient.
8. “Is exact draft ko bhejo.” Approve once in the frozen transaction, then verify one visible outgoing message; delivery/read remain unknown without separate evidence.
9. Repeat browser follow-ups and confirm no unexpected Chrome/profile appears.
10. By microphone, say a Hindi/Hinglish browser command and confirm its recognized text enters the same action pipeline and produces a grounded receipt.

Capture the GENIE trace/request ID, selected browser/app window identity, error code, and visible before/after state for each failure. Do not share passwords, messages, or unredacted screenshots in the report.
