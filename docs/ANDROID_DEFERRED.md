# GENIE — ANDROID DEFERRED BACKLOG

**Owner decision (2026-09-17): the final Android application is intentionally postponed.**

This is a roadmap decision, **not** a failure and **not** a blocker. The Device Mesh, the device
protocol, the registry, pairing, routing and the offline queue are complete and verified. The
Kotlin node exists as a foundation. The final application will be built later as its own project,
against the contracts that already exist.

```
GENIE Core
    ↓
Device Mesh            ← COMPLETE, verified on a real machine
    ↓
Device Contract        ← COMPLETE, frozen enough to build against
    ↓
Android Application    ← DEFERRED BY OWNER (later, separate project)
```

**The future Android app must not require redesigning GENIE Core.** It is another GENIE *body*:

```
Android App
  ├── GENIE UI
  ├── voice interface
  ├── device node          (android/genie-node — foundation exists)
  ├── phone control adapter
  ├── camera/mic adapter
  ├── notifications
  ├── accessibility/device control
  └── Device Mesh client
```

Memory · NEDLE2 · missions · agents · skills · provider routing · core intelligence stay in
GENIE Core. Desktop, Android, tablet and a future 3D UI stay synchronised through the same backend.

---

## Standing rule for every later phase

When a phase needs something that only the unfinished Android app can provide:

1. **do not stop**
2. record the interface/contract requirement here
3. implement the PC/local/test side now
4. continue the current phase

---

## Deferred items

| ID | Status | Item | Why it is deferred | What exists today | What the app must implement |
|---|---|---|---|---|---|
| **ANDROID-APP-FINAL** | `OWNER-DEFERRED` | The complete Android application (UI, onboarding, permissions UX, packaging) | GENIE Core is still evolving; building the app now would mean rebuilding it later. Dependency: **GENIE core maturity / later application phase** | `android/genie-node/` — protocol, capabilities, foreground service, Gradle project | The whole app shell, consuming the existing mesh + contracts |
| **ANDROID-APK-BUILD** | `OWNER-DEFERRED` | Compiling/packaging the node into an APK | No JDK/Kotlin/Gradle/Android SDK on the development machine, and installing them is explicitly out of scope | Complete Kotlin source + a pinned protocol conformance vector to build against | `gradle assembleDebug` on a machine with the SDK |
| **ANDROID-CAMERA-STREAM** | `BACKLOG` | `perception.camera.stream` — a live/on-demand camera frame source | Needed by Phase 8 (perception). Phase 8 proceeds with PC webcam + local test sources | `screen.capture` pattern + the generic device contract | Implement `perception.camera.stream` as a node capability |
| **ANDROID-MIC** | `BACKLOG` | `perception.audio.capture` — phone microphone as a voice input | Voice already works on the PC | Phase 3 voice pipeline + `VoiceInputProvider` contract | Implement the capability behind the same contract |
| **ANDROID-NOTIFICATIONS** | `BACKLOG` | `notifications.read` / `notifications.post` | Phase 9 (proactivity) can deliver on the PC first | Phase 9 notification system (PC) | Node capability + owner-granted notification access |
| **ANDROID-SCREEN-OBSERVE** | `BACKLOG` | `screen.observe` on the phone | Phase 8 proceeds with the PC | `screen.capture` + the device contract | Node capability via MediaProjection + explicit consent |
| **ANDROID-A11Y-CONTROL** | `BACKLOG` | `uia.*` equivalents for Android (touch/a11y actions) | No phase depends on it yet | UIA on the PC | Accessibility service, registered only after the owner grants it |
| **ANDROID-FILES** | `BACKLOG` | Shared-storage file exchange | App-sandbox `files.write` already exists in the node | `files.write` inside the app sandbox | Scoped-storage file access with owner consent |
| **ANDROID-MEDIA-CONTROL** | `PARTIAL` | Rich media control (now-playing metadata, session control) | `media.next` exists and honestly reports `verified = false` because the platform gives no read-back | `media.next` via AudioManager key events | MediaSession integration so the action becomes verifiable |
| **ANDROID-SYNC-CLIENT** | `BACKLOG` | The app side of sync v1 (see `docs/PHASE6.md` §10) | Sync v1 is implemented and verified daemon-side; the phone is just another peer | `devices/sync.py` + conflict policies | Consume the sync API from the app |

---

## How to close this backlog later

```bash
# on a machine with a JDK + Android SDK
cd android/genie-node
gradle assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk

python genie.py devices-pair phone_main --code 123456
python genie.py devices-grant phone_main
python genie.py devices-run phone_main media.next
```

Verify the build against the pinned conformance vector first
(`tests/unit/test_devices_core.py::test_protocol_conformance_vector`) — if the Kotlin canonical
JSON or HMAC ever disagrees with Python, the handshake fails loudly rather than silently
mis-authenticating.
