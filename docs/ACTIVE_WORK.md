# GENIE — Active Work

## Source upgrade — 2026-09-20

New work is on `upgrade/genie-continuity-ui`, based on `1e0f3c2`.
See [the upgrade roadmap and verification ledger](UPGRADE_ROADMAP_2026-09-20.md).
Home refresh, context retention, anonymous model endpoint support, plan-task
transport/validation and restart persistence are PRESERVED. Added since:
canonical provider failure classification, high-level mission planner, honest
capability probe, one canonical UI API transport, page loading states,
competition engine, isolation model. These are source changes and are not in
the rc14 artifact below. Still pending: cleanup/docs, donor inventory, and a
new installer for this branch.

## PHASE 0 — terminal respawn incident (2026-09-20)

A reported flashing console window was investigated as P0. **GENIE is disproven as
the source** (every shipped GENIE binary is GUI-subsystem; zero GENIE persistence
exists; no respawn loop observed in 15 minutes of tight polling). A **separate real
GENIE defect was found and fixed**: in packaged builds all 5 plugin hosts died
instantly on every boot (174 failures / 36 boots). Plugin hosts are now launched by
script path and a persisted circuit breaker bounds startup retries.
The identity of the reported flashing window remains **BLOCKED** — see
[PHASE_0_TERMINAL_RESPAWN.md](PHASE_0_TERMINAL_RESPAWN.md).

## RC15 hardening (2026-09-20)

Feature set FROZEN. rc15a and rc15b are both **superseded** — rc15a's payload
predates the plugin-host fix, and rc15b's graceful exit left the backend running.
**rc15c** is the current candidate and is installed for owner acceptance:
`dist-electron-rc15c/GENIE Setup 0.1.0.exe`, 139,547,700 bytes, SHA-256
`9aa757a5b69631af539ca400fd42337360a3d601837c71b5e2e6bddcf6dd3c32`, source
`cc0caa0`. Default-install acceptance PASS; plugin hosts 24/24 in the payload;
**0 files and 0 processes left after uninstall** (was 35 and 6).
Frontend tests 87/87. Owner checklist: RC15C_ACCEPTANCE_CHECKLIST.md.

The "%TEMP% install files disappear" incident is **CLASSIFIED**: every NSIS install
runs the previously registered uninstaller, so install N deletes install N-1. TEMP
is not a factor — install order is. Not a product defect. Full A–X evidence in
[RC15_HARDENING_REPORT.md](RC15_HARDENING_REPORT.md).

## Current Status

**RELEASE CANDIDATE — rc14 built; owner desktop acceptance still pending.**

rc14 is the current tested product artifact. It has not been accepted on the
owner's Windows desktop, so GENIE is **not** declared released.

Work since rc14 lives on `upgrade/genie-continuity-ui` (HEAD `0bfa0c5`) and is
**source-only** — no new installer has been built for it yet. See
[the reconciliation report](RECONCILIATION_2026-09-20.md) for what is done,
partial and pending.

| Suite | Count |
|---|---|
| Backend deterministic | **1,517 passed**, 79 deselected |
| Frontend | **87** (57 page/unit + 14 API contract + 14 UI↔backend + 2 bootstrap) |

## Current Artifact

Two different things, and the difference matters:

**Tagged release (still rc14):**

| Field | Value |
|---|---|
| File | `dist-electron-rc14d/GENIE Setup 0.1.0.exe` |
| SHA-256 | `4f29328fdb5585d1fe65d78c159a41d74f7b92672a1777ea3b4c8418aa11f1c8` |
| Size | 136,976,883 bytes |
| Manifest | `artifacts/release_installer_manifest_rc14.json` |
| Git tag | `genie-v0.1.0-rc14` (commit `d92f0db`) |

**Newest build (candidate rc15a — untagged, contains everything from PHASE A–I):**

| Field | Value |
|---|---|
| File | `dist-electron-rc15a/GENIE Setup 0.1.0.exe` |
| SHA-256 | `af38d1f235009a8e60016d25c5a9cf2fe8b233d1ea4320bbf35f81aed19b0540` |
| Size | 139,543,838 bytes |
| Portable | `dist-electron-rc15a/GENIE-0.1.0-portable.exe` (139,039,427 bytes) |
| Manifest | `artifacts/release_installer_manifest_rc15a.json` |
| Source | branch `upgrade/genie-continuity-ui` @ `604f41e` (17 commits after rc14) |
| Git tag | **none** — rc14 was never moved; a new candidate identity was created instead |

rc14d is still the release of record, and it is also still *stale*: it was built
before PHASE A–I, so it does not contain the canonical API transport, the
competition engine, the isolation model or the per-page loading states. rc15a is
the first artifact that does.

Earlier artifacts are historical only. rc13 is preserved at
`dist-electron-rc13g/GENIE Setup 0.1.0.exe`; rc12 at `dist-electron-rc12g`.
Neither is re-tagged. Build-output cleanup is classified, not yet executed —
see [the cleanup plan](CLEANUP_PLAN_2026-09-20.md).

## What Is Complete

### Fixed in rc13

| Area | Fix |
|---|---|
| Single source of truth | `ui/web/helpers.js` is the only place that defines pure helpers and field normalizers. `humanValue()` is the GENERIC formatter (people / agents / actors / IDs); `humanErr()` stays NARROW for error-shaped objects. |
| `[object Object]` in reported_by | `humanList()` now uses `humanValue()`, so a `reported_by` entry like `{who:"owner"}` renders as `"owner"` instead of being filtered out. |
| Audit actor correctness | `normalizeAuditEntry()` resolves an actor whether it arrives as a string OR as an object, so the rc12 "correct actor" fix cannot regress. |
| Memory key | `pages.js` reads `/api/memory.hits` — Memory no longer goes silently blank when the backend returns hits. |
| Computer workspace | `pages.js` reads the REAL binding `status.computer.workspace.{root,bytes}`, with `"data/workspace"` only as an honest fallback when the backend omits it. |
| Load order | `ui/tests/frontend/script-order.test.js` asserts `helpers.js` loads BEFORE `pages.js` in every operational shell — a forgotten script tag now fails the test, not the browser. |
| API/UI contract | `ui/tests/contracts/api-ui.test.js` validates the JSON shape `pages.js` reads for 12 real endpoints. Fixtures are MINIMAL SANITIZED (`make_sanitized_fixtures.js`): no owner data, no local paths, no provider credentials. |
| Frontend freeze gate | `ui/tests/visual/verify_rc13_gate.py` recaptures the affected pages (Memory, Computer, Security) and runs a global smoke (13/13 routes 200, no banned strings, no raw epoch, no console exceptions). |

### Fixed in rc12 (preserved in rc13)

- Desktop icon: the packaged `GENIE.exe` carries the GENIE brand icon at all 8 sizes.
- Installer upgrade: asks a running GENIE to close itself via `GENIE.exe --genie-shutdown` and waits; never force-kills.
- Application exit: one canonical full exit reused by tray, installer requests and explicit exits; single-instance lock.
- Missions: real goal, progress, current step and a visible failure reason.
- Agents: distinguishes "no runtimes registered" from "no active agents".
- Media: reports the artifact store's real counts.
- Settings: "Enabled" no longer implies a credential or healthy endpoint.
- Rendering safety: `esc()` renders undefined, null, NaN and raw objects as an em dash.

### Green before rc13

- Core logic and deterministic test gates.
- Installer install and uninstall (rc11 evidence).
- Golden Gate: 18/18 criteria.
- Wrong-action baseline: 31 executed, 0 wrong.

## What Is Pending

1. **Owner desktop acceptance of rc13** — `python scripts/desktop_acceptance.py`.
   Steps: hash → install → launch normally (no GPU flags) → confirm window /
   daemon / Home → second launch single-instance → install while running →
   graceful shutdown (request + success, no forced cleanup, relaunch) →
   user-data preserved → Exit → no leftover processes → uninstall → removed /
   user-data policy.
2. **Production code signing** — owner-blocked, tracked separately from the
   functional gate.

## Known Limitations

- **Default app launch is blocked in the agent environment**, not necessarily
  on a normal desktop. The Chromium GPU process fails to initialise its sandbox
  here. If the owner desktop reproduces the failure, classify it as
  ENVIRONMENT-SPECIFIC GPU-SANDBOX LIMITATION and evaluate the narrowest
  mitigation first (`--disable-gpu-sandbox`), then `--in-process-gpu`.
  `--no-sandbox` remains diagnostic-only and is never the default.
- Optional external specialists are unavailable and are not core release
  failures: Strix (Docker daemon missing), MiroFish (upstream runtime missing).
- Packaged A/B staged activation is not supported; the boundary is documented
  under rollback acceptance.

## Test Evidence

| Suite | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|
| deterministic | 1387 | 0 | 0 | 78 |
| unit | 890 | 0 | 0 | 0 |
| external_optional | 58 | 0 | 3 | 0 |
| frontend | 45 | 0 | 0 | 0 |
| contracts | 14 | 0 | 0 | 0 |
| browser_regression | 24 | 0 | 1 | 0 |
| full_real_machine | 68 | 0 | 3 | 1394 |

Reported separately, because they answer different questions:

- **Golden Gate:** 18/18 criteria PASS (G2/G9/G10/G11 supervised).
- **Wrong-action baseline:** 31 executed, 0 wrong, rate 0.0000, 8 consecutive
  runs. Meets target for this defined sample. It is not a claim of a universal
  0% wrong-action rate; 4 of 9 representative categories are unexercised.

The authoritative matrix is `artifacts/release_matrix.md`, generated from
`artifacts/release_matrix.json`.

## Release Decision

Two levels, kept separate:

1. **Functional gate** — green, except for owner desktop acceptance of rc13.
2. **Distribution and signing** — owner-blocked, independent of the functional
   gate.

GENIE is therefore **not** "released": release-specific acceptance is
incomplete. Product capability is mature; release gates are not.

## Historical

Kept for provenance only. None of this describes current truth.

- **rc12** — SHA-256 `3c7a4fbb3431212da9c8cb8a4a0cc27047f29943061ddfa569e1dc4770bc34b7`,
  82,420,694 bytes. Fixed the desktop icon, the install-while-running upgrade,
  the canonical full-exit path, and the UI truth fixes that made rc12
  functionally release-ready (unsigned). Superseded by rc13 (API/UI contract
  normalization + Memory/Workspace fixes).
- **rc11** — SHA-256 `c40cae84a9445745aa96933cdee351633de307545d38e57a9c25dbd9a1d8c26b`,
  82,088,453 bytes. Produced the packaged-app launch evidence and the installer
  install/uninstall PASS records. Its packaged EXE carried Electron's default
  icon; that defect is fixed in rc12.
- **Packaged-app launch investigation** — the GPU process was denied its disk
  cache (access denied, `0x5`), exited 9 times, then Chromium aborted with
  `0x80000003` (STATUS_BREAKPOINT). Dev Electron fails identically. Only
  `--no-sandbox` runs; `--disable-gpu-sandbox` and `--in-process-gpu` also run
  and are narrower. No security-software event explains it.
- **Installer current-state investigation** — install and uninstall both pass;
  the failure was app launch, not installation.
- **Tag history** — `genie-v0.1.0-rc1` through `genie-v0.1.0-rc13`, all
  immutable and never re-tagged. Zero remotes; nothing has been pushed.
