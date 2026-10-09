# GENIE — PHASE 14: HARDENING & DAILY-USE RELEASE

**Status: 🟨 IN PROGRESS** — implementation complete; RC13 built.

- Remaining functional gate: **RC13 owner-desktop acceptance**.
- Distribution gate: **production code signing — owner-blocked**. This is a
  separate axis: signing is not required to determine whether the unsigned rc13
  is functionally release-ready.

Everything here exists so a bad day is survivable and reversible.

---

## 14.1 Backup & restore ✅

`core/backup.py` — `BackupService`.

Two rules drive it:

1. **Never copy a live database file.** GENIE runs SQLite in WAL mode; `shutil.copy` on an open WAL
   database can capture a torn state. Every snapshot goes through SQLite's **online backup API**,
   producing a transactionally consistent copy (tested with a connection deliberately left open
   mid-write).
2. **Verify before restoring.** Each backup records a sha256 per file; `restore()` re-checks them
   and **refuses** to apply a tampered archive. Restoring corrupt data over good data is worse than
   not restoring at all — and it is the failure mode that actually hurts.

A backup holds the database, the user config, and (by default, opt-out) the encrypted vault, which
is labelled `contains_secrets` in the manifest. `list()` and `verify()` round it out.

## 14.2 Safe mode, offline mode & crash recovery ✅

`core/hardening.py`.

* **Safe mode** — boots with dangerous capabilities off so the owner can still talk to GENIE
  (memory, missions, chat, local inference) when the automation layer misbehaves. Denied:
  `input.`, `process.kill`, `application.open/close`, `system.volume`, mutating `files.*`,
  `clipboard.set`, `shell.`, `browser.`, `plugin.`, `devices.`
* **Offline mode** — probes reachability instead of inferring it from a failed request, then routes
  to local providers only. If there is no network *and* no local provider it reports `degraded`
  with an honest note rather than pretending to work.
* **Crash recovery** — `CrashRecovery` writes a start flag; if it survives to the next boot the
  shutdown was unclean. `recover()` purges stale locks, lists interrupted missions, optionally
  resumes them, and **reports everything it found**. It never hides work, and it never auto-resumes
  unless explicitly asked.

> **Bug worth remembering:** safe mode was initially matching PTE *scopes* (`browser:navigate`)
> instead of capability *ids* (`browser.navigate`). Capability ids use dots; scopes use colons.
> Matching the wrong form silently matched nothing — safe mode would have been a **no-op that
> merely looked defensive**. Caught by a test that asserted real capability ids.

## 14.3 Updater & rollback ✅

`core/updater.py` — `Updater`.

A release is a **directory**, never an in-place overwrite. That single decision is what makes
rollback possible: the old version is still on disk, untouched, so reverting is just pointing
"active" back at it.

```
releases/
  1.2.3/     staged files + manifest.json (sha256 per file)
  1.3.0/
active.json  {"active_version": "1.3.0", "previous_version": "1.2.3"}
```

* `stage()` copies **and hashes** — nothing is trusted without a manifest.
* `activate()` records the previous version *before* switching, so rollback always has a target.
* `rollback()` restores the previous version, and **refuses** when there is nothing to go back to,
  when the previous release is missing from disk, or when it is corrupt.
* A corrupt release can never become active.

---

## Outstanding (honest status)

### Wired into the running system

| item | status | where |
|---|---|---|
| Safe mode gate | ✅ wired | `ComputerService.execute()` consults `get_safe_mode()` before the PTE check; a blocked capability returns `safe.explain()` |
| `--safe` CLI flag | ✅ wired | `genie.py --safe <cmd>` enables safe mode for the process |
| Crash recovery on boot | ✅ wired | `Daemon.start()` → `CrashRecovery.recover(locks, missions)` then `mark_start()`; `Daemon.stop()` → `mark_clean()` |
| Backup / restore | ✅ wired | `core/backup.py` — `BackupService` is implemented, and the CLI surface `genie.py backup` / `backup-list` / `backup-verify` / `backup-restore` is wired |
| Offline mode wired into routing | ✅ wired | `Gateway.candidates()` drops remote providers when offline (local + mock remain). The probe is **cached** (30s) so requests don't each pay a TCP timeout |

### Still outstanding

| item | status | note |
|---|---|---|
| Installer (`GENIE-Setup.exe`) | ✅ **built** (unsigned) | `dist-electron/GENIE Setup 0.1.0.exe` — rc13, 82,420,646 bytes, SHA-256 `6d5f9000…`, valid PE with NSIS markers; `win-unpacked/GENIE.exe` has the canonical icon embedded (verified at all 8 sizes). Built with **electron-builder**, which bundles its own NSIS, so Inno Setup 6 turned out **not** to be required — the earlier assumption was wrong. Signing was skipped (`signAndEditExecutable:false`) because extracting `winCodeSign` fails with "Cannot create symbolic link: a required privilege is not held" on this host, so SmartScreen will warn until a certificate is attached. Earlier (historical): rc12 82,420,694 bytes, rc11 82,088,453 bytes |
| Real-machine acceptance — **rollback** | ✅ verified | `scripts/verify_rollback_real.py` drives the REAL `genie.py update-*` CLI against the real data dir: stage → activate A → activate B → rollback → active is A again, content intact. **PASS**. Scope: real CLI + real data dir, not a packaged-install rollback (that needs the installer) |
| Real-machine acceptance — **golden tasks** | ✅ 18/18 PASS | `scripts/golden_gate.py` + `docs/GOLDEN_GATE.md`. G2 Browser, G9 Teaching, G10 Skills and G11 Voice have now been **run supervised and their criteria PASSED** (HISTORICAL: 14/18 PASS, 4 UNVERIFIED). They carry `pytestmark = real_machine`: they open Chrome, drive mouse/keyboard, or use the microphone, so they must run supervised. Full real-machine suite: 68 PASS / 0 FAIL / 3 SKIP — Blender ×2 ENVIRONMENT_UNAVAILABLE (Blender not installed), Chrome tab-startup ×1 non-gate environment/browser-startup skip |

### Golden gate detail

`python scripts/golden_gate.py --report` maps each Appendix E task to the tests
that assert its pass criteria and runs them. UNVERIFIED means *not run*, not
*failed* — pytest exits 5 when every test is deselected, and reporting that as a
failure would be wrong.

| Status | Tasks |
|---|---|
| PASS (18) | All eighteen golden criteria, G1–G18. The four previously-unverified goldens (G2 Browser, G9 Teaching, G10 Skills, G11 Voice) have since been run supervised with the owner present and PASS. |
| UNVERIFIED (0) | — |
| FAIL (0) | — |
| `Updater` CLI | ✅ wired | `genie.py update-status` / `update-stage` / `update-activate` / `update-rollback` |
| **Wrong-action rate** | ✅ instrumented | `core/action_metrics.py` + migration `013_metrics`, wired into `ComputerService.execute()`, exposed at `GET /api/metrics/actions`. Target 2%. Refusals are deliberately **not** counted as wrong actions. Measured baseline: 31 executed, 31 verified, 0 wrong, rate 0.0000, 8 consecutive runs — MEETS TARGET FOR THIS MEASURED SAMPLE (not a claim of a universal 0% rate). |
| Release **fetching** | ✅ done | `core/release_fetch.py` + `genie.py update-fetch <url>` — **channel-agnostic**: point it at any URL serving a manifest. Avoids forcing a product decision while making updates possible. Verifies per-file sha256; accepts an optional signature and **refuses a signed manifest when no verifier is supplied** |
| Installer (`GENIE-Setup.exe`) | ✅ built (unsigned) | HISTORICAL row (superseded): this previously said 'spec only / needs Inno Setup 6'. The installer is now built with electron-builder (bundled NSIS). See the current row above and `docs/RELEASE_ACCEPTANCE.md` |

### A deliberate security caveat on release fetching

Verifying each file's sha256 proves the download is **intact** (corruption/truncation), but it does
**not** by itself prove the manifest is **authentic**, since both travel over the same channel.
So:

* a **signed** manifest with **no verifier supplied is refused** — never trusted implicitly;
* a **bad signature** is refused;
* an **unsigned** manifest is allowed (the channel may well be trusted) but the result carries
  `signed: false` and an explicit warning that authenticity was not proven.

Silently accepting unsigned updates while implying they were verified would be the dangerous
default, so the distinction is surfaced rather than hidden.

**Honest status:** 14.1–14.3 are all implemented, tested **and wired into the running system** —
safe mode gates capabilities, crash recovery runs at boot, offline mode routes locally, backup and
updates are reachable from the CLI. All 18 golden criteria PASS.

Rollback, stated precisely:

- **Core updater rollback: VERIFIED** — `scripts/verify_rollback_real.py` drives the real
  `genie.py update-*` CLI against the real data directory: stage → activate A → activate B →
  rollback → A active again, content intact.
- **Packaged-installer rollback: NOT A CLAIM / separate scope** — it needs the desktop installer
  and is not covered by the CLI verification above.

Remaining gates, kept separate:

- **Remaining functional gate: RC13 owner-desktop acceptance.** Signing is not
  required to determine whether the unsigned rc13 is functionally release-ready.
- **Distribution gate: production code signing — owner-blocked.** A separate
  axis; it does not block the functional verdict.

---

## Verification

```
tests/e2e/test_phase14_backup.py       12   consistent snapshot of an OPEN db, hashes recorded,
                                            tamper/missing-file detection, restore brings data back,
                                            restore REFUSES a tampered backup, vault opt-out
tests/e2e/test_phase14_hardening.py    16   safe mode blocks real capability ids and keeps
                                            chat/memory/missions, offline routes local-only and
                                            reports degraded, crash recovery detects unclean
                                            shutdown / purges locks / reports interrupted missions
                                            / never auto-resumes / survives broken services
tests/e2e/test_phase14_updater.py      14   stage+hash, verify detects tampering, activate records
                                            previous, rollback restores it, rollback refused when
                                            empty / missing / corrupt
```
