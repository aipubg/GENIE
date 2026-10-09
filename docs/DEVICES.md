# GENIE — DEVICES

Authoritative: master spec §6. Quick-reference for device/embedded work.

## Principle

GENIE device-agnostic bolta hai: `media.next(phone_main)`.
Platform-specific kaam **adapter** karta hai. "Alag Raspberry architecture" nahi.

## Capability manifest (har device connect par)

```json
{
  "device_id": "phone_main",
  "type": "android",
  "trust_tier": "owner_primary",
  "capabilities": ["screen.observe","touch","keyboard","media.control","app.launch","notifications","camera","microphone"]
}
```

## Device Bus requirements

`identity · pairing · encryption · heartbeat · capability discovery · command_id · ack · result ·
cancellation · reconnect · offline state`

Command example:
```text
command_id = 18731 · device = phone_main · action = media.next · status = completed
```
Commands **idempotent** (idempotency key + dedupe window). Offline → queue with TTL.

## Networking

| Concern | Requirement |
|---|---|
| LAN discovery | mDNS/UDP beacon + manual pin fallback |
| Remote relay | encrypted; relay sees ciphertext only |
| NAT traversal | STUN/relay; router port opening by default nahi |
| Transport | mTLS, per-device cert, pinning on first pair (TOFU + confirm) |
| Reconnect | exponential backoff + jitter, session resume, heartbeat timeout → `DEVICE_OFFLINE` |
| Replay protection | monotonic counter + timestamp window + command_id dedupe |
| Bandwidth | control > voice > telemetry > media; adaptive on metered links |

## Nodes

| Node | Role | Notes |
|---|---|---|
| Main PC | core + computer control | daemon |
| Second PC / Laptop | GENIE node | render/worker |
| Android | interface + device agent + remote terminal | `openclaw` Android app = useful reference (MIT) |
| Raspberry Pi | physical node: GPIO, sensors, relay, camera, mic, speaker | same Device Bus |
| Cameras | event source | **24/7 cloud stream nahi** — motion/change detect → frame → vision provider |
| Home devices | via Home Automation Plugin → controller | GENIE har bulb protocol implement nahi karega |

## Android node scope

App launch · media control · notification access · screen observation · touch/a11y actions · keyboard ·
mic · camera · file exchange · device status · secure comms.

## Device SDK

`manifest · capabilities · command handler · observation handler · connection`
Naya hardware developer bas itna implement kare.

## Sync

Mission state / memory / device state / artifact metadata sync via `ops/sync` — version vectors, per-type conflict
policy, offline queue, conflict UI. See master spec §3.8.
