# GENIE — PHASE 14: HARDENING & DAILY-USE RELEASE

**Status: 🟨 IN PROGRESS** — 14.1 / 14.2 / 14.3 complete; installer packaging + real-machine  
acceptance outstanding.

Everything here exists so a bad day is survivable and reversible.

---

## 14.1 Backup & restore ✅

`core/backup.py` — `BackupService`.

Two rules drive it:

1. **Never copy a live database file.** GENIE runs SQLite in WAL mode; `shutil.copy` on an open WAL database can capture a torn state. Every snapshot goes through SQLite's **online backup API**, producing a transactionally consistent copy (tested with a connection deliberately left open mid-write).
2. **Verify before restoring.** Each backup records a sha256 per file; `restore()` re-checks them and **refuses** to apply a tampered archive. Restoring corrupt data over good data is worse than not restoring at all — and it is the failure mode that actually hurts.

A backup holds the database, the user config, and (by default, opt-out) the encrypted vault, which  
is labelled `contains_secrets` in the manifest. `list()` and `verify()` round it out.

## 14.2 Safe mode, offline mode & crash recovery ✅

`core/hardening.py`.

- **Safe mode** — boots with dangerous capabilities off so the owner can still talk to GENIE (memory, missions, chat, local inference) when the automation layer misbehaves. Denied: `input.`, `process.kill`, `application.open/close`, `system.volume`, mutating `files.*`, `clipboard.set`, `shell.`, `browser.`, `plugin.`, `devices.`
- **Offline mode** — probes reachability instead of inferring it from a failed request, then routes to local providers only. If there is no network *and* no local provider it reports `degraded` with an honest note rather than pretending to work.
- **Crash recovery** — `CrashRecovery` writes a start flag; if it survives to the next boot the shutdown was unclean. `recover()` purges stale locks, lists interrupted missions, optionally resumes them, and **reports everything it found**. It never hides work, and it never auto-resumes unless explicitly asked.

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
```

- `stage()` copies **and hashes** — nothing is trusted without a manifest.
- `activate()` records the previous version *before* switching, so rollback always has a target.
- `rollback()` restores the previous version, and **refuses** when there is nothing to go back to, when the previous release is missing from disk, or when it is corrupt.
- A corrupt release can never become active.

---

## Outstanding (honest status)

### Wired into the running system


| Item | Status | Where |
|---|---|---|
| Safe mode gate | ✅ wired | `ComputerService.execute()` consults `get_safe_mode()` before the PTE check; a blocked capability returns `safe.explain()` |
| `--safe` CLI flag | ✅ wired | `genie.py --safe <cmd>` enables safe mode for the process |
| Crash recovery on boot | ✅ wired | `Daemon.start()` → `CrashRecovery.recover(locks, missions)` then `mark_start()`; `Daemon.stop()` → `mark_clean()` |
| Backup / restore | ✅ usable | `core/backup.py`, not yet exposed via CLI — wiring a `genie.py backup` / `restore` subcommand is the next step |
| Offline mode wired into routing | ✅ wired | `Gateway.candidates()` drops remote providers when offline (local + mock remain). The probe is **cached** (30s) so requests don't each pay a TCP timeout |
| `genie.py backup` / `backup-list` / `backup-verify` / `backup-restore` | ✅ wired | full CLI surface over `BackupService` |

### Still outstanding


| Item | Status | Note |
|---|---|---|
| Installer (`GENIE-Setup.exe`) | ⬜ not started | needs InnoSetup/NSIS on a Windows build host; not something that can be meaningfully produced or verified from here |
| Real-machine acceptance (rollback, golden tasks) | ⬜ pending | the roadmap exit gate explicitly requires rollback tested on a **real machine**; it is currently verified deterministically only |
| `Updater` CLI | ✅ wired | `genie.py update-status` / `update-stage` / `update-activate` / `update-rollback` |
| **Wrong-action rate** | ✅ instrumented | `core/action_metrics.py` + migration `013_metrics`, wired into `ComputerService.execute()`, exposed at `GET /api/metrics/actions`. Target 2%. Refusals are deliberately **not** counted as wrong actions. *No real-usage baseline yet* |
| Release **fetching** | ✅ done | `core/release_fetch.py` + `genie.py update-fetch <url>` — **channel-agnostic**: point it at any URL serving a manifest. Avoids forcing a product decision while making updates possible. Verifies per-file sha256; accepts an optional signature and **refuses a signed manifest when no verifier is supplied** |
| Installer (`GENIE-Setup.exe`) | 🟨 spec only | `installer/GENIE.iss` + `installer/README.md` written and **explicitly labelled UNVERIFIED**; needs Inno Setup 6 on a Windows build host to compile and test |

### A deliberate security caveat on release fetching

Verifying each file's sha256 proves the download is **intact** (corruption/truncation), but it does  
**not** by itself prove the manifest is **authentic**, since both travel over the same channel.  
So:

- a **signed** manifest with **no verifier supplied is refused** — never trusted implicitly;
- a **bad signature** is refused;
- an **unsigned** manifest is allowed (the channel may well be trusted) but the result carries `signed: false` and an explicit warning that authenticity was not proven.

Silently accepting unsigned updates while implying they were verified would be the dangerous  
default, so the distinction is surfaced rather than hidden.

**Honest status:** 14.1–14.3 are all implemented, tested **and wired into the running system** —  
safe mode gates capabilities, crash recovery runs at boot, offline mode routes locally, backup and  
updates are reachable from the CLI. The roadmap exit gate is still **not met**: it requires all 18  
golden tasks green and rollback verified on real hardware, and rollback is currently only verified  
deterministically.

---

## Verification

```
GENIE — ROADMAP
Master spec §10 ka executable version. Har phase ka exit gate likha hai — bina gate pass kiye agla phase nahi.
Legend: 🟥 todo · 🟨 active · 🟩 done
Phase 0 — Foundation & governance  🟩
Done: master spec v1.0 (baseline), license matrix (verified from actual zips), architecture, contracts
(C1–C16), repo map, decision log, roadmap, reference docs.
Exit gate:
[x] LICENSE_MATRIX.md verified from real files
[x] Owner decisions D-001, D-022 … D-028 recorded
[x] Repo tree created (consolidated — D-029)
[ ] Owner sign-off on 🟠/🔴/⛔ license rows (owner-side, non-blocking)
Phase 1 — Brain skeleton (trusted)  🟩 COMPLETE
Kernel, event bus, NEDLE2 director, memory, missions, provider gateway, agent runtime, PTE + Vault + Audit.
Exit gate:
[x] Text loop end-to-end: "Chrome kholo" → director → mission → permission → capability → verify → audit
[x] Permission denied path tested (non-owner, missing grant)
[x] Audit entries per action + hash chain verifies
[x] Mission resumable (snapshot / resume / interrupted)
[x] Cancellation releases locks
[x] Provider failover + circuit breaker
[x] Custom provider/model added at runtime without source changes
[x] Offline/degraded mode (mock provider)
[x] UI talks only over HTTP + SSE
[x] Real Cactus Needle 2 runtime loaded and passing routing tests (owner gate): engine 2.0.4, sha256-verified, smoke 12/12, confidence gate 0.4, escalation on refusal
[x] 86 tests passing
Carry-over into Phase 2: live provider keys (D-036), NEDLE2 vocabulary growth as new commands appear.
Phase 2 — Computer engine  🟩 COMPLETE
Windows state (apps/windows/A11y), files, shell, desktop control locks, USER_TAKEOVER, browser provider.
Full detail: docs/PHASE2.md.
Exit gate — all verified on a real machine, non-dry-run:
[x] Open an arbitrary installed app — process + visible window verified
[x] Open a missing app — fails honestly ("not installed")
[x] Focus/minimize/maximize a window — verified against real window state
[x] Set and verify volume — Core Audio read-back
[x] Type exact text in Notepad — SendInput + UIA read-back
[x] Manipulate files — write/read/copy(hash)/move/delete verified
[x] Use the clipboard — set + read-back verified
[x] Execute a shell command and capture the result — destructive commands refused
[x] Semantically click a Windows UI control — UIA element found/invoked/focused
[x] Recover from a failed action — strategy ladder with back-off
[x] Navigate a browser — Chrome via CDP, render-verified
[x] Use browser DOM actions — click verified by navigation
[x] Cancel an active action and release locks
[x] Detect USER_TAKEOVER — lease released, automation paused
[x] Survive/recover without stale input locks — lease TTL + purge
[x] Audit the full action chain — hash chain + per-step verification rows
[x] Golden mission: manual find+move verified, YouTube channel content-verified, Blender step fails honestly
[x] 141 tests passing
Phase 3 — Voice  🟩 GREEN (PHYSICAL OWNER-MIC ACCEPTANCE TEST = PENDING)
Pipeline (capture → VAD → STT → turn manager → brain → TTS), barge-in, communication brain,
voice profiles, latency metrics. Full detail: docs/PHASE3.md.
Exit gate (all verified on a real machine):
[x] Real microphone capture + VAD (speech start/end)
[x] Speech → transcript → real GENIE turn → verified action
[x] Hinglish command works ("awaz 30")
[x] Response audio plays (real Windows SAPI through the real speaker)
[x] User can interrupt mid-sentence; playback stops in 66 ms
[x] Conversation resumes correctly after the interruption
[x] Simple commands still use the local fast path (no remote model)
[x] Voice does not bypass mission/trust/audit
[x] Degraded behaviour is honest when a provider is missing
[x] Per-stage latency metrics recorded
[x] Real STT engine added (offline Vosk) and verified on real audio: "volume 30" →
transcript "volume thirty" (confidence 1.0) → system.volume.set
[x] Audio preprocessing (DC removal, high-pass, bounded AGC) — real mics deliver quiet signals
[ ] PHYSICAL OWNER-MIC ACCEPTANCE TEST = PENDING — the owner runs
python genie.py voice-e2e when available (this machine's mic-to-speaker SNR is 1.0 dB,
see docs/VOICE.md §4). Development continues; this is not a blocker.
[x] 199 tests passing
Phase 4 — Plugins & browser maturity  🟩 COMPLETE
Plugin SDK, separate plugin host processes, two-gate permissions, first real plugins (media,
vscode), full browser maturity and the prompt-injection boundary. Detail: docs/PHASE4.md.
Exit gate — all 21 items verified on a real machine:
[x] plugin discovered / loaded in its own process
[x] permission denied by default; permitted invocation works; undeclared permissions refused
[x] plugin timeout handled; crash does not crash the daemon; restart works; disable + re-enable
[x] real media control via native Windows media keys
[x] browser: multiple tabs, verified switching, DOM click without raw mouse
[x] form typing/select/checkbox verified; SPA waits condition-based
[x] redirect verified; download verified + recorded; upload verified
[x] browser cancellation; two agents cannot share a session
[x] prompt-injection fixture cannot change the mission
[x] plugin and browser actions audited
[x] 242 tests passing
  Sync note (2026-09-18): this file had drifted — phases 5–12 were still marked 🟥 although
they were complete and documented in ACTIVE_WORK.md. Statuses below now match reality.
Phase 5 — Skills & teaching  🟩 COMPLETE
Skill lifecycle, teaching recorder, generalizer, sandbox validation.
Exit gate: golden #9 #10 — one demonstration generalizes to a new resolution. ✅
Phase 6 — Device mesh  🟩 COMPLETE
Kotlin Android node, second PC, networking layer (mTLS/relay/replay protection), sync v1.
Exit gate: golden #8 #17; encrypted pairing; offline queue with TTL. ✅
Phase 7 — Pi / IoT / home  🟩 COMPLETE
Exit gate: relay/sensor command idempotent + audited; home-automation bridge works. ✅
Phase 8 — Perception  🟩 COMPLETE
Screen/camera/audio perception, presence engine, fusion.
Exit gate: golden #12; no 24/7 cloud streaming; bedroom privacy default OFF. ✅
Phase 9 — Proactivity & companion  🟩 COMPLETE
Proactive scoring, notification system, floating companion.
Exit gate: golden #13; duplicate suppression + quiet hours verified. ✅
Phase 10 — Agent teams & cost control  🟩 COMPLETE
Agent factory, multi-agent teams, cost/quota controller.
Exit gate: golden #4 #5 within budget; mid-mission failover (golden #7) passes. ✅
Detail: docs/PHASE10.md, docs/PHASE10_GOLDENS.md.
Phase 11 — Specialist integrations  🟩 COMPLETE
n8n, media (ComfyUI remote worker), YT pipeline, Kronos, MiroFish — all isolated/external.
Exit gate: license boundaries verified (D-015…D-021); no 🟠/🔴 code imported. ✅
Detail: docs/PHASE11.md + docs/REPO_UTILIZATION_AUDIT.md (11A audit · 11B integrations ·
11C GENIE workspace · 11D operator console).
Phase 12 — Evolution & evaluation  🟩 COMPLETE
Improvement proposals, isolated branches, benchmark comparison, adoption; eval harness.
Exit gate: one real self-improvement cycle completes without touching live code. ✅
Detail: docs/PHASE12.md — 12.1 experience distillation (Youtu-Agent), 12.2 evaluation lab
(Qwen-AgentWorld), 12.3 governed self-evolution (enoch).
Phase 13 — UI polish + User Control Center  🟩 COMPLETE
Exit gate: owner can see "what do you remember / which agents run / which provider sees my data" in 3 clicks. ✅
Detail: docs/PHASE13.md — core/controlcenter.py, /ui/control (one click from the sidebar,
all three answers on one page).
Phase 14 — Hardening & daily-use release  🟨 IN PROGRESS
Offline mode, backup/restore, updater/rollback, crash recovery, safe mode, installer (GENIE-Setup.exe).
Exit gate: all 18 golden tasks green; wrong-action rate within target; rollback tested on a real machine.
Detail: docs/PHASE14.md.
[x] 14.1 backup & restore — core/backup.py; SQLite online-backup snapshots, sha256 manifest,
restore refuses a tampered archive
[x] 14.2 safe mode — core/hardening.py; enforced in ComputerService.execute(), --safe flag
[x] 14.2 crash recovery — unclean-shutdown detection + stale-lock purge wired into Daemon.start()
[x] 14.3 updater & rollback — core/updater.py; release directories, verified rollback
[x] 14.2 offline mode — Gateway.candidates() drops remote providers when offline; probe cached
[x] genie.py backup / backup-list / backup-verify / backup-restore
[x] Updater CLI — update-status / update-stage / update-activate / update-rollback
[x] release fetching — core/release_fetch.py + genie.py update-fetch <url>; channel-agnostic.
sha256-verified; signed manifests refused without a verifier; unsigned ones disclosed
[ ] installer (GENIE-Setup.exe) — installer/GENIE.iss spec written, unverified; needs
Inno Setup 6 on a Windows build host to compile and test
[x] golden #6 (memory episode retrieval) — tests/e2e/test_golden_6_memory.py (was uncovered)
[x] golden #14 (crash recovery) — tests/e2e/test_golden_14_18.py (was uncovered)
[x] golden #18 (offline) — tests/e2e/test_golden_14_18.py (was uncovered)
[x] wrong-action rate instrumented — core/action_metrics.py + /api/metrics/actions
(measurable now; no real-usage baseline yet)
[ ] exit gate — all 18 golden tasks green + rollback verified on a real machine
(per-task status: docs/GOLDEN_TASKS.md — 12/18 deterministic-green, #2 failing on the
real machine, #6/#8/#11 need hardware, rollback not yet verified on real hardware)
Cross-phase rules
1. Exit gate bina pass kiye agla phase start nahi.
2. Har phase ke end mein: CHANGELOG.md + प्रभावित modules ke docs update.
3. Golden task regression har release par (spec §8.10–8.11).
4. License check har naye integration ke waqt — retrospective nahi.
5. Phase 1–2 mein security add-on nahi (D-007).
6. "Complete" = executable + tested + verified. Files hone se complete nahi hota.
```
