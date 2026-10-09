# Final cleanup deletion manifest

## 2026-10-09 read-only revalidation

- E:/G3/GENIE/data/models/laya/weights (including pinned weight and tokenizer/config files) byte-matches %LOCALAPPDATA%/GENIE/models/laya/weights; the protected checkpoint copy also matches. The active loader uses the AppData model root. The existing workspace row remains only an owner-run candidate; no purge was performed.
- Workspace and protected-checkpoint Vosk model payloads byte-match the canonical AppData model (14 model files; AppData also carries its verification manifest). Existing workspace candidate remains owner-run only.
- Faster-Whisper .staging byte-matches the complete 12-file final model directory. It is outside this workspace-bounded purge script and was preserved as possible provisioning recovery state.
- AppData models, credentials, databases and browser profiles were preserved. RAR archive content was not inspectable with installed archive-list tools and is not listed as removable.
- Current source/runtime identity is 6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2; sync check passed for 263 packaged files. This section updates evidence only; it authorizes no new deletion.

Workspace: `E:\G3\GENIE`  
External checkpoint: `E:\G3\GENIE-preclean-checkpoint-20261008` (preserve; never touched by this manifest).  
Checkpoint evidence from the prior pass: 53,171 readable files / 6,172,714,055 bytes; hashes recorded in the checkpoint's `CHECKPOINT_SHA256.txt`. Fourteen cache/test directories were not enumerable to the PowerShell inventory and therefore remain protected as `MIGRATION_REQUIRED`.

Statuses below are the only states consumed by `scripts/OWNER_FINAL_WORKSPACE_PURGE.ps1`. Paths are exact relative paths. `READY_FOR_OWNER_PURGE` is still owner-confirmed, workspace-only removal. `KEEP_REQUIRED` and `MIGRATION_REQUIRED` are not included in the script's deletion list. This manifest records no deletion performed by the agent except the historical Inno script removal noted below.

## Current path-by-path disposition

| Exact path | Type / disposition evidence from fresh source-reference scan | Status |
|---|---|---|
| `installer/GENIE.iss` | Superseded installer source; absent. The current sole authority is `installer/genie_native.nsi`. | ALREADY_DELETED |
| `backend-dist/rc14-215848/` | Empty legacy runtime directory; fresh code and installer scan has no exact `rc14-215848` dependency. | READY_FOR_OWNER_PURGE |
| `browser-profile/` | Generated GENIE-owned Chromium state. BrowserService/CDP defaults now use `%LOCALAPPDATA%\GENIE\data\workspace\browser-profile`; explicit profile injection remains for tests. Never the owner's Brave profile. | READY_FOR_OWNER_PURGE |
| `browser-profile-2/` | Empty stale profile; no production/test caller names this suffixed profile. | READY_FOR_OWNER_PURGE |
| `browser-profile-3/` | Empty stale profile; no production/test caller names this suffixed profile. | READY_FOR_OWNER_PURGE |
| `browser-profile-abs/` | Generated debug profile; no runtime caller uses this directory. | READY_FOR_OWNER_PURGE |
| `ui/screenshots/` | Generated screenshots only. Sole helper creates the directory before writing; no product/installer asset reference. | READY_FOR_OWNER_PURGE |
| `ui/windows/Genie.Desktop/bin/` | Reproducible WPF build output; installer source consumes a fresh Release publish and clean-room instructions rebuild it. Not source authority. | READY_FOR_OWNER_PURGE |
| `ui/windows/Genie.Desktop/obj/` | Generated MSBuild intermediates include absolute workspace paths; regenerated from the project file. | READY_FOR_OWNER_PURGE |
| `ui/windows/Genie.Desktop.Tests/bin/` | Generated .NET test output; test project source remains. | READY_FOR_OWNER_PURGE |
| `ui/windows/Genie.Desktop.Tests/obj/` | Generated MSBuild intermediates; regenerated from the test project. | READY_FOR_OWNER_PURGE |
| `vendor/strix-audit/` | Ignored audit donor, but `scripts/probe_strix_deps.py` explicitly expects it. Keep source until that diagnostic is retired or migrated. `integrations/strix_runtime.py` is separate product source. | MIGRATION_REQUIRED |
| `capture_run.log` | Generated root log; no code/build/installer consumer. | READY_FOR_OWNER_PURGE |
| `daemon_run.log` | Generated root log; no code/build/installer consumer. | READY_FOR_OWNER_PURGE |
| `PHASE1_ACCEPTANCE.md` | Historical report; distinct durable repair lessons are retained in `docs/HISTORY_REPAIR_LOG.md`; no product/build/test consumer. | READY_FOR_OWNER_PURGE |
| `PHASE1_MULTISTEP_REPORT.md` | Historical report; durable lessons retained in `docs/HISTORY_REPAIR_LOG.md`; no product/build/test consumer. | READY_FOR_OWNER_PURGE |
| `OWNER_ACCEPTANCE.md` | Duplicate historic checklist; current checklist is `docs/OWNER_FINAL_ACCEPTANCE.md`; no product/build/test consumer. | READY_FOR_OWNER_PURGE |
| `desktop_awareness.json` | Root diagnostic snapshot; runtime uses `data_dir()/desktop_awareness.json`; no caller reads this root file. | READY_FOR_OWNER_PURGE |
| `config/user.json` | Local file replaced with `{}`; config now reads/writes `data_dir()/user.json`; packaging scripts exclude mutable `config/user.json`. | READY_FOR_OWNER_PURGE |
| `backend-dist/backend-runtime/app/config/user.json` | Legacy packaged copy replaced with `{}`; no packaged runtime reader uses bundled config as mutable config; future build/sync excludes it. | READY_FOR_OWNER_PURGE |
| `data/genie.db` | Private local DB; installed runtime uses canonical per-user data dir. Daemon must be stopped before purge. | READY_FOR_OWNER_PURGE |
| `data/genie.db-wal` | SQLite sidecar for private local DB; no code references this exact workspace file. | READY_FOR_OWNER_PURGE |
| `data/genie.db-shm` | SQLite sidecar for private local DB; no code references this exact workspace file. | READY_FOR_OWNER_PURGE |
| `data/plugin_dbg.db` | Local debug DB; runtime/provider code uses canonical per-user data dir. | READY_FOR_OWNER_PURGE |
| `data/plugin_dbg.db-wal` | SQLite sidecar; no code references this exact workspace file. | READY_FOR_OWNER_PURGE |
| `data/plugin_dbg.db-shm` | SQLite sidecar; no code references this exact workspace file. | READY_FOR_OWNER_PURGE |
| `data/providers.user.json` | Private local provider overrides; registry reads from `data_dir()`. No replacement copy is made because it can contain private provider metadata. | READY_FOR_OWNER_PURGE |
| `data/vault.enc` | Private encrypted local vault; default vault is under configured data dir. Never decrypt/read/copy it. | READY_FOR_OWNER_PURGE |
| `data/shot.bmp` | Generated local capture; no product or installer dependency. | READY_FOR_OWNER_PURGE |
| `data/win.bmp` | Generated local capture; no product or installer dependency. | READY_FOR_OWNER_PURGE |
| `data/desktop_buffer/` | Generated capture buffer; runtime recreates under its configured data dir. | READY_FOR_OWNER_PURGE |
| `data/workspace/browser-profile/` | Additional GENIE-owned generated Chromium profile found in the fresh recursive inventory. Runtime's installed workspace is external; this local profile is not the owner's Brave profile. | READY_FOR_OWNER_PURGE |
| `data/workspace/page.png` | Generated workspace screenshot; no product asset reference. | READY_FOR_OWNER_PURGE |
| `data/workspace/artifacts/capture_1789590073.bmp` | Generated desktop capture in local workspace artifacts; no product asset reference. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic2.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic3.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic4.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic5.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/acoustic6.wav` | Generated voice test audio; local test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/mic_capture.wav` | Local captured microphone audio; private/generated test artifact, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/pytest_stt.wav` | Generated STT test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/speech_1789615670322.wav` | Generated voice test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/stt_probe.wav` | Generated STT test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/tts_1789594376078.wav` | Generated TTS test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/tts_1789594377857.wav` | Generated TTS test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/voice/tts_1789594379609.wav` | Generated TTS test audio, not a packaged asset. | READY_FOR_OWNER_PURGE |
| `data/logs/` | Local runtime logs; packaged runtime writes to canonical per-user logs. Daemon must be stopped. | READY_FOR_OWNER_PURGE |
| `data/models/laya/` | External copy at `%LOCALAPPDATA%\GENIE\models\laya` passed pinned source/weight manifest and worker verification (`scripts/provision_laya.py --verify-only`); Laya remains shadow-only and sampling off. | READY_FOR_OWNER_PURGE |
| `data/models/vosk-model-small-en-us-0.15/` | Nonempty external copy exists under `%LOCALAPPDATA%\GENIE\models`; defaults and model tests now use canonical `core.paths.model_dir()`. | READY_FOR_OWNER_PURGE |
| `.build/` | Copied to `%LOCALAPPDATA%\GENIE\build`; external .NET SDK returns version 8.0.425. `build_backend_runtime.py` now supports `GENIE_BUILD_ROOT` and defaults to the external build root. | READY_FOR_OWNER_PURGE |
| `.workbuddy-aii/memory/` | Local agent/work memory, not imported by product; private notes are not part of runtime. | READY_FOR_OWNER_PURGE |
| `__pycache__/` | Root Python bytecode cache; safe to regenerate. | READY_FOR_OWNER_PURGE |
| `**/__pycache__/` | Approved generated-cache pattern, expanded by the owner script to exact directories only within listed source/test trees; `.build`, backend-dist, data/models and vendor are excluded. | READY_FOR_OWNER_PURGE |
| `.pytest_cache/` | Pytest cache tree has unreadable subdirectories in this execution context; preserve until fully enumerable. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp2/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp3/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp4/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp5/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp6/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp7/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp8/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmpC/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmpD/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmpE/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp_full/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `artifacts/.pytest_tmp_full2/` | Temporary pytest tree was not enumerable due access denial. | MIGRATION_REQUIRED |
| `config/user.example.json` | Safe sample defaults. Required documentation/template input. | KEEP_REQUIRED |
| `config/providers.json` | Shipped provider templates. | KEEP_REQUIRED |
| `data/personas/` | Tracked product persona corpus required by runtime/tests. | KEEP_REQUIRED |
| `data/workspace/` | May hold developer-created workspace content; runtime also uses the configured workspace. Contents/ownership are not fully classified. | MIGRATION_REQUIRED |
| `data/voice/` | May contain voice setup/capture state; ownership and contents are not fully classified. | MIGRATION_REQUIRED |
| `data/plugins/` | Local plugin state not fully classified. | MIGRATION_REQUIRED |
| `data/baseline-probe/` | Evaluation/probe outputs not fully classified. | MIGRATION_REQUIRED |
| `data/desktop_awareness.json` | Runtime settings/state path; retained until owner data migration is confirmed. | MIGRATION_REQUIRED |
| `data/voice_setup.json` | Local voice settings; retained until owner data migration is confirmed. | MIGRATION_REQUIRED |
| `data/running.flag` | Runtime coordination marker; retained pending complete live-process/ownership audit. | MIGRATION_REQUIRED |
| `backend-dist/backend-runtime/` | Current packaged Python runtime needed for the next clean-room build and current application identity. | KEEP_REQUIRED |
| `backend-dist/` | Contains the current runtime and outputs; only the exact empty `rc14-215848/` path above is removable. | KEEP_REQUIRED |
| `dist/GENIE-Setup.exe` | Existing installer preserved as an input/reference until a new clean-room installer is built. Not a release acceptance result. | KEEP_REQUIRED |
| `installer/genie_native.nsi` | Current native NSIS installer source. | KEEP_REQUIRED |
| `vendor/prime_rlm/` | Active integration source imported by GENIE. | KEEP_REQUIRED |
| `ui/windows/Genie.Desktop/` | Current WPF source. | KEEP_REQUIRED |
| `tests/` | Active automated tests and fixtures. | KEEP_REQUIRED |
| `E:\G3\GENIE-preclean-checkpoint-20261008/` | External recovery checkpoint, outside this script's workspace boundary. | KEEP_REQUIRED |

## Private-data and secret review

The initial workspace scan found local database files/sidecars, an encrypted
vault, provider overrides, browser profiles, screen captures, logs and agent
memory. The vault and credential values were not opened, decrypted, copied or
printed. These workspace-contained items are individually listed above where
known. External `%LOCALAPPDATA%\GENIE` owner data and personal browser profiles
are outside the purge boundary and are never targets. The post-purge read-only
verifier checks likely credential literals in product source and suppresses
matched values. This does not inspect the contents of unreadable pytest trees;
those remain `MIGRATION_REQUIRED`.

## Fresh source-reference scan notes

The current code scan found only runtime destinations or test fixtures for
private DB/vault/profile names. `desktop_awareness.py` uses the configured data
root. `ModelRegistry` uses `core.paths.data_dir()`. Browser defaults now use the
per-user workspace profile. UI screenshot output now creates its generated
folder. The audit-donor path remains referenced by `scripts/probe_strix_deps.py`
and is intentionally not purged. No package or installer needs `.build`, local
models, local profiles, workspace databases, captures or root logs.

The owner script is deliberately limited to rows marked `READY_FOR_OWNER_PURGE`.
The purge has not been run. `docs/FINAL_WORKSPACE_AUDIT.md` must stay incomplete
until owner purge, clean-room build, full applicable tests, installer install /
uninstall checks and the post-purge verifier all succeed.
