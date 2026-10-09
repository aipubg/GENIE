# GENIE — PHASE 6: DEVICE MESH (+ ANDROID NODE)

**Status: 🟩 COMPLETE for the mesh · 🟨 Android node implemented, not compiled (toolchain blocker)**

> **Rule that shaped this phase:** a device is not a special case of GENIE. It is the *same*
> capability contract reached over a channel — `media.next(phone_main)` must work exactly like
> `media.next(pc_main)`, with the same permission check, the same verification and the same audit.

---

## 1. What a device is

One device, one **manifest**: what it is, what it can do, how much it is trusted.

```json
{
  "device_id": "phone_main", "name": "Pixel", "type": "android",
  "trust_tier": "owner_secondary", "platform": "android",
  "capabilities": ["media.next", "device.info", "files.write", "system.volume.set"],
  "protocol_version": "1.0", "transport": "tcp"
}
```

GENIE is device-agnostic; the **node** does the platform work. There is no per-platform
architecture, and no per-platform protocol.

## 2. The mesh

```
                    ┌──────────────────────────────┐
   capability  ───▶ │  DeviceService               │
                    │  registry (truth)            │
                    │  PTE scope  device:<id>:<fam>│
                    │  idempotency + TTL queue     │
                    │  verification + audit        │
                    └──────────────┬───────────────┘
                                   │  signed JSON frames over TCP
                    ┌──────────────┴───────────────┐
                    │  DeviceServer (accepts nodes)│
                    └──────────────┬───────────────┘
              ┌────────────────────┼────────────────────┐
        pc_main (in-process)   phone_main            laptop_main
                               (Android node)        (reference node)
```

The **node dials the daemon**. That single decision removes inbound ports, router configuration
and NAT traversal from the LAN case, and keeps a relay (for when the phone is away) as a later
transport behind the same interface rather than a second protocol.

### Files

| File | Responsibility |
|---|---|
| `devices/contracts.py` | manifest, command/result envelopes, statuses, trust tiers, HMAC signing, replay window |
| `devices/registry.py` | authoritative device list, state, capabilities, pairings (secret in the vault) |
| `devices/protocol.py` | TCP transport, handshake, connection registry, pending-command futures |
| `devices/service.py` | routing, permissions, offline queue, idempotency, verification, audit |
| `devices/node.py` | the node runtime + `ReferenceHandler` (real local operations) + CLI |
| `core/db.py` | migration `006_devices` (`devices`, `device_pairings`, `device_commands`) |
| `agents/runtime.py` | `TaskType.DEVICE_ACTION` now routes to the mesh |
| `core/lifecycle.py` | service wiring, router vocabulary, shutdown |
| `core/ipc/server.py`, `genie.py` | `/api/devices*`, `devices*` CLI |

## 3. Security model

| Concern | How it is handled |
|---|---|
| **Pairing** | the node shows a 6-digit code; the owner enters it on the PC. Both sides derive the secret with PBKDF2 (`device_id`-bound), so **the code never crosses the wire** |
| **Authentication** | every frame is HMAC-SHA256 signed; an unsigned or forged frame is rejected before it is parsed |
| **Trust** | `untrusted / guest / family / owner_secondary / owner_primary`. **A node's own manifest never decides its trust** — a reconnect cannot promote it (A-066) |
| **Permissions** | default deny. `device:<id>:<family>` must be granted; pairing itself grants nothing |
| **Replay** | monotonic counter per direction + `command_id` dedupe window |
| **Idempotency** | an `idempotency_key` returns the previous outcome instead of re-executing |
| **Offline work** | queued with a TTL; **expired commands are never delivered** |
| **Untrusted principals** | trust is checked *before* capabilities, so a stranger learns nothing about a device's surface |
| **Secrets** | only a vault reference and a fingerprint are stored; a database copy cannot impersonate a device |

## 4. Verification

A device result counts as success only when `ok AND verified`. A node that cannot observe its own
effect must return `verified = false` — the Android `media.next` implementation does exactly that,
because the platform gives no read-back for a media key. Claiming success there would be a lie.

## 5. Verified on this machine

`tests/e2e/test_devices_phase6.py` — **20 tests, all green**, each using a real node over real TCP:

* an unpaired node is refused and served nothing
* pairing brings a real node online; a stranger never connects
* a real command executes on the node and the **file really exists in the node's sandbox**
* default deny before the grant, success after it
* undeclared capability refused by the service *and* by the node
* idempotency: the same key executes once; a replayed `command_id` is answered from cache
* an expired command is never executed
* offline → queued with a TTL → delivered and completed on reconnect
* stale queued work expires and **never reaches the device**
* a queued command can be cancelled
* a frame signed with the wrong secret is rejected
* a disconnect fails in-flight work instead of hanging, and the device does not stay "online"
* the real capability worker routes a `device_action` end to end

`tests/unit/test_devices_core.py` — **46 tests**: manifests, scopes, signing, the replay window,
envelope TTLs, the registry, trust tiers, pairings in the vault, routing refusals, and a pinned
**protocol conformance vector** so the Android implementation must match byte for byte.

## 6. Android node — implemented, not compiled

`android/genie-node/` contains a real Gradle project:

| File | Contents |
|---|---|
| `GenieProtocol.kt` | canonical JSON, HMAC, frame codec, PBKDF2 pairing, replay window, the connect/serve/reconnect loop |
| `DeviceCapabilities.kt` | `media.next`, `device.info`, `files.write` (app-sandboxed), `system.volume.set` |
| `GenieNodeService.kt` | foreground service so the node survives the screen turning off |
| `AndroidManifest.xml`, `*.gradle.kts` | permissions and build config |

**It is not compiled.** This machine has no JDK, no Kotlin compiler, no Gradle and no Android SDK
(verified). Per the project rule, unbuilt code is not a completed feature, so it is recorded as
*implemented, unverified* — **not** as done. Finish it on a machine with the SDK:

```bash
cd android/genie-node && gradle assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
python genie.py devices-pair phone_main --code 123456
python genie.py devices-grant phone_main
python genie.py devices-run phone_main media.next
```

The protocol is the contract, so the daemon cannot tell the Android node from the reference node.
That is what let the whole mesh be verified today without a phone attached.

## 7. Defects found while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-065 | The pre-pairing `pair_required` reply was signed with a constant the node could not verify, so an unpaired node silently ignored the handshake | a shared `UNPAIRED_SECRET` for pre-pairing frames; the node tries both |
| A-066 | **A node could promote itself.** `hello` carries the node's own manifest, whose trust tier defaults to `untrusted`; accepting it let any node reset its own trust by reconnecting | the stored tier is authoritative; only `set_trust()`/`pair()` may change it |
| A-067 | Pairing before the first `hello` lost the device's capabilities, and an *unknown* surface was treated as a *missing* capability, so legitimately queued work was refused | `pair()` accepts declared capabilities; an undeclared surface defers to the node, which is the authority on its own capabilities |
| A-068 | `computer/state.snapshot()` called `foreground_window()` **twice**; if the foreground window vanished between the calls, the second returned `None` and the snapshot crashed mid-action, taking a real command down | read the API once |

## 8. Deliberately deferred (interfaces preserved)

The master spec (§6) lists more than a LAN-first mesh needs on day one. These are **not** built
yet, and nothing here blocks them:

| Deferred | Why it is safe to defer | Hook that exists |
|---|---|---|
| mDNS/UDP LAN discovery | pairing by code works and is more secure than trusting a beacon | `DeviceServer.handshake` + the registry |
| Remote relay / STUN | the node dials out, so LAN needs no traversal | `transport` field on the manifest |
| mTLS with per-device certificates | HMAC over a PBKDF2 secret already authenticates and integrity-protects | `sign_payload` / `verify_signature` |
| Bandwidth classes (control > voice > telemetry > media) | no media streaming in the mesh yet | command envelopes carry a capability, so a class can be derived |
| Raspberry Pi / camera / home-automation nodes | they are nodes like any other | the node contract is platform-agnostic |

## 9. Verification summary

```
tests/unit/test_devices_core.py    46   contracts, signing, replay, registry, trust, pairings, routing, conformance vector
tests/e2e/test_devices_phase6.py   20   REAL node over REAL TCP: pairing, execution, verification, queue, TTL, replay
tests/contract/test_ipc_api.py    +11   /api/devices* HTTP surface
genie.py devices-selftest               pair a real node → run a real capability → verify the file exists
```

**Phase 6 exit gate (mesh): PASS** — a capability is routed to a real remote node, permission-checked,
idempotent, replay-protected, delivered across an offline gap, and verified against the node's own
state, with the refusal paths proven.
