# PHASE 0 — Terminal Respawn Incident

**Date:** 2026-09-20
**Branch:** `upgrade/genie-continuity-ui`
**Report language:** ALREADY EXISTED / PRESERVED / MERGED / FIXED / DEDUPLICATED /
NEWLY IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / PLANNED

---

## TERMINAL RESPAWN INCIDENT (mandatory in every future report)

> **Status: BLOCKED — root respawner NOT identified. GENIE is DISPROVEN as the
> source of a visible console window, but a real GENIE crash→respawn defect WAS
> found and FIXED.**
>
> * Reported symptom: a terminal/console window opens and closes too fast to read,
>   repeatedly; began around the first launch of the installed GENIE app; continued
>   after GENIE was uninstalled.
> * **GENIE cannot produce a visible console window.** Every executable GENIE ships
>   is GUI-subsystem: `GENIE.exe` = `WINDOWS_GUI`, `pythonw.exe` = `WINDOWS_GUI`.
>   The only console-subsystem binary in the bundle (`python.exe`) is never launched
>   by the app — the daemon is launched as `pythonw.exe` and plugin hosts inherit
>   `sys.executable` (`pythonw.exe`) with `CREATE_NO_WINDOW`.
> * **No GENIE persistence exists** anywhere: 0 footprint matches across 9 Run /
>   RunOnce keys, both Startup folders, IFEO debugger keys, Winlogon, App Paths,
>   181 scheduled tasks, WMI permanent event consumers and the service list.
> * **No respawn loop is currently active.** 15 minutes of 120 ms polling captured
>   355 process creations; 352 were the investigating agent's own tooling, and the
>   3 that were not are stock Windows (`backgroundTaskHost.exe`, `svchost.exe`,
>   `MoUsoCoreWorker.exe`).
> * **A genuine GENIE crash→respawn defect was found and FIXED** (see P0.4/P0.5):
>   in the packaged build **all 5 plugin hosts died instantly on every daemon boot**
>   — 174 failures across 36 boots. They were invisible (GUI subsystem), so this is
>   *not* the reported flashing window, but it is exactly the class of defect the
>   incident is about and it is now bounded.
> * **Strongest non-GENIE candidate:** `LEDKeeper2.exe` (MSI Center Mystic Light)
>   crashes in a tight loop — 31 `Application Error 1000` records and 16
>   `.NET Runtime 1026` unhandled-exception records, several within the same
>   minute (`System.NullReferenceException` in `MSI_LED.App.USBEventWatcher`).
>   It predates the GENIE work (09-17 → 09-19). Subsystem is GUI, so it is a
>   candidate for a *flashing window*, not a console.
> * **Next action to close this:** run
>   `python scripts/p0_process_capture.py --seconds 600 --out artifacts/p0_capture.jsonl`
>   *while the flashing is happening*, in a window that is not running an agent.
>   Nothing was killed, disabled or deleted during this investigation.

---

## P0.1 — Capture the actual process

| Question | Answer | Evidence |
|---|---|---|
| Is a process respawning right now? | **No** | 15 min capture: 355 creates, 3 non-agent (all stock Windows) |
| GENIE processes alive | **0** | no `GENIE.exe` / `pythonw.exe` / packaged `python.exe` running |
| Image subsystem of GENIE binaries | `GENIE.exe` = GUI, `pythonw.exe` = GUI, `python.exe` = console (unused) | PE optional-header subsystem read directly |
| Daemon boot rate | 36 boots in `genie.log`; repeated **double boots 0–6 s apart** (23:30:35→23:30:39, 03:23:25→03:23:31, 04:12:30→04:12:36, 05:38:36→05:38:40) | `%LOCALAPPDATA%\GENIE\data\logs\genie.log` |
| Who started the 05:38 daemon | **Not Electron** — `daemon-bootstrap.log` has no entry at that time | `%APPDATA%\genie-desktop\logs\daemon-bootstrap.log` |
| Root respawner of the flashing window | **UNIDENTIFIED** | see BLOCKED above |

New tooling added (non-destructive, observe-only):

* `scripts/p0_process_capture.py` — 120 ms poll of process creation; records PID, PPID,
  image path, full command line, cwd, user, creation timestamp, **full parent chain**,
  lifetime and **real exit code** (retained `SYNCHRONIZE` handle +
  `GetExitCodeProcess`). Never kills, disables or deletes anything.
* `scripts/p0_persistence_sweep.py` — read-only audit matched by **content**, not name.

## P0.2 — GENIE-owned or not

| Observation | Verdict |
|---|---|
| `GENIE.exe`, `pythonw.exe` plugin hosts, `pythonw.exe` daemon | GENIE-owned |
| `LEDKeeper2.exe` crash loop | **NOT GENIE** (MSI Center Mystic Light) |
| `GENIE Setup 0.1.0.exe` — 21 crashes, `System.dll`, `0xc0000005` | GENIE-owned; the known NSIS flake (`rc15a-2`), occurs during our own install runs |
| `GENIE.exe` — 22 crashes, `0x80000003` (breakpoint) | GENIE-owned; all from our own acceptance launches in `%TEMP%`, `GENIE-Acceptance`, `artifacts/owner-acceptance`, `dist-electron*/win-unpacked` |
| `electron.exe` — 5 crashes, `0x80000003` | dev-tree Electron, not the shipped app |
| `python.exe` — 1 crash, `0xc0000096` | system Python 3.12, one-off |

## P0.3 — Persistence audit (all read-only)

Searched **by content** for the GENIE footprint (`GENIE`, `genie-desktop`,
`backend_entry`, `backend-runtime`, `plugins.host`, `E:\G3\GENIE`):

| Location | Result |
|---|---|
| HKCU/HKLM `Run`, `RunOnce`, `RunOnceEx`, Policies `Run` (9 keys) | **0 matches** |
| Startup folders (user + common) | only `desktop.ini` |
| IFEO `Debugger` / `GlobalFlag` | **0 entries at all** |
| Winlogon `Shell` / `Userinit` | stock (`explorer.exe`, `userinit.exe`) |
| `App Paths` | **0 GENIE matches** |
| Scheduled tasks (181) | **0 matches**; no enabled task repeats faster than 1 hour |
| Services | **0 GENIE services** |
| WMI permanent event consumers | 0 `CommandLineEventConsumer` |
| Electron auto-launch | none (no login-item entry anywhere) |

A stale Windows Firewall rule references a **different, now-absent** GENIE at
`E:\genie\release\final\genie\_internal\pinchtab.exe`. `E:\genie` does not exist —
the rule is an orphan from an unrelated earlier product and cannot run anything.

Service Control Manager shows **no** restart loop: one Defender 7031 (09-17) and one
Claude-service 7034 (09-18). No 7031/7034 for any GENIE or LED service.

## P0.4 — Codebase respawn audit

First-party `spawn`/`Popen`/`subprocess` sites inventoried:

| Site | Bound? | Notes |
|---|---|---|
| `plugins/client.py::_spawn` | **was unbounded across boots** | the defect fixed in P0.5 |
| `ui/electron/backend.js` | bounded | `pythonw.exe`, `windowsHide: true`, `detached`, atomic spawn lock, 30 s readiness timeout. **No loop.** |
| `ui/electron/main.js` | no loop | `app.exit(0)` only; no `relaunch`, no `setInterval` |
| `agents/evolution_vcs.py`, `computer/shell.py`, `computer/workspace.py`, `director/*`, `integrations/*`, `plugins/installed/vscode/adapter.py` | one-shot `subprocess.run` | no restart logic |

**No unbounded respawn loop exists in first-party code.**

## P0.5 — Crash→respawn: root cause found, FIXED

**Root cause.** The packaged build runs an embeddable CPython whose `._pth` file
isolates `sys.path`: `.` anchors at `python/`, and **PYTHONPATH and the cwd are
ignored**. `plugins/client.py` spawned hosts with `-m plugins.host`, so the
interpreter could never resolve the `plugins` package:

```
pythonw.exe: Error while finding module specification for 'plugins.host'
             (ModuleNotFoundError: No module named 'plugins')
```

Every host therefore exited instantly, on every boot, for all 5 plugins
(174 log records / 36 boots ≈ 5 per boot). `plugins/host.py` already had a
`sys.path` self-anchor, but `-m` fails before that line can ever execute.

**Fix (FIXED, canonical):**

1. `_spawn()` now launches the host **by script path**
   (`[python, <app>/plugins/host.py, --plugin <dir>]`) instead of `-m plugins.host`.
   The script then anchors `sys.path` itself. Verified to work in both the dev tree
   and the packaged embeddable tree.
2. **Circuit breaker.** A host that dies within `FAST_FAIL_S = 2.0 s` never reached
   `initialize`, so its failure is deterministic. Those are counted in a persisted
   ledger (`<data>/plugins/startup_circuit.json`); two consecutive startup failures
   open the breaker with exponential cooldown (300 s → 7200 s cap). A plugin that
   cannot start is then **never respawned**, across daemon restarts. Runtime crashes
   keep the existing in-process three-strike rule because those may be transient.
3. **stderr is now drained** by a reader thread — it diagnoses a dying host (the real
   error text is now in the log) and removes a latent hang: an unconsumed 64 KB
   stderr pipe could block a chatty host forever.
4. **Truthfulness fix:** `health()` no longer relabels a legitimate negative health
   answer (e.g. "no controller configured") as "health check failed".

Worst case per plugin is now 2 doomed spawns ever, then silence — instead of 5
doomed spawns per boot, forever.

## P0.6 — Console visibility

**No visibility change was made, by design.** The directive requires the root cause
first, and there is nothing to hide: no shipped GENIE process is console-subsystem.
Adding `CREATE_NO_WINDOW`/`windowsHide` everywhere would mask a symptom without
treating anything. `backend.js` already uses `pythonw.exe` + `windowsHide: true` +
`detached` + `unref()`, and plugin hosts already pass `CREATE_NO_WINDOW`.

## P0.7 — Process ownership table

| Executable | Subsystem | Owner | Console? | Status |
|---|---|---|---|---|
| `GENIE.exe` | GUI | GENIE (Electron) | no | shipped |
| `pythonw.exe` (daemon) | GUI | GENIE | no | shipped |
| `pythonw.exe` (plugin host) | GUI | GENIE | no | **was dying instantly — FIXED** |
| `python.exe` (embedded) | console | GENIE | would flash | **never launched by the app** |
| `LEDKeeper2.exe` | GUI | MSI Center | no | **crash loop, not GENIE** |
| `LightKeeperService.exe` / `Mystic_Light_Service.exe` | service | MSI Center | n/a | running, no restart loop |
| `backgroundTaskHost.exe`, `svchost.exe`, `MoUsoCoreWorker.exe` | — | Windows | no | normal |

## P0.8 — Uninstall cleanup

* `deleteAppDataOnUninstall` is **not** enabled, so uninstall does not delete user data
  (`%LOCALAPPDATA%\GENIE`). **VERIFIED by configuration.**
* Uninstall leaves behind **no** auto-start entry of any kind (P0.3 = 0 matches).
  **VERIFIED.**
* Uninstall does not kill or restart anything after it completes. **PARTIAL** — the
  uninstaller's `--genie-exit` handshake is exercised by acceptance, but the
  post-uninstall steady state was measured here only by the absence of processes and
  of any persistence entry.

## P0.9 — Reproduction matrix

| # | Scenario | Result |
|---|---|---|
| A | Idle machine, no GENIE installed | **no respawn** (15 min capture: 3 non-agent events, all stock Windows) |
| B | GENIE daemon running | no respawn observed; daemon boots are not self-restarting |
| C | Installed app launched | GENIE.exe exits in 164 ms in this sandboxed context — **cannot reproduce the GUI session here** (marked BLOCKED, not passed) |
| D | Plugin host crash path | **REPRODUCED and FIXED** — all 5 hosts died; now all 5 start |
| E | Circuit-breaker behaviour | **VERIFIED** — a plugin that cannot start is refused, not retried |
| F | Uninstall then observe | not currently reproducible: the incident predates this session |
| G–K | Reboot / logon / service-restart / scheduled-task / WMI triggers | **nothing to trigger** — 0 persistence entries found |

## Verification ledger

| Gate | Result |
|---|---|
| `tests/e2e/test_plugins_phase4.py` | **22 passed** (run three times — breakers are not sticky) |
| Packaged-runtime plugin verification (`_verify_plugin_host_packaged.py`) | **24/24 passed** — all 5 hosts start, health answers correctly, breaker opens for an unstartable plugin |
| `py_compile plugins/client.py` | OK |
| Persistence sweep | **0 footprint matches** |
| Process capture | 355 creates / 3 non-agent, all stock Windows |

## Honest gaps

* **BLOCKED:** the flashing window's identity. Needs a capture taken while it recurs
  in a session that is not running an agent.
* **PARTIAL:** scenario C (real installed GUI launch) cannot run in this sandbox.
* **UNRESOLVED (pre-existing, `rc15a-1`):** installed files vanish from `%TEMP%`
  install directories 5–10 minutes after install. Not Defender, not GENIE code.
* **`rc15a-2`:** NSIS installer intermittently exits `0xC0000005` (21 records).
  Reproduced as environment flakiness; acceptance now retries and records attempts.
