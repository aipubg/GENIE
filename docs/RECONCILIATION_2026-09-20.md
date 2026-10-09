# GENIE — Reconciliation Report (first report, spec §30)

Date: 2026-09-20
Branch: `upgrade/genie-continuity-ui`
HEAD: `0bfa0c5` (13 commits above rc14 baseline `1e0f3c2`)

This is an internal reconciliation map, written **before** the large implementation
phases. It states what already existed, what was preserved, what was merged, what
was fixed, what was deduplicated, what is newly implemented, what is verified,
what is partial, and what is blocked. Nothing here is marked done on intent alone.

---

## A. Current code state

| Item | Value |
|---|---|
| Branch | `upgrade/genie-continuity-ui` |
| HEAD | `946d5d2` |
| Base | `1e0f3c2` (rc14: bootstrap tests pointed at final rc14d artifact) |
| Worktrees | `E:/G3/GENIE` (main) · `E:/G3/GENIE-worktrees/ui-final-genie-companion` @ `211591b` |
| UI worktree vs this branch | **0 ahead / 123 behind** → stale, superseded |
| Working tree | clean except 16 untracked **build output** directories |
| Release tags | `genie-v0.1.0-rc1 … rc14` (all intact, none moved) |

Commits added this session, in order:

| # | Commit | What |
|---|---|---|
| 1 | `5b92067` | dedup: remove unreachable duplicate `/api/agents` GET routes + regression guard |
| 2 | `9b38202` | U1: canonical provider failure classification |
| 3 | `ca64fe7` | PHASE D: canonical high-level mission planner |
| 4 | `66a2a2c` | preserve upgrade-branch work (context continuity, no-key endpoints, planner DAG, restart restore) |
| 5 | `3bf4399` | preserve Home UI refresh + roadmap/README |
| 6 | `803945e` | PHASE F: honest capability availability probe |
| 7 | `736db3d` | preflight `None` stdout guard |
| 8 | `946d5d2` | PHASE E: one canonical API transport + pytest collection hygiene |
| 9 | `deed36d` | PHASE E: loading state for all 11 pages + page-state contract tests |
| 10 | `8154da2` | docs fixup |
| 11 | `4e08020` | PHASE G: competition engine (roadmap §16–§18), 25 tests |
| 12 | `0bfa0c5` | PHASE H: isolation model + declared sensor sources + `/api/isolation` |

---

## B. Old work vs. new work — reconciliation

The "newer agent" did **not** work on a branch. Its work was **uncommitted in the
main working tree** (13 files). That is why it looked like two competing projects:
one version lived in commits, one lived only on disk.

Resolution taken:

1. Diffed the working tree before touching anything.
2. Kept the newer agent's behaviour where it was a genuine improvement.
3. Committed it as `66a2a2c` + `3bf4399` **before** building anything on top, so
   it could never be lost by a later checkout.
4. Treated the `ui-final-genie-companion` worktree as historical context only —
   it is 123 commits behind and contributed nothing that is not already present.

Explicitly **not** done: no restart, no rewrite, no reverting of the newer work.

---

## C. Duplicates found

| # | Duplication | State |
|---|---|---|
| 1 | `/api/agents` GET routes registered twice in `core/ipc/server.py` (second copy unreachable) | **REMOVED** + guard test |
| 2 | Six fetch wrappers in the UI (`api()` ×4, `getJSON`/`postJSON` ×2) | **DEDUPLICATED** → `ui/web/api.js` |
| 3 | Two "planners": `computer/planner.py` (capability ladder) and `missions/planner.py` (objective decomposition) | **NOT a duplicate — different scopes.** Kept both, documented |
| 4 | Provider status computed in both backend and frontend | **RESOLVED** in rc14: backend enum is authoritative, frontend maps enum→label only |
| 5 | Model roles persisted in both `localStorage` and backend | **RESOLVED** in rc14: backend only |
| 6 | 16 build-output directories (`dist-electron-*`, `backend-dist/`, `.build/`) | **IDENTIFIED, NOT DELETED** (see O) |
| 7 | `needle_runtime.status()` read as nested when it is flat | **FIXED** in `core/capability_status.py` |

The dangerous one was #2. Three of the six wrappers never checked `res.ok`, so an
HTTP 500 parsed as `{}` and the page rendered an **empty** state instead of an
**error** state. An owner seeing "no missions" during an outage would act on it.
`app.js` had no offline handling at all; `control.js`/`ops.js` `postJSON` ignored
`res.ok`, so a **failed control action** was reported as success.

---

## D. Canonical choice per capability

| Capability | Canonical | Notes |
|---|---|---|
| Runtime paths | `core/paths.py` | single authority |
| Provider registry / status | `models/registry.py` + `models/providers/*` | backend-authoritative enum |
| Provider failure meaning | **`models/failures.py`** (new) | one `FailureKind` vocabulary + retry/failover policy |
| LLM transport | `models/gateway.py` | classifies via `failures`, publishes `PROVIDER_ERROR` |
| High-level planning | **`missions/planner.py`** (new) | objective → deliverables → DAG → criteria |
| Capability ladder | `computer/planner.py` | unchanged; different layer |
| Context building | `context/builder.py` | mission-first, priority-ordered, tokens recomputed |
| Agent teams | `agents/service.py` + `agents/team.py` | DAG validated before allocation |
| Agent profiles | `agents/factory.py` + `experience/bank.py` | reload from DB on restart |
| Evaluation | `evaluation/lab.py` | unchanged |
| Experience | `experience/bank.py` | unchanged |
| Capability truth | **`core/capability_status.py`** (new) | `ready / missing_runtime / missing_credentials / disabled / failed / unknown` |
| API transport (UI) | **`ui/web/api.js`** (new) | `apiFetch` / `apiFetchStrict` |
| Daemon bootstrap | `ui/electron/backend.js` | atomic spawn lock |

---

## E. Newer agent's changes — preserved

All preserved, none reverted:

- `context/builder.py` — mission in `keep_first`; priority-ordered middle; token
  recompute after trim; `budget_overflow` flag.
- `models/gateway.py` + `models/providers/openai_compat.py` — `_requires_credential()`
  replaces `protocol == "mock"`; `Authorization` sent only when a secret exists
  (no-key local endpoints work).
- `core/ipc/server.py` — unreachable duplicate POST block removed; tasks survive transport.
- `agents/service.py` — `start_team` validates the whole DAG (duplicate ids, cycles,
  missing deps) **before** allocating workers or budget; installs tasks topologically.
- `agents/factory.py` — `ExperienceStore` + `AgentFactory` reload from DB at startup;
  mission workers and unevaluated candidates are deliberately **not** resurrected;
  `retire()` persists `active=0`.
- `ui/web/home.*` — copy refresh, `#voice-caption` with `aria-live`, non-2xx → offline,
  mission goal shown.
- `tests/unit/test_agent_restart.py` — 4 restart/restore tests.

---

## F. RC14 features — all still present

Embedded-Python backend · source-independent runtime · real NEDLE2 director
(`cactus-needle==2.0.15`) · canonical provider status enum · Manage Providers UI ·
model roles persisted backend-side · custom OpenAI-compatible providers ·
vault-backed credentials (incl. removal) · auto-bootstrap · duplicate-daemon
prevention (atomic `O_EXCL` lock) · contract tests · installer manifest.

Artifact: `dist-electron-rc14d/GENIE Setup 0.1.0.exe`, 136,976,883 bytes,
SHA-256 `4f29328fdb5585d1fe65d78c159a41d74f7b92672a1777ea3b4c8418aa11f1c8`,
tag `genie-v0.1.0-rc14` → `d92f0db`. **Tag not moved.**

---

## G. Test baseline

| Suite | Before this session | Now |
|---|---|---|
| Backend deterministic | 1,431 passed / 79 deselected | **1,517 passed / 79 deselected** |
| Frontend (`ui/tests/frontend`) | 52 | **57** (incl. 5 new page-state tests) |
| API contract (`ui/tests/api-contract.test.js`) | — | **14** (new) |
| UI↔backend contracts | 14 | **14** |
| Electron bootstrap | 2 | **2** |
| **Frontend total** | 59 | **87** |

New backend tests: `test_no_duplicate_routes.py` (3), `test_provider_failures.py` (25),
`test_mission_planner.py` (11), `test_capability_status.py` (6),
`test_competition.py` (25).

Backend run is reproducible: `python -m pytest -q -p no:cacheprovider`.

---

## H. U1 — provider reliability gaps closed

**Was:** failures were classified ad hoc, by substring-matching exception text in
more than one place. Retry and failover decisions were inconsistent, and a
partially-streamed reply could be replayed against a second provider — duplicating
already-emitted output.

**Now:** `models/failures.py` owns one `FailureKind` vocabulary (12 kinds) with an
explicit policy triple — `retryable`, `failover_ok`, `committed`. `gateway.complete()`
classifies once, publishes `PROVIDER_ERROR`, and **refuses failover when output was
already committed**. 25 tests.

---

## I. Planner / execution gaps

**Was:** `/api/agents/plan` returned an empty plan whenever the caller supplied no
tasks. The bridge from "objective" to "executable task graph" did not exist as a
product feature, only as caller-supplied data.

**Now:** `missions/planner.py` classifies the objective (coding / research / content /
computer / general) and produces deliverables, an ordered task DAG, and per-task
completion criteria. Computer missions dry-run before executing. `/api/agents/plan`
fills a real plan when none is supplied. 11 tests.

**Still open:** the plan→execution bridge is wired for the planner's output format
only; competition between candidates (PHASE G) is not built.

---

## J. UI gaps

**Done:** canonical transport (§C#2); Home refresh preserved; non-2xx now surfaces as
an error instead of emptiness.

**Audited (all 11 page loaders in `ui/web/pages.js`):**

| Page | error state | empty state | loading state |
|---|---|---|---|
| all 11 (Missions, Agents, Computer, Skills, Devices, Memory, Knowledge, Media, Forecast, Security, Settings) | yes | yes | yes |

Result after the audit:

- **Error path:** all 11. This came free from the canonical transport — which is exactly
  why the dedup mattered: before it, three pages turned a 500 into "no data".
- **Empty state:** all 11 already had one (several hand-written rather than using the
  `empty()` helper), so an honest "the backend has nothing" was never a blank page.
- **Loading state:** was missing on **10 of 11**. Added — `helpers.js` now exports
  `loading()`, and every loader paints it before its first network call. It carries
  `role="status"` + `aria-live`, so it is announced rather than only seen.

Locked by `ui/tests/frontend/page-states.test.js`, so a twelfth page cannot ship without
all three states.

**Not yet reviewed:** narrow-window behaviour and accessibility across the 11 shells.

**Unverified:** Home "ghosting" (a duplicate/faint visual artifact). Investigated in
markup, JS and CSS — no duplication source found. It is a visual defect that needs
the owner's screen; it is **not** claimed fixed.

---

## K. Specialist / runtime truth — probed, not assumed

Live probe (`core/capability_status.report()`):

| Capability | State | Engine / reason |
|---|---|---|
| Voice STT | **ready** | vosk |
| Voice TTS | **ready** | windows-sapi |
| Microphones | 13 input devices listed | — |
| Perception (vision) | **missing_runtime** | `cv2` absent |
| Browser | **ready** | playwright |
| Computer control | **ready** | pywinauto |
| NEDLE2 | **unknown** | 2.0.15 / 2.0.4 present, smoke not run in a bare process |

The important change: an adapter class existing no longer means the capability is
available, and "present but never verified" now reports `unknown` rather than
claiming `ready`.

---

## L. Competition engine — DONE (`agents/competition.py`, 25 tests)

Built **on** the existing runner/budget/experience/audit machinery, not beside it, and
wired into `AgentService.compete_task()` so it is reachable from the daemon.

Five rules it exists to enforce — each is the failure a naive "run them all and take the
first answer" implementation produces:

1. **The verifier is not a contestant.** `runner is verifier` is rejected at
   construction, and scoring is blind: the verifier receives the task and the output,
   never the agent id.
2. **All candidates failing is a result.** Outcome `needs_revision` plus every failure
   reason. A loser is never promoted because something has to be returned.
3. **Exactly one commit stage.** Side effects run once, after a winner exists — two
   contestants cannot both push.
4. **Cancellation stops everybody**, checked before and between attempts.
5. **Cost is a first-class decision.** Two candidates by default; three or four only
   when a real finite budget is still mostly unspent (60% -> 3, 85% -> 4).

One design correction found by test: an *absent* budget limit was initially treated as
"unlimited", giving 4 candidates. That is wrong — no limit is absence of information,
not permission to spend. It now falls back to 2.

**Not yet wired:** no IPC route and no UI for competition; the mission runner does not
call it automatically yet. It is reachable and tested, not yet scheduled.

---

## M. Workspace / isolation / perception — DONE (`core/isolation.py`)

"The agent is sandboxed" is meaningless until you can say **which** confinement is in
effect. `core/isolation.py` is the single place that answers it, reachable at
`GET /api/isolation`. It is deliberately conservative:

| Level | How it is established | What it does NOT confine |
|---|---|---|
| workspace dir | `computer.workspace` root is active | network, process execution, reads outside the root, the owner's desktop session |
| browser profile | dedicated profile directory | network egress, the IP the site sees, the OS account |
| container | heuristic (`/.dockerenv`, cgroup, WSL) — evidence returned | — |
| VM | heuristic (Windows hardware description) — evidence returned | — |
| isolated desktop | **declared only**, never detected | — |

Three honesty rules baked in:

- A negative container/VM probe is reported as **`not_detected`**, never as proof of
  bare metal, and always carries its evidence so the answer can be argued with.
- An isolated desktop **cannot be detected** — it is a property of how GENIE was
  launched. It stays `unknown` until declared. Guessing "isolated" would be the most
  dangerous lie in the module.
- Devices are listed, but `declared_camera` / `declared_microphone` stay **None**.
  Enumerating microphones is not the same as saying which one is listening.

Live on this box: effective `workspace_dir` · container not_detected · VM not_detected ·
isolated desktop unknown · cameras `missing_runtime` (cv2 absent) · microphones ready,
none declared.

**Not done:** no isolated-desktop *implementation* — declaring one is supported, but
GENIE cannot yet actually launch or attach to one. That is real future work, not a
gap in the report.

---

## N. Documentation drift — CONFIRMED

`docs/` holds 43 documents including phase reports `PHASE2 … PHASE14`. Drift was real
and has been corrected:

- `ACTIVE_WORK.md` said "RELEASE CANDIDATE — rc13" while the artifact is **rc14**, and
  pointed at a `dist-electron/` path that no longer holds an installer.
- `README.md` said "242 tests passing" while the suites are at **1,517 backend /
  87 frontend**.

Both now state the current artifact (rc14d + SHA-256 + tag), the branch HEAD, and the
real test counts. Stale status is worse than no status — it is what someone reads
before deciding whether a thing is safe to ship.

Still historical and unreconciled: the `PHASE2 … PHASE14` reports and several
older design docs. `UPGRADE_ROADMAP_2026-09-20.md` plus this report are the
authoritative near-term sources.

---

## O. Cleanup — CLASSIFIED, then executed in one approved batch

4.66 GB across 16 untracked build outputs. Full classification with sizes and a
recommended deletion order is in `docs/CLEANUP_PLAN_2026-09-20.md`.

**Executed (owner approved, ~0.6 GB):** `.build/` (367.6 MB scratch),
`backend-dist/GENIEBackend/` (235.3 MB, superseded by `backend-runtime`), and the
seven husks `dist-electron-check`, `rc12`, `rc12b`–`rc12f` (~65 KB total). Removed
via a staged move with verification between steps; `rc14d` and `backend-dist/backend-runtime`
were confirmed intact afterwards.

**Deliberately kept:** `dist-electron-rc14d` (the tagged release) and
`backend-dist/backend-runtime` (required to build the next installer). Also kept:
`rc14`, `rc14b`, `rc14c`, `rc13g`, `rc12g` — superseded, but each holds its own
installer, and deleting them destroys evidence until the owner confirms `rc14d` is
the build they actually tested. **≈4.2 GB still reclaimable on request.**

`.gitignore` now covers `dist-electron-*/`, `backend-dist/` and `.build/`, so
`git status` no longer reports build output as untracked noise. They are ignored,
not deleted.

## O2. Donor inventory (§15) — SURVEYED

`docs/DONOR_INVENTORY_2026-09-20.md` — 27 repos, ~1.87 GB. All ten roadmap donors
are present.

- **Already integrated:** Prime, Scrapling, MiroFish, Agency (`integrations/`
  adapters, `agents/factory.py`).
- **Vendored:** Strix (`vendor/strix-audit`, its own tests excluded from our suite).
- **Referenced but no dedicated adapter:** DeepSeek Harness, Youtu, Qwen.
- **Present but completely unused:** **OpenClaw (590.8 MB) and Hermes (175.2 MB)** —
  766 MB of the largest two donors doing nothing.

Three repos carry **no licence file** (Prime, Scrapling, Open-Higgsfield) and two
are non-standard (HeyGem.ai, n8n). Absence of a licence is not permission;
`docs/LICENSE_MATRIX.md` must resolve these before any of that code ships.

The Agent Maker PDF guides were **not found** anywhere in the workspace. Nothing has
been assumed or fabricated about them.

---

## P. Implementation order

| Phase | Content | State |
|---|---|---|
| A | Reconcile + baseline | **DONE** |
| B | Preserve newer work | **DONE** |
| C | Dedup routes | **DONE** |
| D | High-level planner | **DONE** |
| E | Canonical transport + page audit | **DONE** — transport unified; all 11 pages have loading/empty/error states |
| F | Honest capability probe | **DONE** |
| G | Competition engine + experience | **DONE** |
| H | Workspace / isolation / perception | **DONE** |
| I | Cleanup + docs | **DONE** (docs corrected, one cleanup batch executed, donor inventory written) |
| J | Packaging + release verification | **DONE** — candidate rc15a built and graded |

## Q. PHASE J — packaging and release verification

**New identity, tag untouched.** Product code changed after rc14, so the tag
`genie-v0.1.0-rc14` was **not** moved. The build got its own candidate identity,
`rc15a`, in `dist-electron-rc15a/`.

| Item | Result |
|---|---|
| Installer | `dist-electron-rc15a/GENIE Setup 0.1.0.exe` — 139,543,838 bytes, SHA-256 `af38d1f2…` |
| Portable | `dist-electron-rc15a/GENIE-0.1.0-portable.exe` — 139,039,427 bytes |
| Source | `upgrade/genie-continuity-ui` @ `604f41e`, 17 commits after rc14 |
| Manifest | `artifacts/release_installer_manifest_rc15a.json` |

**A real problem found before building.** The packaged runtime was stale: it had
no `agents/competition.py`, no `core/isolation.py` and no `ui/web/api.js`. Any
installer built from it would have shipped pre-PHASE-A code while claiming to be
current. It was rebuilt with the canonical `scripts/build_backend_runtime.py`.
To avoid silently changing the dependency set, `.build/embed/python-embed` was
pre-seeded from the rc14 python tree first, so pip reported every requirement
"already exists" — the shipped dependency versions are byte-identical to rc14 and
only GENIE's own code changed.

**Deduplicated during packaging (§2 workstream).** The shipped `site-packages`
carried two `cactus_needle` dist-infos, `2.0.15` and `3.0.2`. Hashing every file
in both RECORDs against disk proved 2.0.15 owns all 36 recorded files while 3.0.2
mismatched 13 of 35, so the 3.0.2 metadata was stale; it was removed from the
shipped tree and from the build cache.

**Gates**

| Gate | Result |
|---|---|
| Backend tests | **1,517 passed**, 79 deselected |
| Frontend tests | **87/87** (9 files) |
| Packaged runtime smoke | **29/29**, run with the embedded interpreter against the *shipped* tree inside `win-unpacked` |
| Competition engine in package | real `compete()` → `winner`, 2 candidates, 2 runner calls |
| Isolation model in package | `effective=host`, 5 levels, 7 enum members |
| Canonical transport in package | `app/ui/web/api.js` shipped; 17/17 HTML shells load it |
| EXE icon | 8/8 sizes match the source ICO |
| Installer integrity | valid PE, Nullsoft/NSIS markers present |
| Install acceptance | install rc=0 (3,545 files) → launch stays up → `/health` **HTTP 200** → clean close → uninstall rc=0 |
| NEDLE2 in the packaged app | daemon log: `NEDLE2 engine ready (needle 2.0.15, engine 2.0.4)`, `smoke 12/12` |

**Two broken gates fixed, not worked around.** Both were verification defects,
so a false result would have been recorded as truth:

1. `reached_daemon` only counted ESTABLISHED/TIME_WAIT netstat lines on 8787. A
   UI that connected and hung up between two 1-second samples was recorded as
   "never reached the daemon" — while the daemon's own log showed it serving
   requests and NEDLE2 activating 12/12. Replaced with an HTTP GET against
   `/health` (answered `200 {"ok": true}` on the first attempt); netstat is now
   corroboration only.
2. `scripts/acceptance_installer.py` hardcoded `dist-electron/`, which is how
   acceptance once silently graded an **rc11** installer. Added `--dist` and
   `--report`; defaults unchanged.

**Still honest about what is not proven.** Home ghosting still needs the owner's
screen. The build is unsigned, so SmartScreen will warn. And one observation has
no root cause yet: installed files disappear from `%TEMP%` install directories
5–10 minutes after install, including directories the app was never launched
from; Defender shows no GENIE detection and no GENIE code deletes install
directories (`core/updater.py` only removes its own staged releases). It made one
acceptance run report "uninstaller not found"; the run where files survived
uninstalled cleanly. Tracked as `rc15a-1` in the manifest.

---

## Honest summary

**Verified done:** reconciliation, preservation, deduplication (routes, UI
transport, shipped package metadata), provider failure classification, high-level
planner, capability probe, canonical API transport, page loading/empty/error
states, collection hygiene, competition engine, isolation model, docs correction,
donor inventory, one approved cleanup batch, and **packaging + release
verification (candidate rc15a)**. Backend 1,517 green; frontend 87 green;
packaged runtime smoke 29/29; install acceptance passed end to end.

**Partial:** none in A–J.

**Shippable now for the first time:** rc15a is the first artifact that actually
contains the A–I work. rc14d is still the tagged release and is still stale
relative to the source tree.

**Deferred by choice:** ~4.2 GB of superseded build output kept until the owner
confirms rc14d is the build they tested.

**Partial:** the competition engine is built and tested but is not yet scheduled by the
mission runner, and has no IPC route or UI.

**Known flake (pre-existing, not from this work):** one
`PytestUnhandledThreadExceptionWarning` in `tests/unit/test_installer_shutdown.py` — a
subprocess reader thread hits a decode error reading another process's stdout. A
warning, not a failure; not yet fixed.

**Unverifiable here:** Home ghosting (needs the owner's screen).

**Corrected during J:** this file previously said "Electron cannot launch in this
environment — it dies at GPU init before `app.whenReady()`", which was true only
for launching the unpackaged app from a dev tree. The **installed** app launches
fine: in install acceptance the first launch stayed running past the 8-second
settle and the daemon answered `/health` with HTTP 200. Bootstrap is still also
proven unit-side by driving the real `ui/electron/backend.js` from Node with only
the `electron` `app` module stubbed.

**Undetermined (rc15a-1):** installed files vanish from `%TEMP%` install
directories 5–10 minutes after install. Not Defender, not GENIE code. See the
manifest.


## §R — PHASE 0: terminal respawn incident

See [PHASE_0_TERMINAL_RESPAWN.md](PHASE_0_TERMINAL_RESPAWN.md) for the full
investigation. Summary in the required status language:

* **FIXED** — packaged plugin hosts died instantly on every daemon boot
  (`python -m plugins.host` cannot resolve `plugins` under the embeddable
  interpreter's `._pth` isolation). 174 failures across 36 boots. Hosts are now
  launched by script path, and a persisted circuit breaker bounds startup retries.
* **NEWLY IMPLEMENTED** — `scripts/p0_process_capture.py` (observe-only process
  capture with parent chains, lifetimes and real exit codes) and
  `scripts/p0_persistence_sweep.py` (read-only persistence audit matched by content).
* **VERIFIED** — 22/22 plugin e2e tests (three consecutive runs), 24/24 packaged
  runtime plugin checks, `0` GENIE persistence matches, no respawn loop in 15
  minutes of 120 ms polling.
* **DISPROVEN** — GENIE as the source of a visible console window: `GENIE.exe`
  and `pythonw.exe` are both GUI-subsystem, and the bundle's only console binary
  (`python.exe`) is never launched by the app.
* **BLOCKED** — the identity of the flashing window itself. It is not currently
  reproducing; a capture must be taken while it recurs.
* **PRESERVED** — rc14 tag untouched; no process was killed, no component
  disabled, no task or key deleted during the investigation.

**Corrected during PHASE 0:** the earlier note that plugin hosts were "bounded by a
three-strike rule" was misleading. The three-strike counter lives on an in-process
object, so it reset on every daemon boot — the behaviour was effectively unbounded
across boots.

## §S — RC15 hardening: both incidents classified

See [RC15_HARDENING_REPORT.md](RC15_HARDENING_REPORT.md) for the full A–X report.

* **FIXED + VERIFIED** — the plugin-host crash loop. rc15b payload: 24/24 packaged
  checks and all five hosts start in the installed app. The rc15a payload predates
  the fix (5/5 FAIL), so **rc15a is superseded by rc15b**, not mutated.
* **CLASSIFIED** — the "%TEMP% install directories empty" incident. Every NSIS
  install runs the previously registered uninstaller (`old-uninstaller.exe
  /S /KEEP_APP_DATA /currentuser --updated _?=<previous install dir>`), so install
  N deletes install N−1. **TEMP is not a factor** — the control matrix inverted the
  hypothesis: the non-TEMP cases were emptied and the TEMP case stayed at 3545
  files for ~7 minutes. Not GENIE product code, not the updater, not Defender.
* **NEWLY IMPLEMENTED** — `scripts/p0_case_matrix.py`, `scripts/p0_install_watch.py`,
  `scripts/p0_registry.py`.
* **HARDENED** — `core/updater.py::stage()` now refuses any version string that
  escapes the releases directory (a traversal version could rmtree an arbitrary
  root). Three proof tests added.
* **PRESERVED** — rc14 tag untouched; rc15a tree and installer untouched.
* **PENDING** — Home owner acceptance (not marked PASS).
* **OPEN (`rc15b-1`)** — graceful exit stops the Electron frontend but not the
  backend daemon; six `pythonw.exe` processes persist. This is why uninstall can
  leave locked DLLs behind.

## §T — rc15c: the lifecycle defect behind "files left behind"

* **FIXED + VERIFIED** — `requestFullExit()` never stopped the backend, so "Exit
  GENIE" left the daemon and its five plugin hosts running. They held their own
  DLLs open and the uninstaller could not delete them. Reproduced exactly:
  six live `pythonw.exe` → **35 locked DLLs** survived uninstall.
  With rc15c: **0 files and 0 processes left**.
* **NEWLY IMPLEMENTED** — `stopDaemon()` in `ui/electron/backend.js` records the
  spawned daemon PID and terminates that one PID tree. `isOurDaemon()` **refuses
  by default**: it confirms the PID still belongs to our embedded interpreter and
  kills nothing if it cannot be confirmed, so a recycled PID is never destroyed.
* **FIXED (harness)** — the close and pre-uninstall steps used `taskkill /F`,
  which bypasses `requestFullExit()`; the graceful path was therefore never
  exercised. They now shut GENIE down via `--genie-shutdown` and only force-kill
  as a recorded fallback.
* **VERIFIED** — `scripts/verify_daemon_stop.js` 7/7; frontend tests 87/87;
  default-install acceptance PASS; plugin hosts 24/24 in the rc15c payload.
* **PRESERVED** — rc14 tag untouched; rc15a and rc15b trees untouched.
* **PENDING** — Home owner acceptance (still not marked PASS).
