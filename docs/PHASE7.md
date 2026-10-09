# GENIE — PHASE 7: PI / IoT / HOME

**Status: 🟩 COMPLETE**

> **Exit gate:** *relay/sensor command idempotent + audited; home-automation bridge works.*
> Both verified: **44 tests**, all green, including relay/sensor commands driven through the real
> device mesh to a real Pi-style node over real TCP, and a home bridge driven through the real
> plugin host against a real HTTP controller.

---

## 1. The shape of this phase

Phase 6 gave GENIE a device mesh. Phase 7 makes it useful for the physical world, using the two
mechanisms the architecture already decided:

```
physical device  ──▶  Raspberry Pi node        ──▶  peripheral capabilities (GPIO/relay/sensor)
home appliance   ──▶  home-automation controller ─▶  one plugin (GENIE speaks one interface)
```

GENIE does **not** implement every bulb protocol (docs/DEVICES.md). The controller owns the
appliances; the plugin owns one stable interface to the controller. And a Pi is not a special
architecture — it is a device node that declares peripheral capabilities.

## 2. Peripherals (`devices/peripherals.py`)

| Backend | What it is | Verified on this host |
|---|---|---|
| `SysfsGpioProvider` | real Linux/Raspberry Pi GPIO via `/sys/class/gpio` | ❌ (not Linux) |
| `SerialRelayProvider` | real USB relay boards over a serial line | ❌ (no board attached) |
| `NullProvider` | honest "this host has no peripheral backend" | ✅ (this is what the host reports) |
| `MemoryBoardProvider` | a **test** backend, explicitly labelled, never auto-selected | ✅ (used by tests) |

`select_provider()` never picks the test backend. A deployment must not silently believe it
controlled hardware.

### Capabilities

`peripheral.list` · `gpio.read` · `gpio.write` · `relay.set` · `relay.pulse` · `relay.state` ·
`sensor.read` · `sensor.list`

A node declares **exactly** the capabilities its provider can honestly serve — a node with no
backend declares nothing rather than advertising what it cannot do.

### The safety semantics that matter

| Rule | Why |
|---|---|
| **`relay.set` is idempotent** | A retried command must not flip a physical switch. Writing the same state twice is a no-op, and the provider reports `changed: false`. |
| **No `toggle` primitive** | A toggle cannot be retried safely, and the mesh retries and queues. The same reasoning removed `toggle` from the home plugin. |
| **`relay.pulse` is explicit and bounded** | A pulse is inherently not idempotent, so it must be asked for by name and is capped at 10 s. An unattended relay held on is a hazard, not a feature. |
| **Writes are verified only when the backend can read back** | sysfs can read an output pin, so GPIO writes are `verified: true`. A USB relay board cannot, so it reports `verified: false` instead of claiming success. |
| **Unknown peripheral → refusal** | `unknown_peripheral`, never a silent success. |

### Proof (through the mesh, not just the provider)

* a relay command reaches the Pi node and the physical state really changes
* the same idempotency key produces **one** command row — the device is not touched twice
* a repeated `relay.set` **without** a key is still safe, because the primitive cannot toggle
* a sensor read reaches the node, is verified, and is audited (`device.command:sensor.read`)
* pairing a Pi grants nothing: without `device:rpi_main:relay` the command is denied and the
  relay **does not move**
* the Pi node offers no file capability, and asking for one is refused

## 3. Home-automation bridge (`plugins/installed/home/`)

A first-party plugin, so it inherits the Phase 4 guarantees for free: its own process, crash
isolation, two-gate permissions, timeouts and audit.

| Capability | Idempotent | Verification |
|---|---|---|
| `entities` | read | the list comes from the controller |
| `state` | read | the state comes from the controller |
| `turn_on` / `turn_off` | **yes** — already-in-state is a no-op | the entity is read back and confirmed |
| `set_brightness` | **yes** | `brightness_pct` is read back and compared |
| `activate_scene` | yes | the scene entity is read back |
| `reachable` | read | a real request to the controller |

**No `toggle`.** Deliberate, and documented in the manifest.

### Design decisions

* **A 200 response is not proof.** Every state change is verified by reading the entity back; a
  controller that accepts the call but does not change the entity is reported as
  `verification_failed`, not as success. There is a test for exactly that.
* **Already-in-state does not touch the device.** `turn_on` on a lit light issues no service
  call at all — that is what makes a mesh retry safe.
* **Credentials never live in the repo or the daemon.** The controller URL and token come from
  `workspace/config.json` (owner-created) or `GENIE_HOME_URL` / `GENIE_HOME_TOKEN`; the
  environment wins so a token need never be written to disk. The plugin process has no access to
  GENIE's vault or databases by design.
* **LAN traffic never goes through a proxy.** The bridge builds an opener with an empty
  `ProxyHandler`, because honouring `http_proxy` sent local controller traffic to a corporate
  proxy that answered 502 (A-069).

## 4. Defects found while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-069 | The home bridge used `urlopen`, which honours `http_proxy`. A controller on the LAN was routed through an HTTP proxy that answered 502 — a silent, confusing failure that would also leak the request | the bridge uses an opener with proxying disabled |
| A-070 | The plugin adapter must handle the **fully qualified** capability name the host passes (`plugin.home.turn_on`), not the short name | the adapter strips the prefix, matching the media/vscode convention |

## 5. Deliberately deferred (interfaces preserved)

| Deferred | Why | Hook that exists |
|---|---|---|
| Real Pi hardware verification | no Pi, no GPIO, no relay board on this machine | `SysfsGpioProvider` / `SerialRelayProvider` are implemented; the tests state plainly which backend ran |
| Zigbee/Z-Wave/Matter protocols | the controller owns them (docs/DEVICES.md) | the home plugin speaks one HTTP interface |
| Camera / motion detection | this is **Phase 8** (perception), and the rule there is no 24/7 cloud streaming | a camera is a device node with a capability; `docs/ANDROID_DEFERRED.md` tracks the phone camera |
| Home-automation device *discovery* | the controller already knows its devices | `entities` lists them |
| mDNS/UDP LAN discovery for Pi nodes | pairing by code works and is more secure | `DeviceServer.handshake` |

## 6. Verification

```
tests/e2e/test_phase7_peripherals_home.py   44   peripherals (unit + through the mesh) and the home bridge (real HTTP)
tests/contract/test_ipc_api.py              +2   /api/peripherals
genie.py peripherals                             reports the local backend honestly
genie.py peripherals-run <capability>            runs a peripheral capability locally
```

**Phase 7 exit gate: PASS** — relay and sensor commands are idempotent and audited end to end
through the mesh, and the home-automation bridge works against a real controller over real HTTP,
with read-back verification and default-deny permissions.
