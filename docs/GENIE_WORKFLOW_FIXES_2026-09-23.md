# GENIE Workflow Repair Report

Date: 2026-09-23. Current source and executed checks are authoritative.
This is a repair of the existing system, not a claim that the entire Jarvis vision
or every historical issue is complete. Existing unrelated work was preserved.

## Start Here

Launch **GENIE Preview** from Desktop or Start Menu. It points to:
`E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe`

The old installed build was not overwritten. The packaged backend was synced to
the current working source (246 runtime files), and the WPF application rebuilt.
Preview was launched successfully; its `/health` returned `ok: true`.

Settings -> Desktop awareness controls capture, startup, preview, and pause/resume.
Computer -> Live view -> Desktop selects the local desktop; the display selector
switches between connected, authorized monitors.

As requested, the user's canonical data settings now enable local awareness,
automatic startup, and local preview. Remote visual consent remains OFF. This
does not automatically send screenshots to model providers or implement an
unrestricted visual control loop.

## Root Causes and Repairs

| Reported symptom | Cause found | Repair |
| --- | --- | --- |
| Voice hears but stops responding | `hinglish` was passed as an unsupported Whisper language; failed transcription left the turn in TRANSCRIBING | Normalize language aliases at the provider boundary; reset failed turns and resume continuous listening |
| Multiple YouTube tabs | Navigation selected a tab using the destination URL, creating new targets | Reuse the active tab; claim the launch target; reject occupied foreign debugging ports and clean up failed launches |
| Video remained paused | Playback promise was not awaited or initiated as a user gesture | Await playback with a timeout, capture rejection, and require actual media-clock advancement |
| Wrong browser after failed personal-browser attachment | Observer/fallback paths could launch a GENIE-owned browser after attachment failed | Preserve owner attachment mode, do not observe/verify failed actions, do not adopt unrelated debugging endpoints |
| Generic or unnamed "our browser" silently used a new profile | Owner intent was lost when the browser name was absent | Preserve owner intent and request the specific browser instead of silently changing identity |
| YouTube/song request said `media` plugin was off | Generic song classification routed browser playback to the optional device-media plugin | Route YouTube/song search directly to core `browser.media.play`; the plugin remains optional for OS media-key control |
| Browser task appeared frozen | Search/page/verification waits were serialized with large unbounded-looking delays | Bound media page waits and polling; failures now return a concrete receipt instead of holding the voice turn |
| Unknown website produced an invented action/URL | Website requests could fall through to model narration or unrelated media heuristics | Route explicit URLs deterministically; block unresolved website steps and dependent playback; strengthen receipt-based response instructions |
| Screen sharing did not show the desktop | Settings location/lifecycle and desktop preview transport were incomplete | Use canonical data directory, apply settings immediately, expose local frame/settings/pause/resume APIs, add WPF controls and monitor selection |
| Sensitive-window screenshot masking did not work | BMP bit depth was a tuple instead of an integer | Correct pixel redaction; verify masking; discard captures when redaction fails |
| Lock/pause information or images remained stale | Invalid Windows handle declarations and incomplete cache clearing | Correct 64-bit desktop handle types; fail closed when locked; clear buffers on pause/stop; preserve accurate locked status |
| Backend disappeared during continuous desktop preview | Repeated GDI captures left the selected bitmap attached to the memory DC and did not validate capture handles | Validate DC/bitmap/BitBlt results, restore the previous selected object, and release every GDI resource on every cycle |
| Provider errors appeared as empty responses or wasted retries | Streaming error text was ignored; Gemini invalid-key wording was not classified as credentials failure | Display streamed error messages; classify invalid Gemini keys as authentication failures |

Primary implementation locations: `voice/providers.py`, `voice/pipeline.py`,
`browser/service.py`, `browser/cdp.py`, `browser/mode.py`,
`browser/website_task.py`, `director/heuristics.py`, `core/orchestrator.py`,
`computer/desktop_awareness.py`, `computer/service.py`, `computer/executor.py`,
`core/lifecycle.py`, `core/ipc/server.py`, `security/trust.py`,
`models/failures.py`, and the existing WPF Settings/Computer/BackendClient files.
No parallel voice pipeline, computer authority, or agent framework was added.

## Verification Evidence

- 111 focused pytest checks passed, including 28 workflow regressions plus
  browser-target, voice-core, speech-recognition, and provider-failure tests.
- Packaged embedded Python runtime: 30/30 smoke checks passed.
- Release WPF build: zero warnings, zero errors. `git diff --check` passed.
- Existing targeted scripts: browser mode 22 passed, voice loop 15 passed,
  desktop awareness 31 passed. Voice-loop checks use test input, not a live mic.
- Real isolated browser test: three navigations reused exactly one tab.
- Real local media test: playback clock advanced while `paused` was false.
- Live YouTube test with an isolated headless profile: `Barsaat song` search and
  playback succeeded in one tab; time advanced from 1.138568 to 4.160645 seconds.
  This verifies the browser playback state, not audible output from the speakers.
- Fresh end-to-end `/api/chat` test with the Hindi request `YouTube par koi barsaat
  song chala do`: completed in 13.8 seconds through `browser.media.play`, with a
  receipt `currentTime 1.175919 -> 2.704339, paused=False`. The result was not
  dependent on the optional `media` plugin.
- Actual Windows SAPI generated speech; the packaged offline Whisper model
  transcribed "Hello. Please open the browser and play a song." successfully with
  `language='hinglish'`. No transcript injection or paid model call was used.
- Real Windows desktop capture: one connected display, valid 6,220,854-byte BMP.
  Pause removed preview buffers. The launched Preview app also returned a valid
  desktop frame with auto-start enabled and remote visual consent disabled.

Repeatable tests: `tests/unit/test_interaction_workflow_regressions.py` and
`scripts/verify_interaction_fixes.py`.

### Fresh Preview verification (2026-09-23)

- Release WPF build completed with `0 Warning(s), 0 Error(s)`.
- Packaged daemon became healthy on `127.0.0.1:8787` and reported `ready=true`;
  startup was about 1.3 seconds.
- A real `/api/chat` YouTube request used `source=heuristic-fast` and returned
  in about 9.3 seconds. Live YouTube navigation completed, but playback
  verification reported `currentTime 0 -> 0`; GENIE correctly returned FAILED
  instead of claiming that a song was playing.
- Real microphone start/stop completed without the earlier PortAudio process
  crash. Input ownership is now serialized and late callbacks are ignored
  during close.
- The packaged isolated browser/desktop verifier still passed: one browser tab,
  advancing media clock, one display preview, and preview cleanup on pause.

## Remaining Limits and Required Acceptance

1. Logs showed Gemini HTTP 400: invalid API key. Code cannot repair a revoked or
   invalid credential. Configure a valid provider key/model in Settings. Local
   STT/TTS working does not prove cloud conversational answers work.
2. End-to-end owner speech -> model response -> audible speaker reply still needs
   live microphone/speaker acceptance. Generated speech is only a component test.
3. Only one physical display was connected. Multi-monitor selection/filtering is
   implemented, but physical two-display capture, hot-plug, and control are not
   certified by this run.
4. An existing personal browser must expose a reachable, identifiable debugging
   connection. GENIE now reports a blocked connection instead of silently using
   another profile. It does not bypass browser protections or copy private profiles.
5. Unknown websites still need an exact URL or an unambiguous supported target.
   Reducing fabricated narration is not a general proof that models never hallucinate.
6. WPF compiled and the actual application/backend booted, but visible-layout and
   click-through acceptance of every screen was not performed.
7. Background desktop preview is local, low-frequency observation, not automatic
   understanding by all models. General visual reasoning/action grounding remains
   a separate integration and needs explicit provider/privacy decisions.
8. No claim is made that all crashes, all historical fixes, continuous agent
   competition, reinforcement learning, or the full project have been certified.
   Further failures should be addressed from reproducible logs and regression tests.

## Efficient Next Acceptance

Use the updated Preview, check the provider credential, then try one spoken
conversation, one YouTube request, pause/resume desktop preview, and an explicit
personal-browser request. Add a second display for multi-monitor acceptance.
Keep failed-action receipts and diagnostics; do not re-scan the full repository
or add competing execution paths for each new failure.
