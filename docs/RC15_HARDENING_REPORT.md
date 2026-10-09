# RC15 Release Hardening — A–X Report

**Date:** 2026-09-20 · **Branch:** `upgrade/genie-continuity-ui` · **Feature set: FROZEN**
**Status language:** FIXED / VERIFIED / PARTIAL / BLOCKED / NEWLY IMPLEMENTED / PRESERVED

---

## A. FLASHING TERMINAL INCIDENT STATUS

**ROOT PROCESS NOT YET CAPTURED. GENIE NOT PROVEN TO BE THE VISIBLE WINDOW SOURCE.**

This is deliberately kept separate from finding B. The evidence that `GENIE.exe`
and `pythonw.exe` are GUI-subsystem binaries rules out a GENIE *console*, but it
does not identify the owner's flashing process. LEDKeeper2 remains a lead, not a
root cause — its process/parent chain has not been captured.

* No GENIE process is running; no GENIE persistence exists anywhere
  (9 Run/RunOnce keys, Startup folders, IFEO, Winlogon, App Paths, 181 scheduled
  tasks, WMI consumers, service list — 0 matches by content).
* 15 minutes of 120 ms capture: 355 creations, 3 non-agent (all stock Windows).
* Process-capture tools are **kept**: `scripts/p0_process_capture.py`,
  `scripts/p0_persistence_sweep.py`.
* To close: run `python scripts/p0_process_capture.py --seconds 600` **while the
  flashing recurs**, in a session that is not running an agent.

**Next capture must record:** flashing executable, full command line, parent PID,
parent executable, root respawner, exit code, repetition interval.

## B. GENIE PLUGIN-HOST DEFECT STATUS — **FIXED + VERIFIED**

Packaged builds launched hosts with `python -m plugins.host`; the embeddable
interpreter's `._pth` isolates `sys.path` (PYTHONPATH and cwd ignored), so every
host died at import — 174 failures across 36 boots.

Fixed by launching `plugins/host.py` by script path, plus a persisted circuit
breaker, stderr drain, and an honest `health()` (see `PHASE_0_TERMINAL_RESPAWN.md`).

Verified in the **rc15b shipped payload**: **24/24** packaged checks, and the
installed app's daemon log shows all five hosts `started`:

```
plugin test_crash started (pid=16964)   plugin home started   (pid=15984)
plugin test_timeout started (pid=11148) plugin media started  (pid=16708)
plugin vscode started (pid=12916)
```

In the **rc15a payload the same check is 5/5 FAIL** — which is why rc15a is
superseded (section X).

## C. TEMP-DIRECTORY DELETER — **CLASSIFIED: THE INSTALLER**

**Not GENIE product code. Not the updater. Not Defender. Not Windows TEMP cleanup.**

Every NSIS install runs the **previously registered uninstaller** to remove the
old installation, then unpacks itself. Install *N* deletes install *N−1*.

The original hypothesis ("%TEMP% installs are unstable") is **wrong** — the
control matrix inverted it:

| Case | Location | App launched | Outcome |
|---|---|---|---|
| A | default `%LOCALAPPDATA%\Programs\GENIE` | no | 3545 files → **0** within ~30 s, deleted by the *next* install (C) |
| C | custom non-TEMP `E:\G3\GENIE\artifacts\caseC\GENIE-C` | no | 3545 files → **0**, deleted by the *next* install (D) |
| D | custom `%TEMP%\GENIE-CASED-…` | no | **3545 files, stable for ~7 minutes**, then deleted only when rc15b was installed |

So the non-TEMP installs died and the TEMP install survived — the opposite of the
reported pattern. What actually differed was *install order*.

**One secondary actor, self-inflicted:** `win32-x64.exe` (the WorkBuddy host's
`genie-trash` helper) was invoked with `e:\G3\GENIE\artifacts\caseC` because I ran
`rm -rf artifacts/caseC` in the same command chain as the install. That is my own
tooling, not the product, and it only affected case C.

## D. DELETE PROCESS / PID / COMMAND — **EVIDENCE**

Captured live during the rc15b default install:

```
15:57:46  GENIE Setup 0.1.0.exe   pid 14840
          dist-electron-rc15b\GENIE Setup 0.1.0.exe /S /D=C:\Users\ghostt\AppData\Local\Programs\GENIE
15:57:47  old-uninstaller.exe     pid 3316   PPID 14840
          C:\Users\ghostt\AppData\Local\Temp\nsoD611.tmp\old-uninstaller.exe
          /S /KEEP_APP_DATA /currentuser --updated
          _?=C:\Users\ghostt\AppData\Local\Temp\GENIE-CASED-1789899459
```

| Field | Value |
|---|---|
| Process image | `old-uninstaller.exe` (NSIS `old-uninstaller`, extracted to `%TEMP%\nsXXXX.tmp\`) |
| PID / PPID | 3316 / 14840 (`GENIE Setup 0.1.0.exe`) |
| Command line | `/S /KEEP_APP_DATA /currentuser --updated _?=<previous install dir>` |
| Target | the previously registered install directory |
| Timestamp | 15:57:47; target went 3545 → 0 files at 15:57:48 |

Reproduced three times (15:49:37 → case A, 15:49:51 → case C, 15:57:47 → case D).
Secondary: `win32-x64.exe pid 14000` at 15:49:25, argument `e:\G3\GENIE\artifacts\caseC`.

## E. UPDATER INVOLVEMENT — **NOT INVOLVED**

`core/updater.py` is constructed in exactly one place — `genie.py:809`,
`Updater(Path(cfg.data_dir) / "releases")` — and only from CLI subcommands. It is
never constructed during daemon startup or lifecycle. Its only recursive delete is
`shutil.rmtree(self.releases_dir / version)`.

**Hardened anyway (data-loss risk):** `stage()` now resolves the target and refuses
any version string that escapes the releases directory; without that guard
`stage("../../..")` would rmtree an arbitrary directory including an install root.
Three tests added, all passing (20/20 in that file):

* ordinary updater use never touches a separate install root
* re-staging one version removes only that version, never its siblings
* traversal version strings are refused and the install root survives

## F. HARNESS INVOLVEMENT — **NO CLEANUP BUG, BUT A METHODOLOGY FLAW**

`scripts/acceptance_installer.py` deletes only its own unique target, once, at the
start of a run (`_remove_install_dir`, line 180). It has no `atexit`, no stale-run
sweeper, and never removes an install under observation.

The flaw is different and it produced the historical false negatives: the harness
installs into a **new unique directory every run**, while the registry still points
at the previous one — so each run silently destroyed the previous run's install
directory. That is the real source of both "files vanished 5–10 minutes later" and
the run that could not find its uninstaller.

**Fix:** `scripts/p0_registry.py` now reads the registered install location (the
directory the next installer will uninstall first) so this is recorded before the
fact instead of discovered afterwards.

## G. DEFAULT INSTALL STABILITY — **STABLE (PASS)**

`scripts/acceptance_installer.py --dist dist-electron-rc15b` into
`C:\Users\ghostt\AppData\Local\Programs\GENIE`:

install rc=0 (1 attempt) → verified → launch alive, still running after 8 s →
`/health` 200 → close clean → relaunch alive → uninstall rc=0, exe removed.
Files remained present throughout every step up to the intentional uninstall.

## H. NON-TEMP CUSTOM INSTALL STABILITY — **STABLE ON ITS OWN**

Case C held its 3545 files until a later install removed it. No spontaneous
deletion observed in a non-TEMP location.

## I. TEMP INSTALL STABILITY — **STABLE — the original hypothesis is disproven**

Case D (`%TEMP%`) held 3545 files / 487,944,799 bytes for ~7 minutes and was only
removed when the rc15b install ran its uninstall-previous step. TEMP is not a
factor; install order is.

## J. PLUGIN HOST PACKAGED TEST — **24/24 PASS (rc15b payload)**

`scripts/verify_plugin_host_packaged.py` against
`dist-electron-rc15b/win-unpacked/resources/backend-runtime`: all five hosts start,
health answers with a real reason, circuit breaker opens for an unstartable plugin.
Source-tree tests alone were **not** relied on.

## K. AUTO-BOOTSTRAP — **PASS** — daemon answered on the first health attempt.
## L. HEALTH — **PASS** — HTTP 200, body `{"ok": true}`.
## M. GRACEFUL EXIT — **PASS for the frontend; daemon persists (open question)**

`GENIE.exe` exits cleanly. However `requestFullExit()` only closes windows,
destroys the tray and calls `app.exit(0)` — it never stops the backend. Six
`pythonw.exe` processes (daemon + 5 plugin hosts) survive. This is *why* an
uninstall can leave locked DLLs behind (the 35 surviving binaries in the old
history were exactly the DLLs a live daemon had loaded).

Flagged as `rc15b-1`. It is a behaviour question for the owner — should "Exit
GENIE" also stop the shared daemon? — not a build defect, and it was not changed
under the feature freeze.

## N. RELAUNCH — **PASS** — pid 6796, alive and still running after 8 s.
## O. UNINSTALLER PRESENCE — **PASS** — present and executable at
`C:\Users\ghostt\AppData\Local\Programs\GENIE\Uninstall GENIE.exe`.
## P. UNINSTALL — **PASS** — rc=0, `exe_removed=true`.
## Q. USER DATA PRESERVATION — **PASS** — `%LOCALAPPDATA%\GENIE` intact after
uninstall (40 files: `data`, `genie.sqlite`, `logs`, `models`, `runtime`,
`secrets`, `voice_artifacts`). `/KEEP_APP_DATA` is honoured;
`deleteAppDataOnUninstall` is not enabled.

## R. CURRENT HEAD
`21b68d80a59ed613640b66d861155ac5a82cf27e`

## S. PRODUCT SOURCE COMMIT
`09ee3355f2ba6ec31bbfef3a76a23d72b6df6d29`

| Field | Value |
|---|---|
| Current HEAD | `21b68d80a59ed613640b66d861155ac5a82cf27e` |
| Product source commit used to build the installer | `09ee3355f2ba6ec31bbfef3a76a23d72b6df6d29` |
| Manifest commit | `21b68d8` (same as HEAD) |
| Does HEAD contain test/docs-only commits after the product freeze? | **Yes** — `21b68d8` is docs + manifest only; `09ee335` also carried tests and tooling alongside the product change. Neither enters the payload. |

The installer was built from the working tree at product source commit `09ee335`
**before** `21b68d8` existed, so `21b68d8` cannot be in the payload. The two product
files changed since rc15a (`plugins/client.py`, `core/updater.py`) were confirmed
**byte-identical between source and shipped payload**, and no product file has been
touched since the build.

**Prior ambiguity resolved:** rc15a's manifest named `604f41e` as its source
commit while `5fe2488` (PHASE J) was the commit that wrote the manifest. That is
consistent, not a conflict: rc15a's *product* source was `604f41e`, and `5fe2488`
contained only the manifest, tooling and docs. PHASE 0 (`7281dad`) then changed a
product file, which is exactly why rc15a is superseded.

## T. INSTALLER PATH
`E:\G3\GENIE\dist-electron-rc15b\GENIE Setup 0.1.0.exe`

## U. INSTALLER SIZE
**139,546,423 bytes**

## V. FULL SHA-256 (complete, untruncated)

```
installer : 0a8b73afb8841b5f380a9c893bcc96d141dbbc1ae799a6c15157c91525192ec1
portable  : a271c488395d953d755e4beb46933f1b4ba440e5eb1cd1ef8bd0d0ce09f4a771
```

Built 2026-09-20T15:55:48 · unsigned (no signing identity configured) ·
electron-builder 25.1.8 · Electron 33.4.0 · embedded Python 3.12.6.

## W. HOME OWNER-ACCEPTANCE STATUS — **PENDING, NOT PASSED**

rc15b is installed at the default location with Start Menu (`GENIE.lnk`) and
Desktop shortcuts. Nothing about Home ghosting is marked PASS. The owner must
still verify Home at t = 0 / 1 / 3 / 5 s and inspect Home, Missions, Agents,
Computer, and Settings / provider management.

## X. RC15A KEEP / RC15B REQUIRED — **RC15B REQUIRED, THEN SUPERSEDED BY RC15C**

rc15a **cannot** go to owner acceptance: its payload predates the plugin-host fix,
and 5/5 plugin hosts fail in it. rc15a is kept untouched (its tree and installer
were not modified). rc15b carries the new identity, new SHA-256 and new manifest
(`artifacts/release_installer_manifest_rc15b.json`).

Per the decision rule, the TEMP deletion is **installer** behaviour — but it is
*intended* upgrade behaviour with `/KEEP_APP_DATA`, and it caused no owner-facing
data loss. No rebuild was needed for that finding alone; rc15b was required by
the plugin-host defect.

### Residual risks
* `rc15b-1` graceful exit leaves the daemon and 5 plugin hosts running.
* `rc15b-2` NSIS intermittently exits `0xC0000005` (rc15b install needed 2
  attempts; every attempt is recorded).
* `rc15b-3` each install removes the previously registered install directory —
  expected, but it invalidates any test that assumes earlier installs survive.
* Home ghosting and code signing still require the owner.

---

# Addendum — rc15c (lifecycle fix)

After the rc15b report above, section M's open question was resolved by evidence
rather than by opinion: the surviving processes are the direct cause of the
"files left behind" symptom that opened this investigation.

## The mechanism, reproduced

With six backend processes alive, an uninstall left **exactly 35 files** — and
they were precisely the DLLs a running embedded Python holds open
(`python312.dll`, `libssl-3.dll`, `_socket.pyd`, `_ssl.pyd`, …). The frontend had
exited; the backend had not, so Windows refused to delete its binaries.

## The defect

`requestFullExit()` closed windows, destroyed the tray and called `app.exit(0)`.
It never stopped the backend. "Exit GENIE" therefore left the daemon and its
five plugin hosts running, holding port 8787 and their own DLLs.

## The fix (product)

* `backend.js` records the daemon PID it spawns (`daemon.pid` in userData) and
  adds `stopDaemon()`, which terminates **that one PID** with `taskkill /T /F`
  so plugin hosts go down with it. Never a search by image name.
* `isOurDaemon()` confirms the PID still belongs to our embedded interpreter
  before killing, and **refuses by default**: if the path cannot be confirmed,
  nothing is killed, so a recycled PID can never be destroyed by mistake.
  `wmic` is not used (it is absent on current Windows); CIM is tried first,
  then `tasklist`, and an unconfirmable result means refuse.
* `main.js` calls `stopDaemon()` inside `requestFullExit()`.

## The harness bug that hid it

The acceptance harness closed the app with `taskkill /F`, which bypasses
`requestFullExit()` entirely — so the graceful path was never exercised and the
daemon was never stopped. The close and pre-uninstall steps now ask GENIE to
shut itself down (`--genie-shutdown`), wait for the frontend *and* the daemon,
and only force-kill as a recorded fallback.

## Verification

| | before (rc15b) | after (rc15c) |
|---|---|---|
| Files left after uninstall | **35** | **0** |
| GENIE processes left | **6** | **0** |
| `backend_stopped` on close | n/a (never attempted) | **true** |

* `scripts/verify_daemon_stop.js` — **7/7** with electron's `app` stubbed: kills a
  real daemon and its children, clears the pid file, refuses a PID owned by an
  unrelated process, handles already-exited and no-pid cleanly.
* Default-install acceptance on rc15c — **PASS** (install rc=0, launch alive,
  `/health` 200, close clean, relaunch alive, uninstall rc=0, user data kept).
* Plugin hosts in the rc15c payload — **24/24**.

## rc15c identity

| | |
|---|---|
| Path | `E:\G3\GENIE\dist-electron-rc15c\GENIE Setup 0.1.0.exe` |
| Size | 139,547,700 bytes |
| SHA-256 (complete) | `9aa757a5b69631af539ca400fd42337360a3d601837c71b5e2e6bddcf6dd3c32` |
| Portable SHA-256 | `bc0fca4a13768f44439cfa420d0160141ca699843c5905235db73dc2d9ee9521` |
| Built | 2026-09-20T16:18:43 |
| Source commit (= HEAD) | `cc0caa0ff2ec5368b5641220a2f325657ad194df` |
| Signed | no |

Owner checklist: [RC15C_ACCEPTANCE_CHECKLIST.md](RC15C_ACCEPTANCE_CHECKLIST.md).

---

# Addendum 2 — the flashing-window measurement

Section A stayed BLOCKED until this could be measured rather than argued. A
control measurement now puts a number on it.

## Idle vs active

| window | process creations | console hosts (conhost / OpenConsole / WindowsTerminal) |
|---|---|---|
| 4 minutes, no commands issued | **10** | **1** |
| 35 minutes with the agent working | **2,778** | **1,655** (~47 per minute) |

Console-host creation is almost entirely a function of *the tooling running*, not
of GENIE. Nothing in GENIE creates a console host.

## The largest single source

`conhost.exe <- tasklist.exe` — **521 instances**. The harness polled
`process_alive()` every second during every wait, and `tasklist` is a console
application, so each poll briefly created a console window. That is a window
opening and closing too fast to read, dozens of times, produced by the
investigation itself.

## What this means for section A

A leading hypothesis, stated as a hypothesis and not as a conclusion:

> The owner's flashing window is likely a console host created by tooling
> (agent shells, or a `tasklist`-style poller), not by GENIE.

It fits every reported fact: it began around heavy automated testing, it
continued after GENIE was uninstalled, and it repeats rapidly. It is still
**BLOCKED** as a *definitive* identification — no capture has attributed the
owner's specific window — but the measurement now tells the owner exactly how to
settle it: watch whether the flashing stops when no tooling is running.

## Changes made because of it

* `scripts/acceptance_installer.py` — `process_alive()` no longer shells out to
  `tasklist`. It enumerates processes in-process (ctypes `EnumProcesses` +
  `GetModuleBaseNameW`) and only falls back to `tasklist` if that cannot answer.
  Removes ~521 console windows per acceptance run.
* `scripts/p0_process_capture.py` — end-of-run `--summary` that separates
  candidates from console hosts, and never silently discards console hosts
  (a flashing window would be one of them). `--summary-only` re-summarises any
  existing capture.
* `scripts/capture_flashing_window.bat` — one-click 5-minute capture for the
  owner, with a readable summary at the end.
* `tests/unit/test_acceptance_harness.py` — 5 regression tests: `process_alive`
  detects and clears correctly, and **never spawns a process while polling**;
  `graceful_exit` needs no external command when nothing is running, and reports
  the fallback when it does force-kill (with `run` intercepted so the test never
  issues a real taskkill).

---

# Addendum 3 — Home, read before the owner looks at it

Section W cannot be closed without the owner, but the code can be read first so
the check is precise rather than vague. Two things to know:

## 1. The greeting markup is hardcoded

`ui/web/home.html:76`

```html
<h1 id="greet-line">Good Evening</h1>
```

`setGreeting()` (home.js:41) overwrites it at boot with the correct value for the
current hour, but until that runs the page says "Good Evening" whatever the time.
At 10:00 it therefore shows the wrong greeting briefly. **This is not ghosting** —
it settles on its own — but it is exactly the sort of thing that gets reported as
one. Judge the greeting at t = 3 s and t = 5 s, not at t = 0.

## 2. The greeting never refreshes

`setGreeting()` is called once, from `boot()` (home.js:362). `tickClock()` runs
every 30 s but only updates `#ts-date` and `#ts-time` — it never re-evaluates the
greeting. A session left open across 12:00 or 17:00 keeps a stale greeting until
reload.

The two quotes (`home.html:79` and `:203`) are static by design and are not meant
to change.

## Disposition

Both are one-line fixes. Neither is a release blocker, a data-loss risk, a
lifecycle defect, an installer defect or a harness-correctness issue, so under
the feature freeze they are **reported and not changed**. Offer them to the owner
as an optional rc15d.

## Verification status

Full deterministic suite: **1504 passed, 21 failed, 79 deselected** — the same 21
sandbox bulk-delete-guard failures present before this work started, and none of
them mine. Frontend 87/87. Plugin hosts 24/24 in the payload.

---

# Addendum 4 — portable build and a transient voice warning

## Portable: works, but slower

`GENIE-0.1.0-portable.exe` was smoke-tested for the first time (it had only ever
been built and hashed before).

* Launches, extracts to `%TEMP%\<random>\`, and the backend answers `/health`
  with HTTP 200 — **healthy after ~16 s**, versus ~1–2 s for the installed build,
  because the portable re-extracts on every launch.
* An earlier attempt hit `readiness_timeout` at 30 s. The cause was two portable
  instances launched on top of each other while the first was still extracting;
  run alone it is healthy in 16 s. Worth knowing: the 30 s readiness budget is
  tight for a cold portable start.
* After the run the extraction directory was cleaned up automatically and no
  GENIE processes remained. The installed build was untouched (3545 files).

## One `voice.tts_level` warning — portable only, not reproducible

The daemon logged, once:

```
17:03:04 voice service unavailable: No module named 'voice.tts_level'
```

Checked rather than assumed:

* `voice/tts_level.py` **is** present in the source tree and **is** packaged
  (`.../app/voice/tts_level.py`), so this is not another missing-file packaging
  gap like the earlier stale-runtime problem.
* Importing it with the shipped interpreter succeeds.
* It appears **exactly once** in the log, only during the portable run, and never
  in any installed-build run.

Most likely the module had not finished being extracted when the daemon imported
it. Classified as an observation about a cold portable start, not a product
defect — but if the owner ever sees voice fall back to synthetic audio on a
portable launch, this is the line to look for.

## End-to-end proof on the installed build (not just unit tests)

`scripts/verify_full_exit_e2e.py` drives the real installed app:

```
launch installed GENIE -> /health 200      PASS
processes up (frontend + daemon + hosts)   PASS  (10 processes)
daemon pid recorded                        PASS  (pid 2952)
--genie-shutdown
frontend exited                            PASS
backend stopped (/health dead)             PASS
no GENIE processes remain at all           PASS  (0)
recorded pid file cleared                  PASS
                                           7/7
```

This is the claim rc15c rests on, now demonstrated on the installed build
rather than only with electron stubbed. Note the launch must strip
`ELECTRON_RUN_AS_NODE` or Electron starts as plain Node and the app silently
quits - the same trap the acceptance harness documents.

## Uninstaller cleanliness (shortcuts)

Previously unverified. Tested by installing to a scratch directory, uninstalling,
and inspecting the shortcut locations:

| stage | Start Menu `GENIE.lnk` | Desktop `GENIE.lnk` |
|---|---|---|
| after install | present | present |
| after uninstall | **removed** | **removed** |

The scratch install directory was left with **0 files**. The default install was
then restored for the owner (3545 files, both shortcuts back). So the uninstaller
cleans the install tree *and* both shortcuts, and leaves no orphans.
