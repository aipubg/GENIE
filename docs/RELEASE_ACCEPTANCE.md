# GENIE — Release Acceptance

Final release gates and their current outcome.

## Current status

**RC13 BUILT — OWNER DESKTOP ACCEPTANCE PENDING.**

- rc13 is built and is the current release candidate.
- Production signing is **owner-blocked** and tracked separately. The binary is
  unsigned; an unsigned installer still executes, so signing is not the cause of
  any current failure.
- No sandbox-disabling flag is shipped.

## Current artifact

| Field | Value |
|---|---|
| Release candidate | rc13 |
| Installer | `dist-electron\GENIE Setup 0.1.0.exe` |
| Preserved copy | `dist-electron\GENIE Setup 0.1.0-rc13.exe` |
| Size | 82,420,646 bytes |
| SHA-256 | `6d5f900098609f34651916369f01cedbffbe6106c28cb3702d0d91c1fd06d274` |
| Built source commit | `65c6b0af201d4a83c0b826c92ad82eb73e02d390` |
| Tag | `genie-v0.1.0-rc13` |
| Manifest | `artifacts/release_installer_manifest_rc13.json` |
| Packaging | electron-builder 25.1.8, Electron 33.4.0, NSIS |
| Signing | unsigned |

The built source commit above is the source of the packaged binary. Later
commits are release metadata and documentation only, and must never be reported
as the source of the built installer.

## Verified

- Frontend tests: 45 passed, 0 failed.
- API/UI contract tests: 14 passed, 0 failed.
- Frontend freeze gate: 13 of 13 routes return 200; Memory, Computer and
  Security recaptured; no `[object Object]`, no `undefined`, no `NaN`, no raw
  epoch, no console exceptions.
- Icon **resources** verified: the `GENIE.exe` embedded icon, the installer
  embedded icon and the packaged icon resources all match
  `ui/assets/brand/app-icon.ico` at all 8 sizes. Shell-visible appearance
  (Desktop shortcut, Start Menu, taskbar, Alt+Tab and tray) is **pending visual
  confirmation** by the owner — it is not reported as accepted yet.
- Golden gate: 18 of 18 criteria PASS.
- Full real-machine suite: 68 PASS / 0 FAIL / 3 SKIP. Skip breakdown: Blender ×2
  ENVIRONMENT_UNAVAILABLE (Blender not installed); Chrome tab-startup ×1, a
  non-gate environment/browser-startup skip.
- Wrong-action baseline: 31 executed, 31 verified, 0 wrong, rate 0.0000, 8
  consecutive runs — meets target for this measured sample. Not a claim of a
  universal 0% wrong-action rate.
- Core updater rollback: VERIFIED through the real `genie.py update` CLI against
  the real data directory.
- Installer **artifact correctness** — packaging and static checks (valid PE,
  NSIS markers, embedded icon): verified for rc13.
- Installer **lifecycle verified for rc13 on a clean machine** (install root
  outside the source checkout): clean install rc=0 with `GENIE.exe` present and
  3 shortcuts; installed and uninstaller icons match; uninstall removes the tree
  (0 entries remaining); install over an existing install returns rc=0 with
  graceful shutdown requested **and** succeeded (2.2 s), forced cleanup **not**
  used, and user data preserved.
  **Not verified: default launch** — it exits `2147483651` (`0x80000003`) on
  this machine, so launch, live second-instance behaviour and relaunch remain
  pending the owner desktop.

## Pending

Owner desktop acceptance of rc13, in order:

1. Verify hash
2. Install
3. Launch
4. Wait
5. Verify window/backend
6. Test second launch
7. Reinstall while running
8. Relaunch
9. Exit
10. Uninstall

```text
python scripts\desktop_acceptance.py
```

Launch normally. Do not pass `--no-sandbox`, `--disable-gpu-sandbox` or
`--in-process-gpu`.

## Known limitations

- **Owner-desktop launch decides the GPU question.** The agent machine cannot
  launch the app, but that alone is not evidence about a normal desktop.

  - **Case A — owner desktop default launch PASSES:** the previous agent-machine
    GPU failure is classified **ENVIRONMENT-SPECIFIC**. No compatibility
    workaround is shipped.
  - **Case B — owner desktop default launch ALSO FAILS:** this is a
    **PRODUCT / ENVIRONMENT COMPATIBILITY ISSUE**, not an agent-environment
    artifact, and must not be labelled environment-specific. Reopen the
    mitigation investigation: evaluate `--disable-gpu-sandbox` first, then
    `--in-process-gpu`. `--no-sandbox` remains diagnostic-only.
- Packaged-installer rollback is **not claimed** — it is a separate scope from
  the verified core updater rollback.
- Optional external specialists are unavailable and are not core release
  failures: Strix (Docker daemon missing), MiroFish (upstream runtime missing).
- Packaged A/B staged activation is not supported.

## Evidence

What exists, stated as facts:

- Installer-requested shutdown uses the canonical full-exit path
  (`GENIE.exe --genie-shutdown`). Tray "Exit GENIE" uses the same path. The
  installer asks a running GENIE to close itself and waits; it never force-kills
  by default.
- A single-instance lock ensures the shutdown request reaches the running
  instance.
- Installer and shortcut configuration points to the GENIE icon, and the
  embedded icon resources are verified. Actual shell-visible Desktop / Start
  Menu / taskbar / Alt+Tab / tray appearance remains **pending owner visual
  confirmation** — it cannot be reported PASS until the owner sees it. If the
  shell still shows the old icon while embedded resources are verified, first
  verify the shortcut target and icon resource; if those are correct, refresh
  the Windows shell icon cache with `ie4uinit.exe -show`.
- Every rendered value passes through `esc()`. Structured values are normalised
  in `ui/web/helpers.js`: `humanValue()` is the generic formatter for people,
  agents, actors and ids; `humanErr()` stays narrow for error objects;
  `humanList()` uses `humanValue()` so `reported_by` renders as labels.
- Empty screens show honest empty states; no screen is filled with fake data.
- Memory reads `/api/memory.hits`; Computer reads the real workspace binding
  `status.computer.workspace.{root,bytes}`.
- `helpers.js` is loaded before `pages.js` in every operational shell, enforced
  by `ui/tests/frontend/script-order.test.js`.

## Historical

Kept for provenance only. None of this describes current truth.

- **rc12** — 82,420,694 bytes, SHA-256
  `3c7a4fbb3431212da9c8cb8a4a0cc27047f29943061ddfa569e1dc4770bc34b7`.
  Superseded by rc13.
- **rc11** — 82,088,453 bytes, SHA-256
  `c40cae84a9445745aa96933cdee351633de307545d38e57a9c25dbd9a1d8c26b`. Install
  and uninstall PASS; app launch failed with `0x80000003` STATUS_BREAKPOINT.
- The golden gate previously reported 14 of 18 PASS with 4 UNVERIFIED; the four
  supervised goldens have since been run and PASS.
- The browser download investigation (`test_download_is_verified_and_recorded`)
  is resolved: the cause was target/page-selection contamination, fixed by the
  shared `BrowserTargetResolver`. Current browser regression: 24 passed, 0
  failed, 1 skipped, three consecutive runs.
- The desktop icon previously carried Electron's default because the ICO stored
  every size as a PNG-compressed entry, which rcedit does not apply; it was
  regenerated with BMP entries.
- The root `installer/` directory holds a legacy, unverified Inno Setup spec.
  The current packaging path is `ui/electron` with electron-builder.
