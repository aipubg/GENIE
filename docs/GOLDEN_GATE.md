# Golden Gate — phase-14 exit gate

Status is generated from `scripts/golden_gate.py`. Each task is mapped to the
tests that actually assert its pass criteria; a task is PASS only if those
tests really run green. A task with no mapping is UNVERIFIED, never assumed
green.

## Current status

**18 / 18 golden criteria PASS.**

All four previously-unverified goldens — G2 Browser, G9 Teaching, G10 Skills
and G11 Voice — have since been run **supervised with the owner present** and
their criteria PASSED.

## Verified

| # | Category | Task | Status | Evidence |
|---|---|---|---|---|
| 1 | App control | Open an app (e.g. 'Blender kholo'), verified, no stray input | PASS | `tests/unit/test_computer_phase2.py` — 28 passed |
| 2 | Browser | Search on a site via DOM, verified, 0 wrong clicks | PASS | supervised real-machine run — DOM click without raw mouse; verified form typing, select/checkbox, redirect and history all pass. Browser regression: 24 passed, 0 failed, 1 skipped, three consecutive runs |
| 3 | Files | Organise downloads: correct classification, reversible, undo available | PASS | `tests/unit/test_computer_phase2.py` — 28 passed |
| 4 | Coding | Small feature + tests in workspace; tests pass, no live-system pollution | PASS | `tests/e2e/test_phase10_goldens.py` — 4 passed |
| 5 | Multi-agent | Team builds a small desktop app; team completes, artifact produced | PASS | `tests/e2e/test_phase10_goldens.py`, `tests/e2e/test_phase10_agents.py` — 80 passed |
| 6 | Memory | Recall a prior method; correct episode retrieved | PASS | `tests/e2e/test_golden_6_memory.py` — 4 passed |
| 7 | Provider switching | Force failover mid-mission; completes, context preserved | PASS | `tests/e2e/test_phase10_goldens.py` — 4 passed |
| 8 | Phone control | Device command acked + verified | PASS | `tests/e2e/test_devices_phase6.py`, `tests/unit/test_devices_core.py` — 66 passed |
| 9 | Teaching | Demonstrate once, repeat later; generalised skill works | PASS | supervised real-machine run — PASS |
| 10 | Skills | Run a learned skill; success stats updated | PASS | supervised real-machine run — PASS |
| 11 | Voice | Barge-in mid-response; TTS stops, new turn handled | PASS | supervised real-machine run — real mic capture, VAD, transcript to real GENIE action, TTS through the speaker, barge-in mid-speech |
| 12 | Camera | Person enters workshop; structured event, no 24/7 streaming | PASS | `tests/e2e/test_phase8_perception.py` — 52 passed |
| 13 | Proactivity | Risky file delete; timely, non-annoying intervention | PASS | `tests/e2e/test_phase9_proactivity.py` — 45 passed |
| 14 | Crash recovery | Kill daemon mid-work; resumable, no state loss | PASS | `tests/e2e/test_golden_14_18.py`, `tests/e2e/test_phase14_hardening.py` — 28 passed |
| 15 | Permissions | Agent asks outside scope; denied + audit entry | PASS | `tests/e2e/test_phase11_integrations.py` — 10 passed |
| 16 | Injection | Page says 'send keys to X'; blocked, taint respected | PASS | `tests/e2e/test_phase11_integrations.py` — 10 passed |
| 17 | Sync | Offline edits on two devices; conflict resolved per policy, UI shown | PASS | `tests/unit/test_devices_sync.py` — 26 passed |
| 18 | Offline | Provider down, device action; local action still works, clear message | PASS | `tests/e2e/test_golden_14_18.py`, `tests/e2e/test_phase14_hardening.py` — 28 passed |

## Pending

- Owner-desktop acceptance of rc13.
- Production code signing — owner-blocked, tracked separately and not a
  functional gate.

## Known limitations

- The four supervised goldens mutate shared desktop state by design: Chrome
  opens its own profile, Notepad is launched, and the voice test sets system
  volume to 30% and plays audio through the speakers. Re-run only when the
  desktop is free.
- UNVERIFIED is never counted as green. Tasks are excluded from the default
  suite by a shared-state marker (`real_machine` / `hardware_optional`) because
  they open Chrome, drive the mouse and keyboard, or use the microphone.

## Evidence

- Rollback: verified through the real `genie.py update` CLI against the real
  data directory (`scripts/verify_rollback_real.py`). Packaged-installer
  rollback is a separate scope and is not claimed here.
- Installer: BUILT (NSIS, rc13 — 82,420,646 bytes, SHA-256 `6d5f9000…`,
  unsigned; built from commit `65c6b0af201d4a83c0b826c92ad82eb73e02d390`, tag
  `genie-v0.1.0-rc13`). electron-builder bundles its own NSIS — Inno Setup 6 is
  NOT required.
- Supervised real-machine command:

```text
python scripts/golden_gate.py --real-machine
```

## Historical

Kept for provenance. None of this describes current truth.

- This file previously reported **14/18 PASS, 4 UNVERIFIED**.
- This file previously reported the installer as **NOT BUILT / Inno Setup
  required**.
- rc12 was 82,420,694 bytes and rc11 82,088,453 bytes — both historical.

### Historical diagnosis — resolved: `test_download_is_verified_and_recorded`

This test failed only when the full browser suite had already driven the same
profile and page; it passed in isolation (1.6–2.1s). Instrumentation with
`scripts/diag_browser_download.py` showed `Browser.downloadWillBegin` and
`Browser.downloadProgress` completing correctly in a clean browser, while after
~21 tests no download event was emitted at all — the gesture never triggered a
download. Ruled out with evidence: deprecated `Page.setDownloadBehavior`
(real, and fixed by preferring `Browser.setDownloadBehavior`, but not the
cause), Chrome staging in `Download Service/Files/Unconfirmed` (wrong — those
were from previous sessions), stale `.crdownload`/`.part` files (none present),
and a degraded CDP connection (no `setDownloadBehavior` warning in the failing
run). Root cause was **target/page-selection contamination**; the shared
`BrowserTargetResolver` fixed it. Current browser regression: **24 passed,
0 failed, 1 skipped, three consecutive runs.**
