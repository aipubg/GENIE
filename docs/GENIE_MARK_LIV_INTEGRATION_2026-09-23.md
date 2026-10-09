# GENIE: Voice, Startup Setup and Local Awareness

Date: 2026-09-23. Current source and test results take precedence over earlier completion reports.

## Current Build

- Source: `E:\G3\GENIE`, branch `upgrade/genie-continuity-ui`.
- Preview executable: `ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe`.
- Packaged backend: `backend-dist/backend-runtime/app`; 245 source files synchronized and hash-verified.
- Owner data: `%LOCALAPPDATA%\GENIE\data`. This is not the repository's development data directory.
- Existing staged/uncommitted developer work was preserved. No claim that every historical issue or roadmap item is complete.

## Implemented and Connected

### Gemini Setup

A separate native "Connect Gemini" dialog appears on startup until setup is verified. It is separate from adding specialist providers. The owner enters the key in a masked field; the backend validates a Live handshake, saves the key in the existing vault, and records completion against the current key/model. Failed validation does not replace the stored key or mark setup complete. The setup check sends no microphone audio, screenshots, camera frames or conversation history. Settings can reopen this dialog.

Windows vault writes now fail closed if DPAPI encryption fails, write atomically, and retain the previous cached credential if a write fails. Previously Windows could silently fall back to obfuscation. No ZIP credentials or private keys were copied or used.

### Natural Voice

`voice/live.py` owns a single Google GenAI SDK session: 16 kHz microphone input, 24 kHz streamed audio output, input/output transcription, Aoede default voice, selectable audio devices, VAD, bounded buffers, interrupt, session limits, and bounded resumable reconnects. It connects to the existing WPF Home and Settings, without importing MARK-LIV's avatar/UI.

Speaker mode suppresses microphone feedback while output plays; headset mode permits provider interruption. This is not a full acoustic echo-cancellation implementation. Manual interrupt clears local playback; it is not a guarantee of stopping generation or refunding tokens. Idle limit is 180 seconds, maximum session 900 seconds, both configurable. Reconnection never blindly replays an uncertain action.

The old local STT/TTS path remains selectable. The obsolete send-only Gemini transport no longer owns another microphone/session. Reference behavior was independently implemented against the SDK; MARK-LIV's CC BY-NC reference code was not indiscriminately copied.

### Fast Actions

Live `perform_task` uses the canonical GENIE direct action runner, CapabilityWorker, computer/browser services, safety checks and execution receipts. Supported simple actions do not create missions or call a second planner model. Tool-call receipts prevent duplicate execution of the same Live call ID. Cancellation reaches the same runner. Ambiguous/unsupported actions report a blocker rather than inventing a media-plugin requirement.

One GENIE-owned browser authority remains. Specialist agents and owner actions have not been given competing independent browser controllers. Explicit external browser/profile requests retain GENIE's existing identity boundaries.

### Desktop and Webcam

- Existing multi-display awareness/previews are reused. Cloud screen sharing is separately opt-in; redacted, labelled previews are sent at most every ten seconds during a Live session, with duplicate frames suppressed.
- Owner-enabled local webcam monitoring reuses PerceptionService with persistent OpenCV camera handles, bounded session renewal, selected device index, motion detection and heuristic local person detection. Camera sessions cannot silently replace another zone's session.
- Settings displays the local webcam preview, refreshes every three seconds while the Settings view is open, and offers pause and restart-on-launch preferences.
- "Send webcam snapshots to Google during Live voice" is a separate opt-in, off by default. A labelled webcam tile can join the Live visual input independently of screen consent. This enables model descriptions of visible objects, subject to a working camera, valid Live access and model accuracy.
- Camera/cloud gateway input now includes actual JPEG pixels. Previously the classifier prompt only described the frame dimensions. Default camera gateway analysis is now consent-gated.
- Network CCTV/RTSP UI and source handling were removed from this new work after the owner clarified that CCTV is for later. The current implementation selects one connected webcam at a time, not simultaneous multi-camera streaming. Bluetooth cameras work only if Windows exposes them as a supported capture device.

No owner-face enrollment, biometric identity recognition, reliable object inventory or guaranteed visitor identity has been implemented. A motion/person event must not be described as identifying the owner. Local HOG detection is a heuristic, especially limited for close-up faces; its confidence is not a calibrated probability.

### Offline Observation Memory

The existing MemoryService owns an encrypted local event timeline. Desktop changes record foreground process/title, display and timestamp; repeated observations are deduplicated. Pause/lock/excluded foreground policies are respected. Local camera motion/person events can also be stored. Windows DPAPI encrypts event payloads; default retention is 30 days with a 50,000-event cap. No raw video archive, keystroke recording or full-screen OCR archive is created.

This archive is deliberately excluded from automatic remote model context. It is evidence, not an automatically learned preference, authorization or model-weight update. Persistent memory is not reinforcement learning. Reliable personalization requires explicit promotion of observations into reviewed preferences and evaluation before policy changes; that broader feature remains open.

## Verification

- Full unit suite: 1,046 passed, 2 warnings (85.69 seconds).
- Voice + perception focused suite: 76 passed.
- New local-awareness/setup/vault/transport tests: 15 passed, including actual Windows DPAPI round-trip with synthetic data.
- WPF Release build: zero warnings, zero errors.
- Packaged runtime smoke: 30/30 passed; SDK, OpenCV and existing offline speech dependencies installed in the embedded interpreter.
- Native UI inspection: first-launch Gemini dialog rendered with masked key field, verification button and readable error/status area.
- Real packaged API action: "play Barsaat song on youtube" completed in 8.81 seconds, `heuristic-fast`, `mission_required=false`, one successful verified step. Media time advanced from 0.895796 to 2.466076 with `paused=false`. Browser status showed one page, "Barsaat - Banjaare (Official Video) - YouTube".
- Earlier real "open youtube.com" action completed in approximately 5.4 seconds. These are observed samples, not guaranteed latency for every site/network.
- Local timeline running: encrypted records stored without errors; cloud camera and screen sharing remained disabled.

## External Blockers and Remaining Work

The previously stored Gemini key was rejected by Google. The new setup dialog is open for the owner to enter their own valid key. Until that succeeds, real microphone-to-Gemini-to-speaker behavior, latency, interruptions and model visual understanding are not verified. Automated transport tests do not replace this hardware/cloud acceptance test.

No present Windows Camera/Image-class device was found and webcam index 0 could not be opened. Preview, real face/object observations and real-camera detection therefore remain hardware-unverified. Plug in a webcam, select its index and apply monitoring; Windows camera permissions remain under the owner's control.

Other uncompleted roadmap work includes simultaneous multi-camera selection, owner enrollment, CCTV connectors, proven automatic preference learning, safe general-purpose visual desktop operation beyond the supported actions, and Live delegation to durable specialist missions. There is no basis for calling this a complete autonomous AGI or promising that a keyboard/mouse will never be needed.

## Cleanup Boundaries

Production plugin discovery excludes `_test_` fixtures. Packaged crash/timeout fixture copies were removed; their source tests were retained. Duplicate perception initialization and the obsolete Live transport implementation were consolidated. Two unrelated regressions exposed by the full unit run were repaired: role/content history normalization and local model endpoints incorrectly acquiring a required API-key reference.

Disabled modules were not deleted merely for being disabled: missions, agents, competition, policy, memory and specialist routing are part of the stated roadmap. Deleting them without dependency evidence would damage existing functionality. No databases, user browsers, credentials or unrelated developer edits were removed.

## Follow-Up Acceptance

1. Complete the standalone Gemini setup and test a Hindi voice exchange from Home.
2. Interrupt a spoken reply, then request a supported browser action once; confirm one action receipt and one page.
3. Connect a webcam and verify local preview, camera pause/release and restart behavior.
4. Enable cloud webcam sharing only if desired; ask for a description of an object, not an unconfigured identity claim. Local capture is offline; Gemini analysis is not.
5. Validate two real displays, screen pause/exclusions and exact target-browser requests on the owner's environment.

SDK reference checked during implementation: https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk . Default model is editable (`gemini-3.8-live`); successful access depends on the owner's Google account and model availability.
