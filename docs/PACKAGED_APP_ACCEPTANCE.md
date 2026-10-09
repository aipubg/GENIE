# GENIE — Packaged App Acceptance & GPU-Sandbox Decision Record

## Pinned artifact (rc13)

| Field | Value |
|---|---|
| file | `dist-electron/GENIE Setup 0.1.0.exe` |
| SHA-256 | `6d5f900098609f34651916369f01cedbffbe6106c28cb3702d0d91c1fd06d274` |
| size | 82,420,646 bytes |
| PE / NSIS | valid |
| git tag | `genie-v0.1.0-rc13` (commit `65c6b0a`) |
| manifest | `artifacts/release_installer_manifest_rc13.json` |
| built from | commit `65c6b0af201d4a83c0b826c92ad82eb73e02d390`; later commits are release metadata/docs only |

No claim of "same binary" is made without the hash. The previously pinned rc12
artifact (SHA-256 `3c7a4fbb…`, 82,420,694 bytes) and rc11
(SHA-256 `c40cae84…`, 82,088,453 bytes) are historical; see the
"Historical" section at the end.

## Current verified truth

| Item | Status |
|---|---|
| rc13 clean install (external acceptance root) | **PASS** — rc=0, `GENIE.exe` present, 3 shortcuts created |
| rc13 installed icon + uninstaller icon | **PASS** — both match the canonical GENIE icon |
| rc13 uninstall | **PASS** — files removed, 0 entries remaining |
| rc13 install over an existing install | **PASS** — rc=0; graceful shutdown requested **and** succeeded (2.2 s); forced cleanup **not** used; user data preserved |
| rc13 default launch, this machine | **BLOCKED** — exits `2147483651` (`0x80000003`), the GPU-process-sandbox failure |
| rc13 default launch, owner desktop | **PENDING ACCEPTANCE** — the decisive test |
| Historical agent-machine install / uninstall | **PASS** — older build, historical runs, kept for provenance |

### Resolved: "Failed to uninstall old application files : 2"

That message was **not** an installer defect. The acceptance harness installed
inside the source checkout (`artifacts/owner-acceptance`); a leftover harness
install there held a persistent sharing violation on `resources\app.asar`
(WinError 32), so every later installer run tried to uninstall that stale tree,
failed, and aborted. Copies of the same tree renamed fine both inside and
outside the repo, so it was that specific stale tree, not the location.

The harness now installs to `%LOCALAPPDATA%\GENIE-Acceptance\<unique-id>` and
reports pre-existing GENIE registrations before install. After that change the
installer lifecycle passes cleanly. Classification: **harness/environment
defect — product installer not blocked.**

**No sandbox-disabling flag is shipped.** None of `--no-sandbox`, `--disable-gpu-sandbox`
or `--in-process-gpu` is set as a default. Security must not be weakened to accommodate
an agent/non-console environment unless owner-machine evidence proves a mitigation is needed.

## Precise root cause (do not generalise)

Evidence supports exactly this chain, and nothing broader:

```
Chromium GPU process repeatedly crashes (exit_code=1)
        ↓
GPU process becomes unusable
        ↓
Chromium browser process aborts  →  exit 0x80000003 STATUS_BREAKPOINT
```

**Not** supported by evidence: "GPU missing", "GPU broken", "generic Electron sandbox
broken", "installer crash". The GPU is present and reports healthy (Intel UHD Graphics
770, `Status: OK`, driver `32.0.101.7088`). The failure is tied to **GPU-process sandbox
initialisation** in this environment.

## Owner desktop acceptance — run this first

```bat
python scripts\desktop_acceptance.py
```

The script verifies the SHA-256 **before anything else** and aborts on mismatch. Then, on a
normal interactive Windows desktop session, with **no compatibility flags on the first run**:

1. install (silent, unique directory)
2. installed EXE icon + shortcut identity
3. launch GENIE normally, no compatibility flags, observe ≥ 15 s — confirm
   `GENIE.exe` alive, a visible window, the daemon/backend connection, and that
   the window painted
4. backend payload scan for un-renderable values
5. single-instance check — launch GENIE a second time
6. install over the running app — require graceful shutdown requested **and**
   succeeded, and require forced cleanup **not** used
7. relaunch after upgrade and verify user data preserved
8. graceful shutdown of the relaunched app, and verify no owned GENIE processes
   remain
9. uninstall and verify `GENIE.exe` is removed

It writes `artifacts/desktop_acceptance.json` and `.md`.

The script does **not** currently cover the following, so a green run must not
be read as covering them:

- Tray "Exit GENIE" — shutdown is driven through the installer/shutdown-request
  path, not the tray menu item.
- User-data policy after uninstall — step 9 only asserts `GENIE.exe` is removed;
  it does not assert what happens to the data directory.

## Environment record (capture for the owner run)

| Field | Value |
|---|---|
| Windows version/build | `Windows 11 build 26200` (captured automatically) |
| session type | recorded (`session 1 (interactive desktop)` or `session 0 (non-interactive)`) |
| session id | recorded |
| GPU name | `Intel(R) UHD Graphics 770` |
| GPU driver version | `32.0.101.7088` |
| Electron version | declared `^33.0.0` |
| installer SHA-256 | captured by the acceptance script; expected rc13 hash `6d5f900098609f34651916369f01cedbffbe6106c28cb3702d0d91c1fd06d274` |
| GENIE commit/tag | recorded by the script |
| launch exit code | recorded |
| window appeared | recorded |
| daemon connection succeeded | recorded |

This distinguishes **normal-desktop success** from **machine-wide product incompatibility**.

## Decision rule after the owner-desktop test

### Case A — normal default launch passes

Classify as:

* installer install/uninstall — PASS
* packaged app, standard desktop — PASS
* current agent/non-console session — **ENVIRONMENT LIMITATION**
* **no GPU sandbox workaround shipped**

Status may then become:

> **FUNCTIONALLY RELEASE-READY, UNSIGNED DISTRIBUTION BUILD**
> **PRODUCTION SIGNING — OWNER-BLOCKED**

### Case B — normal desktop also fails

Only then reopen product mitigation. Evaluate **in this order**:

1. `--disable-gpu-sandbox` ← narrowest
2. `--in-process-gpu`
3. broader alternatives only if absolutely required

**Never jump to `--no-sandbox`.** Evidence already shows `--disable-gpu-sandbox` works, so
there is no justification for disabling all Chromium sandboxing. `--no-sandbox` stays
diagnostic-only.

## If a product mitigation is required — design a *bounded* compatibility path

Do not ship a compatibility flag unconditionally if the defect affects only specific
environments. Preferred architecture:

```
normal launch
    → default Chromium security model
only when an identified compatibility condition is present
    → documented fallback mode
```

Possible implementations to evaluate:

* explicit Settings / launch option — e.g. **GENIE GPU Compatibility Mode**
* bootstrap recovery after a PREVIOUS *verified* GPU-sandbox launch failure

Rules:

* Do **not** silently retry with weaker security during the same crash without recording it.
* The UI/status must **disclose** when compatibility mode is active.
* Before adopting a mitigation, document:
  - the security impact;
  - the affected Electron/Chromium process;
  - whether the renderer sandbox remains enabled;
  - GPU isolation impact;
  - performance / stability impact;
  - scope of machines requiring it.

## Diagnostic tooling (read-only, no product behaviour change)

```bat
python scripts\diagnose_app_launch.py
python scripts\diagnose_app_launch.py --flag --disable-gpu-sandbox
python scripts\diagnose_app_launch.py --exe "C:\path\GENIE.exe"
```

Captures: launch command, exit code, Electron logs, GPU-process crash lines, session info,
GPU/driver info. Auto-classifies **backend failure / renderer failure / GPU-process failure /
sandbox failure / installer failure** without guesswork. Writes `artifacts/launch_diag.json`
and `artifacts/launch_diag.log`.

## Out of scope for this decision

* Strix (Docker limitation) — optional external capability limitation, unrelated
* MiroFish (runtime limitation) — optional external capability limitation, unrelated
* Signing — separate axis. The current binary should run unsigned; do not conflate
  "unsigned" with "GPU sandbox failure".

## Historical agent-environment diagnosis

RC13 owner-desktop acceptance is pending. The evidence below describes the
agent environment used during development, not the current release state.

The installer had also started failing here, silently:

```text
GENIE Setup 0.1.0.exe /S /D=<path> /LOG=<file>
  -> rc=2, no output, no NSIS log file created at all

same binary, earlier in the same session:
GENIE Setup 0.1.0.exe /S /D=<path> /LOG=<file>
  -> rc=0, 77 files installed
```

**Earlier hypothesis — SUPERSEDED.** These symptoms were first read as "an NSIS
installer that produces no log never reaches NSIS initialisation, so the process
is blocked at creation". That interpretation was later **disproved**: the
installer *did* initialise and displayed a real dialog,
`Failed to uninstall old application files ... : 2`.

Actual resolved finding:

- a stale acceptance-harness install was registered inside the source checkout
  (`artifacts/owner-acceptance/...`), left by earlier harness runs;
- it held a persistent sharing violation on `resources\app.asar` (WinError 32);
- every later installer run detected that registration, tried to clean the
  stale install, failed, and aborted;
- the harness install root was moved outside the source checkout and the stale
  registration was removed (registry values backed up first);
- clean install, upgrade over an existing install and uninstall then all passed.

The `rc=2` / no-NSIS-log symptom was real, but it was the *consequence* of the
aborted stale-install cleanup — not evidence of blocked process creation.

Still true and unchanged: the packaged app crashes in the Chromium GPU-process
sandbox (`0x80000003`) on this machine.

Ruled out for the installer block specifically:

* TEMP writable — yes
* pending reboot — none (`PendingFileRenameOperations` empty, CBS `RebootPending` empty, no WU `RebootRequired`)
* Attachment Manager block — no `Zone.Identifier` alternate data stream present
* Defender / Controlled Folder Access — no GENIE events, no CFA blocks

**Consequence (historical, that agent session):** during that agent session,
acceptance became impossible on that machine. The owner's standard-desktop run
was the only way to close the question. That single test answers both questions
at once — whether stock GENIE launches, and whether unsigned execution is
permitted.

## Final refinement — why the GPU process dies (most precise statement)

Dev Electron (`electron .`) fails identically to the packaged app, so this is not a
packaging artefact. The dev log exposed the mechanism:

```
ERROR:disk_cache.cc(208)] Unable to create cache
ERROR:gpu_disk_cache.cc(713)] Gpu Cache Creation failed: -2
ERROR:cache_util_win.cc(20)] Unable to move the cache: Access is denied. (0x5)
ERROR:gpu_process_host.cc(982)] GPU process exited unexpectedly: exit_code=1   (x9)
FATAL:gpu_data_manager_impl_private.cc(423)] GPU process isn't usable. Goodbye.
```

Flag tests on dev Electron:

| Launch | Result |
|---|---|
| default | crash |
| `--disable-gpu` | crash |
| `--disable-gpu --disable-software-rasterizer` | crash |
| `--user-data-dir=<writable E: path>` | crash |
| `--user-data-dir=<writable>` + `--disk-cache-dir=<writable>` | crash |
| `--disable-gpu-sandbox` | **runs** |
| `--in-process-gpu` | **runs** |
| `--no-sandbox` | **runs** |

Writable cache locations did not resolve the failure. Modes that bypassed the failing
sandboxed GPU-process path allowed Electron to launch in that environment —
`--disable-gpu-sandbox`, `--in-process-gpu` and `--no-sandbox` all ran. It is therefore
not accurate to say that only removing the GPU sandbox lets it start: `--in-process-gpu`
also worked, by avoiding the separate GPU-process path.

The precise statement is:

> The Chromium GPU process, running inside its sandbox, is denied creation/access to its
> disk cache (`Access is denied`, 0x5). The GPU process exits immediately, repeatedly,
> and Chromium then aborts the browser process (`0x80000003`).

This is consistent with every observation in that environment: dev and packaged behave
the same, and writable cache locations do not help. Do not generalise beyond this
environment.

**It remains an environment finding, not a product verdict** — the same packaged app ran
successfully here earlier in the session (including a confirmed daemon connection), so this
machine's ability to host the sandboxed GPU process has changed. The owner-desktop run
remains the decisive test.

## Historical

- **rc11 artifact** — `dist-electron/GENIE Setup 0.1.0.exe`, SHA-256
  `c40cae84a9445745aa96933cdee351633de307545d38e57a9c25dbd9a1d8c26b`,
  82,088,453 bytes, built at commit `600c880`, manifest
  `artifacts/release_installer_manifest.json`. Preserved as
  `dist-electron/GENIE Setup 0.1.0-rc11.exe`. Its packaged `GENIE.exe` carried
  Electron's default icon; that defect is fixed in rc12.
