# GENIE — PHASE 2: COMPUTER ENGINE

**Status:** 🟩 COMPLETE — exit gate passed on a real Windows machine (141 tests passing)

> **The rule this phase exists to enforce:**
> an action is never `COMPLETED` because GENIE produced the right call.
> `COMPLETED` means **real action executed + resulting Windows state observed + expected result verified**.

---

## 1. The execution loop

```text
PLAN     choose ordered strategies (native API -> OS -> UIA -> DOM -> vision -> raw input)
ACT      run the chosen strategy
OBSERVE  take a fresh machine-state snapshot
VERIFY   compare the resulting state with the expected effect
RECOVER  on mismatch: record evidence, back off, try the next strategy, else fail honestly
```

Never `PLAN → ACT → ASSUME SUCCESS`.

| Module | Responsibility |
|---|---|
| `computer/planner.py` | strategy chains per capability + availability (which layers exist on this machine) |
| `computer/executor.py` | the loop, desktop lock, takeover checks, cancellation, per-attempt verification rows |
| `computer/verifier.py` | 33 registered verifiers + read-only set; **unknown capability ⇒ never verified** |
| `computer/service.py` | PTE gate + audit + events, then hands off to the executor |
| `computer/state.py` | ComputerState: foreground, windows, monitors, processes, audio, clipboard, RAM/CPU |
| `computer/windows_api.py` | Win32 via ctypes: windows, monitors, SendInput, clipboard, GDI capture, idle detection |
| `computer/apps.py` | installed-app discovery + launch-method cache |
| `computer/audio.py` | Core Audio (IAudioEndpointVolume) — real volume **read**, set, mute |
| `computer/uia.py` | UI Automation (pywinauto, lazily imported) — semantic element targeting |
| `computer/files.py` | workspace-first filesystem with atomic writes + recycle-bin delete |
| `computer/shell.py` | hardened PowerShell/CMD with allowlist, forbidden-pattern policy, timeouts |
| `computer/locks.py` | exclusive desktop lease + USER_TAKEOVER handling |
| `computer/workspace.py` | GENIE workspace, isolation detection, resource profile |
| `computer/events.py` | polling event sources → bus (app opened/closed, window focus, file change, takeover) |
| `browser/cdp.py` | stdlib WebSocket + Chrome DevTools Protocol client |
| `browser/service.py` | BrowserProvider: navigate / DOM query / DOM actions / extract / screenshot |

## 2. Automation priority (enforced, not aspirational)

```text
native app/plugin API → Windows/system API → UI Automation → browser DOM → vision → raw mouse/keyboard
```

Examples from the real strategy tables:
- `application.open`: `native-launch` (10) → `shell-start` (30) → `uia-startmenu` (60) → `raw-search` (90)
- `application.close`: `window-close` (WM_CLOSE) → `taskkill`
- `files.delete`: `recycle-bin` → `hard-delete` (needs grant)
- `uia.invoke`: `uia-invoke` → `rect-click` (last resort)
- `input.type_text`: `sendinput-unicode` → `clipboard-paste`

Raw coordinates are only reachable when every higher layer is unavailable or has failed.

## 3. Capabilities (65)

`application.*` (open/close/list/resolve, `apps.discover`) · `window.*` (list/focus/minimize/maximize/restore/move/close) ·
`processes.list`, `process.kill` · `system.volume.*` (set/up/down/mute), `system.audio.state` ·
`files.*` (read/write/append/delete/move/copy/mkdir/list/find/exists/disk_usage) ·
`clipboard.get/set` · `shell.run`, `shell.powershell_json` · `uia.*` (windows/find/tree/get_value/invoke/click/set_value/focus) ·
`input.*` (type_text/key/hotkey/click/move/scroll/state) · `screen.capture`, `screen.capture_window`, `screen.state` ·
`computer.state` · `workspace.*` · `desktop.lock_state`, `desktop.resume` ·
`browser.*` (navigate/dom_query/click/type/extract/screenshot/tabs)

## 4. Installed-application discovery (no hardcoded app list)

Three real sources, merged and cached for 5 minutes:

```text
1. Registry App Paths      HKLM/HKCU ...\CurrentVersion\App Paths\*.exe
2. Start Menu shortcuts    *.lnk (launched through the shell)
3. Program Files roots     Program Files, Program Files (x86), %LOCALAPPDATA%\Programs
+ PATH and %SystemRoot%\System32 for system executables (explorer, calc, notepad, cmd)
```

Measured on this machine: **194 applications discovered in 0.01 s**; `chrome`, `notepad`, `explorer`,
`calc`, `mspaint`, `vscode`, `cmd` all resolve to real executables.

Launch methods are cached in `app_launch_cache` with success/failure counts, so a working method is
reused and a failing one is demoted. If an app is not installed, the plan is rejected up front and the
mission fails with `application 'X' is not installed` — never a fake success.

## 5. USER_TAKEOVER

```text
GENIE holds the desktop lease
  -> user moves the mouse or types
  -> GetLastInputInfo shows input we did not send
  -> lease released immediately, conflicting automation paused
  -> USER_TAKEOVER event on the bus, audit entry written
```

GENIE never fights the human for the pointer. Automation resumes only when explicitly resumed
(`desktop.resume`), and stale leases expire on their own (lease TTL) so a crash can never leave a
permanently locked desktop.

## 6. Isolation

```text
GENIE Workspace (safe to break)          User Desktop (protected)
coding builds downloads research          personal apps, games, projects
agents temp artifacts
```

- Workspace-relative paths are always resolved inside the workspace; escaping it raises.
- Writes are atomic (temp file + `os.replace`), so a crash never leaves a half-written file.
- Deletes prefer the Recycle Bin; hard delete is a separate strategy.
- Isolation options are detected **passively** (no `wsl.exe`/`dism` execution — host security policy
  must never be able to break GENIE boot): Hyper-V → Windows Sandbox → WSL2 → local workspace.

## 7. Browser (DOM first, coordinates never)

Built-in CDP provider over a stdlib WebSocket client — no Node, no puppeteer, no extra Python package.

```text
navigate   Page.navigate + wait for readyState AND rendered content (SPAs report 'complete' early)
dom_query  CSS selector or semantic text match
click      DOM click by selector or by visible text, verified by navigation/state change
type       value + input/change events, optional submit
extract    structured text/links/inputs
tabs       /json/list      screenshot  Page.captureScreenshot
```

Measured: `https://example.com` navigate + render verified; DOM click on "Learn more" verified by the
resulting navigation to `iana.org`; `youtube.com/@JoshuaBardwell` navigate verified with real channel
content extracted ("Joshua Bardwell", 439K subscribers).

PinchTab (supplied repo, MIT) remains an **optional external provider** behind the same
`BrowserProvider` contract; it was not embedded because it would add a Bun/Node runtime to a daemon
whose purpose is being light on low-end Windows machines (D-040).

## 8. Performance (measured, `python tools/benchmark.py`)

| Metric | Value |
|---|---|
| NEDLE2 cold (engine init + first decision) | ~1 490 ms |
| NEDLE2 warm avg / p50 / max | **628 / 632 / 788 ms** |
| `system.volume.set` end-to-end | ~131 ms (verification ~125 ms) |
| `clipboard.set` end-to-end | ~128 ms (verification ~124 ms) |
| `application.open` end-to-end | ~1 913 ms (verification ~578 ms, waits for the real window) |
| `application.close` end-to-end | ~1 371 ms (verification ~567 ms) |
| Process working set | ~26 MB idle → ~101 MB with window/process snapshots |

No premature optimization: routing quality came first (see §10).

## 9. Exit gate — real machine, non-dry-run

| Requirement | Result |
|---|---|
| open an arbitrary installed app | ✅ Notepad: process + visible window verified (`native-launch`) |
| open a missing app | ✅ fails honestly: "application 'blender' is not installed" |
| focus / minimize / maximize a window | ✅ verified against the real window state |
| set and verify volume | ✅ Core Audio read-back: requested 27 → actual 27, restored afterwards |
| type exact text in Notepad | ✅ typed via SendInput, read back through UI Automation |
| manipulate files | ✅ write/read/copy(hash-match)/move/delete all verified |
| use the clipboard | ✅ set + read-back verified |
| execute PowerShell and capture result | ✅ exit code + stdout captured; destructive commands refused |
| semantically click a Windows UI control | ✅ UIA element found, invoked, focused |
| recover from a failed action | ✅ strategy fallback ladder (verified in `application.close`, `input.type_text`) |
| navigate a browser | ✅ Chrome via CDP, render-verified |
| use browser DOM actions | ✅ click verified by navigation; extract verified by content |
| cancel an active action and release locks | ✅ cancelled result + lease released |
| detect USER_TAKEOVER | ✅ simulated external input released the lease and paused automation |
| survive without stale input locks | ✅ lease TTL + `purge_stale` recovered a dead holder |
| audit the full action chain | ✅ hash chain verifies; per-step verification rows persisted |

Golden mission (owner-specified): the manual-search + move steps verify; the YouTube channel opens and
is content-verified; the Blender step **fails honestly** because Blender is not installed on this
machine — and the mission is recorded as FAILED with the reason, not as success.

## 10. Honest caveats

- **Router catalogue size is a quality lever.** 13 tools → smoke 12/12. 20 tools → 9–11/12.
  The routable catalogue is therefore kept at 14 focused tools; the other Phase 2 capabilities
  (window focus/minimize, clipboard copy, file search/move, website) remain fully available as
  capabilities and are reached through a mission / remote model instead of the local router (D-042).
- A web address is resolved by a **deterministic fast path** (`fast-path:web-address`), because the
  model cannot reliably separate "open youtube.com/x" from "open notepad" (A-035).
- `input.*` verification confirms **delivery to the OS**; the *effect* must be verified by an
  observable follow-up (window state, UIA value, clipboard) — as the typing test does.
- UI Automation depends on the optional `pywinauto` package (lazily imported). Without it, semantic
  targeting degrades to keyboard/accelerators and raw input; the core daemon stays dependency-free.
- `SHFileOperationW` can report a non-zero code while having actually recycled the item, so delete
  success is decided by **state**, not by the return code (A-033).
- Polling-based event sources (2 s) instead of WinEventHook/ReadDirectoryChangesW in v1 (A-030).
