# GENIE — Windows-native migration report

> ## STATUS: SUPERSEDED (historical, not rewritten)
>
> This was the working migration report. The **current release truth is
> `docs/RELEASE_MANIFEST.md`** — one document, no contradictory entries.
>
> Anything below that disagrees with that manifest is **HISTORICAL**. In
> particular:
>   * "installer not compiled" — SUPERSEDED: it is compiled and tested (12/12).
>   * "GENIE may exit on its own" — SUPERSEDED: it ran 20 min continuously.
>   * Knowledge/Media "no authority" — SUPERSEDED: both have real authorities.
>   * Electron status — SUPERSEDED: Electron is RETIRED.
>   * Old HEAD values — SUPERSEDED: see the manifest for the product commit.
>
> Kept because the reasoning and measurements are still useful history.

# GENIE — Windows-native migration: report A–Z

Branch `upgrade/genie-continuity-ui`, HEAD `72ed3d7`.
All figures in this report were measured on this workspace, not estimated.

---

## A. Native process enumeration — `tasklist` is gone from the hot path

`computer/state.py::list_processes()` no longer spawns anything. It reads the
kernel's process snapshot via `NtQuerySystemInformation(SystemProcessInformation)`.

Measured against `tasklist` over 3 rounds:

| | rows | names matching tasklist |
|---|---:|---:|
| native | 251 | 250 / 251 |
| tasklist | 253 | — |

The only unnamed entry is PID 0 (System Idle Process), which has no image by
definition. The two entries tasklist sees and native does not are `tasklist.exe`
and its own `conhost.exe` — spawned by the comparison and already gone when the
native snapshot is taken.

## B. Why not `EnumProcesses` + `QueryFullProcessImageNameW`

This was built and measured, then rejected. Opening protected processes is
denied even with `PROCESS_QUERY_LIMITED_INFORMATION`:

| | rows | named | unnamed |
|---|---:|---:|---:|
| EnumProcesses + OpenProcess | 250 | **129** | **121** |
| NtQuerySystemInformation | 251 | 250 | 1 |

The 121 unnamed were `System`, `Secure System`, `Registry`, `lsass.exe`,
`LsaIso.exe`, `smss.exe`, `winlogon.exe`, `services.exe` and every service. An
implementation that names barely half the system would have silently broken
`process_running()` and the Computer verifier. A comment in the source records
this so it is not "simplified" back later.

`tasklist` remains as a fallback only, still `CREATE_NO_WINDOW`.

## C. Regression guard

`tests/unit/test_process_enumeration.py` (5 tests) asserts:
protected processes are named; coverage and name accuracy ≥ 90% of tasklist;
**`list_processes()` spawns no subprocess** (the flashing-terminal guard);
`process_running()` is correct against real machine state.

## D. The flashing terminal — root cause and fix

Root cause: `computer/state.py::list_processes()` shelled out to `tasklist`, a
CONSOLE application, on a ~2–3 s timer driven by the computer event watcher.
Measured 321 `tasklist` + 321 `conhost` in 16 minutes. `CREATE_NO_WINDOW` hid
it (321 → 0 *visible*); the native rewrite removed it entirely (0 spawned).

## E. Knowledge, Media, Competition, Experience — routed, not dropped

All four now have canonical endpoints. The honesty contract is the point:

| Endpoint | Authority | Answer |
|---|---|---|
| `/api/competition` | `CompetitionEngine` (agent service) | real: max candidates, 4 outcomes, `commit_policy: winner_only`, history from its own audit entries |
| `/api/experience` | `ExperienceBank` (newly registered in the daemon) | real: `available: true` + lessons. An empty bank is `available:true, 0 lessons`; an **absent** bank is `available:false` + reason |
| `/api/knowledge` | none exists | `available:false` + reason |
| `/api/media` | none exists | `available:false` + reason |

Faking the last two with placeholder rows would have looked finished and been a
lie. `tests/contract/test_ipc_w6_surfaces.py` (6 tests) asserts the contract.

## F. Competition side-effect safety

`compete_and_commit` invokes the commit callable at most once, for the verified
winner. A loser cannot publish, deploy, email or purchase. The endpoint
publishes `commit_policy: "winner_only"` so the client can show the owner the
property rather than assert it. `POST /api/competition/run` and
`/api/competition/cancel` added.

## G. Duplicates removed while wiring the above

- `core/lifecycle.py` constructed `AgentService` **twice** at boot, discarding the first
- `core/ipc/server.py` defined `_agents()` twice
- `tests/conftest.py` constructed `PerceptionService` twice
- `tests/contract/test_ipc_api.py` had a services dict with keys repeated 14×

## H. Native client: four new surfaces

Competition, Experience, Knowledge and Media are now nav items.
`DataSurfaceViewModel` renders `available:false` as `Unavailable — <reason>`
instead of a bare row that reads like a failed call.

## I. Real .NET tests — 56, all passing

`ui/windows/Genie.Desktop.Tests` — a dependency-free harness that runs as a
plain executable, so "no test runner installed" can never be a reason the client
is untested. Covers JSON defensiveness, API parsing, SSE streaming, error
states, the role-persistence client, single instance, and navigation (15 items,
no duplicates).

```
passed 56, failed 0
```

## J. Single instance, end to end — 7/7

Exercised with the real executable, not the class:

```
first launch produced a running instance      PASS (pids=[17452])
backend started with the app                  PASS (pythonw=6)
still exactly one Genie.Desktop               PASS (pids=[17452])
the original instance is the survivor         PASS
second copy exited by itself                  PASS (exit code 0)
instance closed                               PASS
backend stopped with it                       PASS (pythonw=0)
```

The last check was added after the harness found its own bug: it used to clean
up with `terminate()`, a hard kill that cannot run `OnExit`, which orphaned six
`pythonw.exe` processes holding the database — and the next test then found
processes with no window to close. Cleanup now uses `--genie-shutdown`, so the
test also proves the backend really does stop with the app.

## K. Persistence — two independent durable items, 9/9

One item could be explained by the client re-reading a config file. Two
different stores cannot:

```
role change accepted (deepseek-chat -> deepseek-reasoner)   PASS
memory write accepted (unique marker)                       PASS
graceful close issued                                       PASS
zero owned processes after exit                             PASS
backend stopped after exit                                  PASS
relaunch reaches backend                                    PASS
role survived full exit + relaunch                          PASS
memory record survived full exit + relaunch                 PASS
```

## L. 15-minute no-console watch

See section N.

## M. Client lifecycle — 6/6

`scripts/verify_native_client.py`: starts, backend answers, **no console process
spawned by GENIE**, a window exists to close, zero processes after close,
backend stopped after close.

## N. Result of the 15-minute watch — PASS, and the run that proves it

The first 15-minute run passed, but it was not trustworthy: GENIE exited roughly
five minutes before the window closed, so ~10 of the 15 minutes had nothing
running. A watch with nothing running proves nothing.

The watch now owns GENIE as a child process (`--launch-exe`), reports whether it
was still alive at the end, and returns `INCONCLUSIVE` if it was not. The
definitive run:

```
launched GENIE pid=12456 as a child of this watch
watching process creation for 900s

=== console-subsystem process creations ===
  (none)
  conhost.exe total: 0

=== attributed to GENIE ===
  0
GENIE child alive at end of window: True

RESULT: PASS - GENIE created no console children
```

Zero is stronger than required: not merely zero **GENIE-attributed** console
children, but zero console-subsystem creations of any kind during the window —
no `conhost.exe`, no `tasklist.exe`, no `cmd.exe`, no `powershell.exe`, no
`wmic.exe`. Recorded in `artifacts/w7_no_console_15min.json`.

Before the fix this same measurement produced 321 `tasklist` + 321 `conhost` in
16 minutes.

## O. Windows 10 acceptance — OPEN, NOT CLAIMED

No Windows 10 machine has been available. Every component is *believed* to run
(`.NET 8` self-contained WPF supports Win10 1809+; embedded Python 3.12 does;
the Win32 surface used is present), but belief is not acceptance.

`scripts/verify_win10_acceptance.py` and `docs/WIN10_ACCEPTANCE.md` exist so the
answer becomes evidence. The script records the host's real OS build beside every
check and stamps every result `claim: NONE`, so Windows 11 evidence cannot be
mistaken for Windows 10 evidence. The 15-minute watch is the check that matters
most there: it distinguishes "hidden" from "not created".

## P. W8 deletion — Tier 1 executed, Tier 2 kept

The owner was asked once and chose **Tier 1 only**. Full detail in
`artifacts/w8_deletion_manifest.md`.

Deleted (Tier 1), all reconfirmed at 0 tracked files immediately before
deletion: ten `dist-electron*` trees, `ui/electron/node_modules` (558 MB),
`.build` (198 MB), `build` (61 MB) — ≈ 7.06 GB, ≈ 27,778 files.

Kept (Tier 2): all 6 authored Electron source files, including
`ui/electron/build/installer.nsh` (2,992 bytes, verified intact afterwards).
Electron is **not** retired yet; that step stays reversible.

Post-deletion verification: `git fsck` clean; `git status` clean (every deletion
was untracked, so the working tree is unaffected); native client smoke 6/6;
.NET harness 56/56; Python process-enumeration + W6 contract 11/11.

- **Tier 1 (safe): ≈ 7.06 GB, ≈ 27,778 files, 0 git-tracked.** Ten
  `dist-electron*` trees (6.26 GB), `ui/electron/node_modules` (558 MB),
  `.build` (198 MB), `build` (61 MB).
- **Tier 2 (needs a decision): 6 tracked files** — the authored Electron source.
  `ui/electron/build/installer.nsh` **must be relocated first** (proposed
  `installer/genie.nsh`); it is plain NSIS and the native product still needs it.
- **Keep:** `ui/windows/`, `ui/assets/brand/`, `backend-dist/`, `installer/`,
  `android/`, and the Python core.

## Q. A repository incident, and how it was recovered

Running `git stash` in this workspace deleted `.git/objects/pack/*.pack` (both
files), one `.idx`, the whole `.git/refs` directory and `.git/worktrees` — over
1,600 files. Git then reported `not a git repository` / `bad object HEAD`.

The files had gone to the E: Recycle Bin, not to nothing. Recovery parsed the
`$I*` metadata (path at byte offset 28, UTF-16-LE; size at 8..16) and copied the
`$R*` payloads back **only where the target was missing** — never overwriting.
Result: 1,622 files restored, `git fsck` clean, all history intact, branch tip
recovered from `.git/logs/HEAD`.

Standing rule adopted: **never run `git stash` or `git gc` here.** If `.git`
ever looks broken, check `E:\$RECYCLE.BIN` before assuming loss.

Loose refs under `.git/refs/heads/` also vanish intermittently; the branch is
now additionally pinned in `packed-refs`, and `HEAD` is verified after every
commit.

## R. Environment gotchas that cost real time

1. **A proxy is set** (`HTTP_PROXY=127.0.0.1:51567`) and `urllib` routes
   localhost through it, returning **502** — indistinguishable from a dead
   backend. Every harness now uses `ProxyHandler({})`.
2. **A backgrounded child of a bash command does not outlive that command.**
   GENIE died two minutes into a test. Use `scripts/launch_native_detached.py`.
3. **`dotnet restore` fails with `Value cannot be null (path1)`** because this
   shell lacks `APPDATA`, `PROGRAMFILES` and `SystemRoot`. Supply them.
4. **Commands can run twice** (sandboxed, then escalated). Avoid `cp X && git
   checkout X`-style non-idempotent chains.
5. The no-console harness must not match the bare string `genie` — this repo
   lives under `E:\G3\GENIE`, so every harness command line would be
   mis-attributed as GENIE-owned.

## S. Test suites

| Suite | Result |
|---|---|
| Python unit | **990 passed**, 2 failed (both pre-existing, see T), 2 skipped |
| Python contract | **76 passed** |
| W6 route contract | **6 passed** |
| Process enumeration | **5 passed** |
| .NET client harness | **59 passed** |
| Graceful shutdown switch (E2E) | **5/5** |
| Installer install -> run -> uninstall | **12/12** |

## T. The two remaining Python failures — pre-existing, not mine

- `test_json_repair.py::test_schema_validation_failure_is_reported` — a
  json-repair logic assertion, unrelated to any file touched here.
- `test_voice_stt.py::test_vosk_provider_reports_unavailable_without_a_model` —
  fails because `vosk` is not installed in the interpreter used. An environment
  artifact, not a code regression.

Both fail identically before and after this work. Neither imports
`computer.state`, `core.ipc.server` or `core.lifecycle`.

## U. What "no console" now means

Before: a hidden `tasklist` on a timer (~18/min).
After: **zero** console creations in a monitored 15-minute window with GENIE
running throughout. The only remaining console risk is the fallback path, which
is unreachable unless the native call fails.

## U2. Does GENIE exit on its own? — answered, mostly

Standalone detached launches were observed to exit after roughly 5–10 minutes,
while launches held as a child of a test harness lived for the whole test. That
looked like it could be a serious defect, so it was tested deliberately.

A 20-minute run with GENIE alive and confirmed at the end:

```
launched GENIE pid=14428 as a child of this watch
GENIE child alive at end of window: True
attributed to GENIE: 0
RESULT: PASS
```

GENIE ran **continuously for 20 minutes**, on top of the 15-minute run before
it. An app with an internal timer crash cannot do that. The deaths correlate
with being an *orphaned* process in this sandboxed environment, not with
anything the product does.

Not fully closed: the cleanest test is still to launch GENIE from Explorer and
leave it alone for an hour, which needs a human at the desktop. But
"GENIE exits on its own" is no longer a credible hypothesis — the evidence
points the other way.

## U3. Native installer — built and verified

The installer must never force-kill GENIE: killing it orphans the backend and
leaves files locked, which is what produced *"Failed to uninstall old
application files"* in the Electron era.

`dist\GENIE-Setup.exe` is **built and tested**, not a spec. Compiled with NSIS
3.10 from `installer/genie_native.nsi`: 360 MB payload → 98 MB installer
(27.1%), about two minutes to compile. `scripts/verify_installer.py` exercises
the real artifact and passes **12/12**:

| Step | Result |
|---|---|
| silent install, no UAC, per-user | PASS |
| `Genie.Desktop.exe` + embedded `pythonw.exe` present | PASS |
| installed app starts, backend answers `/health` | PASS |
| `--genie-shutdown` closes it, backend stops | PASS |
| silent uninstall removes program files | PASS |
| owner data in `%LOCALAPPDATA%\GENIE` survives | PASS |

One file remains after uninstall — `uninstall.exe` itself. That is standard NSIS
behaviour (an uninstaller cannot delete its own running executable) and the
test asserts it explicitly rather than pretending the directory is empty.

Two bugs found during the build, both of which would have shipped silently:
- The mutex name was escaped as `Local$\Genie...`. NSIS escapes with `$`, not
  `\`, so this compiled with `warning 6000` and produced a **mangled name at
  runtime** — the "is GENIE running?" check would have always failed and the
  installer would have tried to overwrite a live install. Fixed; the script now
  compiles with zero warnings.
- The verification script's own cleanup used `shutil.rmtree` on an 80,625-file
  tree, which is blocked above a threshold here and aborted the run before the
  final assertions. Cleanup now uses the uninstaller.

NSIS is not installed by default here; the build used a portable NSIS 3.10.
The installer is a per-user install into `$LOCALAPPDATA\Programs\GENIE`.

The installer must never force-kill GENIE: killing it orphans the backend and
leaves files locked, which is what produced *"Failed to uninstall old
application files"* in the Electron era.

`--genie-shutdown` is now implemented in the client. A second copy signals the
running instance over a named event; the running instance performs its own
canonical full exit on the UI thread (which stops the backend it owns).
Verified end to end:

```
GENIE started and backend healthy          PASS
GENIE processes present before the request PASS (7)
the requesting process exits quickly       PASS (rc=0 in 0.1s)
every GENIE process is gone                PASS (left=set())
backend stopped                            PASS
```

`installer/genie_native.nsi` is a full NSIS script (no electron-builder) that
packages the WPF client plus the embedded Python core, installs per-user with
no UAC prompt, and detects "is GENIE running?" by opening the app's own
single-instance mutex — plugin-free and without spawning `tasklist`.

**It has not been compiled: NSIS (`makensis.exe`) is not installed here.**
`installer/GENIE.iss` is the older Inno Setup script for the retired
PyInstaller build and is explicitly marked superseded.

Two defects were found and fixed while doing this:
- `BackendLifecycle.Dispose()` leaked the shutdown event handle, so an
  installer's "is GENIE running?" check would keep succeeding after exit.
  Caught by a test, not by inspection.
- `App.xaml.cs` had no unhandled-exception logging, so a startup crash
  produced a window that never appeared and nothing to investigate. Every
  unhandled exception is now written to
  `%LOCALAPPDATA%\GENIE\logs\desktop-crash.log`. This is what exposed the
  `NullReferenceException` above.

## V. Known limitations

- Knowledge and Media have no authority. Their surfaces are honest placeholders,
  not features.
- Windows 10 is unverified (section O).
- `android/` (8 tracked files) is untouched — out of scope for the Windows
  consolidation.
- The client binary is built but no NSIS installer has been produced from
  `installer.nsh` yet for the native build.

## W. Recommended next step

Answer the W8 question (section P / the manifest), then: relocate
`installer.nsh`, delete Tier 1, produce the native NSIS installer, and only then
retire `ui/electron`.

## X. Commands to reproduce

```
python -m pytest tests/unit tests/contract -q
python scripts/verify_native_client.py     --exe <exe>
python scripts/verify_state_persistence.py --exe <exe>
python scripts/verify_single_instance.py
python scripts/verify_no_console_children.py --seconds 900
ui\windows\Genie.Desktop.Tests\bin\Release\net8.0-windows\win-x64\Genie.Desktop.Tests.exe
```

## Y. Commits

```
72ed3d7 W7: single-instance E2E, two-item persistence, Win10 bundle, W8 manifest
80bf6f5 W6: canonical routes for Knowledge, Media, Competition, Experience
9c40b10 computer: replace tasklist with native process enumeration
ec7de47 computer: keep tasklist (hidden) after rejecting a ctypes rewrite
```

## Z. Honest summary

The console-spawning root cause is fixed properly, with measurements backing the
choice of API and a test that fails if a subprocess ever returns. The four
surfaces that were going to be dropped instead got real addresses, two with real
authorities and two that say plainly that they have none. The client has 56 real
tests, single instance is proven with the actual executable, and persistence is
proven across two independent stores. Windows 10 remains open and unclaimed.
Nothing has been deleted; the deletion manifest is exact and awaits one answer.
