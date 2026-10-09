# Windows-Native Migration Audit (PHASE W0)

**Date:** 2026-09-20 · **Status language:** PRESERVED / MIGRATED / FIXED /
DEDUPLICATED / NEW / VERIFIED / PARTIAL / BLOCKED / REMOVED

Read-only audit. No production code changed by this document.

---

## A. CURRENT HEAD / BRANCH

| | |
|---|---|
| Branch | `upgrade/genie-continuity-ui` |
| HEAD | `a8e19370dd257e984a7018c81926c0eacca0b1c4` |
| Last commits | `a8e1937` manifest · `1fc1e39` uninstaller cleanliness · `05ef566` e2e full exit |
| Current tagged release | `genie-v0.1.0-rc14` (untouched) |
| Current candidate | **rc15c** — Electron. Under §39 this is now **rollback evidence only** |

## B. CURRENT CANONICAL BACKEND

Single authoritative Python backend. Entry `backend_entry.py`, HTTP/SSE server in
`core/ipc/server.py` on `127.0.0.1:8787`. Domain packages present and mature:

`core` · `director` (NEDLE2) · `memory` · `context` · `missions` · `agents` ·
`models` (provider gateway) · `experience` · `evaluation` · `computer` ·
`browser` · `skills` · `teaching` · `devices` · `security` · `forecast` ·
`perception` · `voice` · `integrations` · `vendor`

Embedded runtime: Python 3.12.6 (embeddable), launched as **`pythonw.exe`**.

**Verdict: PRESERVED** — the migration touches only the desktop shell.

## C. ELECTRON DEPENDENCIES

| Item | Where | Status |
|---|---|---|
| Main / preload / backend bootstrap | `ui/electron/main.js`, `preload.js`, `backend.js` | REMOVE after W7 |
| Electron + electron-builder | `ui/electron/package.json` devDeps | REMOVE after W7 |
| `node_modules` | `ui/electron/node_modules` — **558 MB** | DELETE (generated) |
| NSIS script | `ui/electron/build/installer.nsh` | REUSE for the native installer (§34) |
| Electron bootstrap test | `ui/tests/electron/daemon-bootstrap.test.js` | RETIRE at W8 |
| Scripts referencing Electron | `scripts/acceptance_installer.py`, `build_backend.py`, `desktop_acceptance.py`, `diagnose_app_launch.py`, `verify_electron_launch.py`, `verify_full_exit_e2e.py`, `p0_case_matrix.py`, `p0_registry.py` | REPOINT to native client |
| Test | `tests/e2e/test_install_while_running.py` | REPOINT |
| Docs | many | UPDATE |

Note: `installer.nsh` is plain NSIS, **not** Electron-specific. It is the natural
way to satisfy §34 (keep NSIS, drop electron-builder).

## D. WEB-UI DEPENDENCIES

* `ui/web` — 33 files, 296 KB. Served by `core/ipc/server.py` routes
  `/ui`, `/ui/ops`, `/ui/control`, `/ui/…`.
* Serving code: `core/ipc/server.py` lines ~184–237 (`bundled_ui_dir()`).
* Status: **legacy**. Behavioural reference during migration only (§4). To be
  classified diagnostics-only or removed after native parity.

## E. CURRENT DUPLICATES

Searched for duplicate services, registries, planners, routes, wrappers.

**No true duplicate implementations found.** Named-similar modules are distinct
domains, not copies:

* `computer/planner.py` (Computer PLAN/ACT/OBSERVE/VERIFY) ≠
  `missions/planner.py` (mission DAG planner) — different concerns, keep both.
* `devices/registry.py`, `models/registry.py`, `plugins/registry.py`,
  `skills/registry.py` — one registry per domain, each the canonical owner.
* `core/orchestrator.py`, `voice/turn_manager.py` — single instances.

**Real duplication is generated output, not code:** ~5.9 GB of
`dist-electron-*` trees, plus `backend-dist` (204 MB) and `.build` (198 MB).
Repo working size **9.3 GB**.

Also **newly identified by this audit:** `artifacts/.pytest_tmp*` scratch trees
left by full-suite runs. These are mine and are pure junk.

## F. WINDOWS NATIVE STACK SELECTED

**WPF on .NET 8, MVVM, self-contained x64** — as directed.

WinUI 3 was considered and **not** chosen: it needs a packaged MSIX/AppContainer
identity or WindowsAppSDK bootstrap for the installer story we must preserve
(per-user NSIS install, Start Menu shortcut, clean upgrade). WPF ships in the
.NET Windows Desktop runtime and produces a plain EXE NSIS can install and
upgrade. No material advantage found for WinUI 3 here.

**BLOCKER found (environment, not architecture):**

```
dotnet --list-sdks   -> No SDKs were found.
dotnet --list-runtimes ->
    Microsoft.NETCore.App 3.1.32
    Microsoft.NETCore.App 9.0.3
    Microsoft.WindowsDesktop.App 9.0.3   (WPF runtime present)
```

The WPF **runtime** exists but there is **no SDK**, so nothing can be compiled
yet. Network reachability confirmed (`dotnetcli` 200, `nuget` 200).

**Resolution:** install .NET SDK 8 as a machine-local extraction (SDK zip, no
system installer, no admin). This is required before W1 can produce a build.

## G. BACKEND API CONTRACT REUSE

**Reuse as-is. Do not invent a second protocol.** `core/ipc/server.py` exposes
**120 route branches**, including:

* health `/health`
* chat `/api/chat`, `/api/chat/stream` (SSE)
* events `/api/events` (SSE)
* missions `/api/missions`, `/missions/create`, `…/cancel`
* agents `/api/agents`, `/api/agents/teams`
* providers `/api/providers`, `/test`, `/update`, `/remove`, `/models/remove`
* model roles `/api/models/roles`
* memory, skills, teaching, devices, perception, forecast, security, isolation,
  plugins, voice, ops, control-center, proactive, metrics

The WPF client will use `HttpClient` for request/response and SSE streaming over
these exact contracts. **No Windows-native IPC unless a concrete gap appears.**

## H. LIFECYCLE DESIGN

One authority, owned by the native client (replacing `ui/electron/backend.js`):

1. Launch `pythonw.exe` only. Never `python.exe`, `cmd.exe` or a shell.
2. `CreateProcess` with `CREATE_NO_WINDOW` + hidden background; **no console**.
3. Record the exact backend PID; verify ownership before terminating (the
   `isOurDaemon` rule from rc15c carries over — refuse by default).
4. Health probe on `/health` with bounded startup.
5. Stale-PID protection and duplicate-launch protection (spawn lock + Mutex).
6. Graceful stop on full Exit: stop owned daemon + owned plugin processes.
7. Single instance via Windows named Mutex; second launch activates the window.

Requirement satisfied: **never kill arbitrary `pythonw.exe` by name.**

## I. FLASHING-TERMINAL CURRENT EVIDENCE

Carried forward, unchanged:

* Idle 4 min (no commands): **10** process creations, **1** console host.
* Active 35 min (tooling running): **2,778** creations, **1,655** console hosts
  (~47/min) — dominated by `conhost.exe <- tasklist.exe` (**521**), produced by
  harness polling, since `tasklist` is a console application.
* GENIE itself creates **no** console host.

**Status: BLOCKED** as a definitive attribution of the owner's window. Leading
hypothesis: console hosts created by tooling. W8/W9 must add the clean idle test
(§18, §41): native GENIE running 15 minutes with tooling stopped, capturing
process creation, expecting **zero** console windows.

The harness fix already landed: `process_alive()` enumerates in-process via
ctypes instead of shelling `tasklist`.

## J. DIRECTORY CLEANUP PLAN

Do not delete evidence first. Preserve: source, owner data, manifests,
**one** known-good rollback installer, and the current candidate.

| Target | Size | Disposition |
|---|---|---|
| `dist-electron-rc12g`, `-rc13g` | 852 MB | DELETE (superseded) |
| `dist-electron-rc14`, `-rc14b/c/d` | 2.86 GB | DELETE after one rollback copy kept |
| `dist-electron-rc15a`, `-rc15b` | 1.48 GB | DELETE — superseded by rc15c |
| `dist-electron-rc15c` | 738 MB | KEEP as Electron rollback evidence (§39) |
| `dist-electron` | 483 MB | DELETE (stale default output) |
| `.build` | 198 MB | DELETE after rebuild (regenerable cache) |
| `backend-dist` | 204 MB | KEEP (packaging source for the runtime) |
| `ui/electron/node_modules` | 558 MB | DELETE at W8 |
| `artifacts/.pytest_tmp*` | junk | DELETE now (mine, regenerable) |

Classification before deletion, per §33.

## K. FEATURES TO PRESERVE

All **PRESERVED** (no rewrite under §42): active mission survives context
trimming · provider-independent continuation · no-key local endpoints · provider
failure classification · planner→DAG→execution · saved agent profile restore ·
experience restore · canonical API transport · backend-authoritative provider
status · model roles · vault credential storage · custom provider support ·
runtime path authority · plugin-host script-path fix · persisted plugin circuit
breaker · plugin stderr drain · real NEDLE2 packaging · packaged embedded Python
· updater traversal protection · competition engine · isolation model ·
Computer/browser stack · voice · perception · device trust · donor integrations.

## L. LEGACY FILES TO REMOVE AFTER PARITY

At W8 only — **not before W7 parity is proven**:

* `ui/electron/` (main.js, preload.js, backend.js, package.json, node_modules)
* `ui/web/` — remove or reclassify diagnostics-only
* `ui/tests/electron/` and Electron-specific acceptance paths
* Electron/Chromium-specific workarounds and GPU troubleshooting
* `electron-builder` from the build path (NSIS stays, directly)
* Superseded `dist-electron-*` trees per §J

## M. WINDOWS 10 / 11 TEST PLAN

| | |
|---|---|
| This machine | Windows 11 **10.0.26200**, x64 — testable now |
| Windows 10 x64 | **not present here** — must be sourced (VM or second device) |

Required on **both** (§36): clean install · first launch · backend auto-start ·
no console flash · Home · Chat · mission · Settings/provider config · voice
available/unavailable · full Exit · relaunch · upgrade · uninstall · data
preservation.

**BLOCKED:** Windows 10 cannot be verified on this machine. Per §36, Windows 10
support must **not** be claimed from Windows 11 results alone. Options: Hyper-V/
VirtualBox Windows 10 VM, or a physical Windows 10 device. Needs owner input.

Terminal-flash gate (§41): with WorkBuddy and all tooling stopped, native GENIE
running 15 minutes, process creation captured, expecting zero console windows and
zero uncontrolled child respawn.

---

## Immediate next actions (W1)

1. Install .NET SDK 8 by local zip extraction (no system installer).
2. Create `ui/windows/Genie.Desktop` (net8.0-windows, `UseWPF`, MVVM).
3. Prove it builds and runs before writing feature code.
4. Repoint the backend lifecycle into the native client.

**Stop conditions:** owner data risk · destructive action beyond the approved
Electron/web removal · credentials · irreversible external action · Windows 10
hardware unavailable.
