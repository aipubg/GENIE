# GENIE — PHASE 8: PERCEPTION

**Status: 🟩 COMPLETE**

> **Exit gate:** *golden #12 — person enters workshop → structured event, no 24/7 cloud streaming;
> bedroom privacy default OFF.*
> Both verified: **52 tests**, all green, including golden #12 driven through the real pipeline.

---

## 1. The pipeline, and the rule that shapes it

```
camera → cheap local change/motion detection → interesting event
       → ONE frame → vision provider (only if configured) → structured event
```

The spec is blunt: *a camera must not stream to a cloud model*. That is not a policy bolted on
afterwards — it is why `perception/motion.py` exists at all. Frames are compared **locally, in
memory**; a frame is only ever offered onward when something actually changed.

## 2. Modules

| File | Responsibility |
|---|---|
| `perception/contracts.py` | zone, sensing policy, perception event, signal, `EnvironmentState` |
| `perception/motion.py` | frame-difference motion detection with hysteresis (dependency-free) |
| `perception/camera.py` | camera contract, bounded sessions, mandatory indicator, honest unavailability |
| `perception/presence.py` | presence engine: decay + noisy-OR fusion of independent signals |
| `perception/fusion.py` | screen / calendar / device / camera sensors → one `EnvironmentState` |
| `perception/service.py` | facade: zones, policies, camera lifecycle, events, retention, purge |
| `core/db.py` | migration `008_perception` (`perception_zones`, `perception_policies`, `perception_events`) |
| `core/orchestrator.py` | the fused environment is added to the context hint (§6.11) |

## 3. Privacy — what makes this liveable

| Control | Behaviour |
|---|---|
| **Private zones default OFF** | `bedroom` and `bathroom` ship with `camera=false`, `microphone=wake_only`, `motion=false`, a 60 s retention window, and `owner_enabled=false` |
| **Policy decides, not the caller** | `activate_camera("bedroom")` is refused with `policy_denied` until the owner explicitly enables it. The owner *can* enable it — the rule is "never implicit", not "never allowed" |
| **Camera fails closed** | If the mandatory indicator cannot be shown, the camera **does not activate**. A silent camera is worse than no camera |
| **No streaming** | Bounded session (≤120 frames, ≤300 s), every grab counted, and **one** vision classification per motion *event* with a 10 s floor between events |
| **Retention + purge** | Each zone has a retention window; `purge(zone)` hard-deletes derived data and clears presence |
| **Nothing is assumed** | The fused state names every unavailable sensor instead of guessing |

Why `bathroom` is private too: the spec names the bedroom, but the principle is *private space*.
Applying it only to the literal word would be a loophole, not a policy.

## 4. Honesty about what is missing

| Thing | State on this machine |
|---|---|
| Camera hardware | **absent** → `NullCameraProvider`; `genie.py perception` prints `camera: unavailable (no camera device is available on this host)` |
| Vision model | **not configured** → the event stays `motion_detected` with `vision_skipped`, and is **never** upgraded to a fabricated `person_entered` |
| Test frame source | `FrameSourceCameraProvider`, explicitly labelled, **never auto-selected** |

The tests use the frame source and a stub gateway because there is no camera and no vision
provider here. Both are doubles for *hardware and a remote model*, never for the code under test:
the detector does real arithmetic on real pixel arrays, the presence engine does real decay
arithmetic, and the gateway call path is the real one.

## 5. Defects found while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-071 | **The vision provider was called on every frame while motion continued** — a person walking past produced one model call per frame. That is precisely the continuous streaming the spec forbids, and it would have cost a call per frame | classify on the **rising edge only** (one call per motion event) plus a minimum interval between events; `MotionResult` now reports `started`/`stopped` |

## 6. Verification highlights

* **golden #12** — a person walking into the workshop produces
  `{type: person_entered, zone_id: workshop, confidence, ts}` with `streaming: false`
* **20 static frames → zero vision calls**; motion → at most one or two, never one per frame
* a static scene produces **no event at all**
* a bedroom camera cannot be activated until the owner enables it; the policy **persists**
* a camera whose indicator fails **never opens** (no session is created)
* a session is bounded and closes itself when exhausted
* no vision provider → `motion_detected` with `vision_skipped`; a failing provider → the event
  survives with `vision_error`
* presence decays (an hour-old observation is not presence), combines via noisy-OR, and never
  exceeds 1.0
* fusion degrades gracefully and **names** the missing sensor; a sensor that raises does not break
  fusion
* a broken perception service never breaks a turn (the context hint falls back)

## 7. Surfaces

| Surface | Routes / commands |
|---|---|
| HTTP | `GET /api/perception`, `/api/perception/zones`, `/api/perception/events`, `/api/perception/environment` · `POST /api/perception/policy`, `/api/perception/camera/activate`, `/api/perception/camera/close`, `/api/perception/observe`, `/api/perception/purge`, `/api/perception/register` |
| CLI | `perception`, `perception-policy`, `perception-events` (incl. `--purge`) |
| Events | `PERCEPTION_EVENT` on the event bus |

## 8. Deliberately deferred (interfaces preserved)

| Deferred | Why | Hook that exists |
|---|---|---|
| A real camera on this host | no device attached | `OpenCvCameraProvider` is implemented; `select_provider()` picks it when present |
| A configured vision provider | no vision-capable model/key | `GatewayVisionProvider` uses the existing model gateway — no second provider stack |
| Phone camera / mic as perception sources | Android app is owner-deferred | `docs/ANDROID_DEFERRED.md` → `perception.camera.stream`, `perception.audio.capture`; the device contract already carries them |
| Audio perception (who/where from voice) | Phase 3 voice provides the transcript path | `PresenceEngine.observe(zone, "audio", …)` is wired |
| Calendar integration | no calendar provider connected | `CalendarSensor` reports time only and says so |
| Screen vision on ambiguity | expensive and unnecessary for now | `ScreenSensor` uses cheap signals; a screenshot path exists via `screen.capture` |
| Mood / reaction understanding | §6.12 forbids claiming emotional truth | out of scope by design |

## 9. Verification

```
tests/e2e/test_phase8_perception.py   52   privacy, fail-closed camera, motion arithmetic,
                                           golden #12, no-streaming bounds, presence, fusion,
                                           retention/purge, context integration
tests/contract/test_ipc_api.py        +6   /api/perception*
genie.py perception                        reports zones, policies, camera and streaming state
```

**Phase 8 exit gate: PASS** — a person entering the workshop produces a structured event, no frame
is ever streamed, and private zones ship with the camera off and cannot be switched on implicitly.
