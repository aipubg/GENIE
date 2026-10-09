# GENIE Android node — build status

**Status: IMPLEMENTED, NOT COMPILED. Blocked on the Android toolchain.**

## What this is

The Android node is a *device* in the GENIE mesh, exactly like the reference node in
`devices/node.py`. It speaks the same protocol (`devices/contracts.py`):

```
{"payload": { ... }, "sig": "<hex HMAC-SHA256 over the canonical JSON of payload>"}\n
```

Because the protocol is the contract, the daemon cannot tell the two apart. That is what makes
the mesh testable on a PC today and Android-ready tomorrow.

## What is implemented

| File | Contents |
|---|---|
| `app/src/main/java/ai/genie/node/GenieProtocol.kt` | canonical JSON, HMAC signing/verification, frame encode/decode, PBKDF2 pairing-secret derivation, replay window, the `GenieNode` connect/serve/reconnect loop, command execution with idempotency and TTL |
| `app/src/main/java/ai/genie/node/DeviceCapabilities.kt` | `media.next`, `device.info`, `files.write` (app-sandboxed), `system.volume.set` — each performing a real effect and reporting whether it could be **verified** |
| `app/src/main/AndroidManifest.xml` | permissions the node asks for, and the service declaration |
| `app/build.gradle.kts`, `build.gradle.kts`, `settings.gradle.kts` | a real Gradle project |

## Why it is not compiled here

This machine has **no JDK, no Kotlin compiler, no Gradle and no Android SDK** (verified: `java`,
`kotlinc`, `gradle`, `adb` are all absent and `%LOCALAPPDATA%\Android\Sdk` does not exist).

Per the project rule, code that has never been built or run is **not** counted as a completed
feature. This node is therefore recorded as *implemented, unverified*, and the mesh it plugs
into is recorded as *verified* — see `docs/PHASE6.md`.

## How to finish it (one command on a machine with the SDK)

```bash
cd android/genie-node
gradle assembleDebug          # or: ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

Then, on the phone, start the node. It prints a pairing code; enter it on the PC:

```bash
python genie.py devices-pair phone_main --code 123456
python genie.py devices-grant phone_main          # grant the capability scopes
python genie.py devices-run phone_main media.next
```

## Conformance requirement

The Kotlin canonical JSON and HMAC **must** byte-match the Python implementation. A conformance
vector is pinned in `tests/unit/test_devices_core.py::test_protocol_conformance_vector`, which
asserts the exact canonical string and signature for a fixed payload. If the Kotlin side ever
disagrees, the handshake fails loudly rather than silently mis-authenticating — verify against
that vector before trusting a build.
