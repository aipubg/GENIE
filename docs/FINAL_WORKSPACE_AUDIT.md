# Final workspace audit — owner purge pending

## 2026-10-09 reconciliation update

Read-only identified-path counts, model SHA-256 records, duplicate groups, unknown archive/worktree dispositions and functional-repair evidence are recorded in:

- docs/GENIE_COMPLETE_FILE_AND_MODEL_INVENTORY.md
- docs/GENIE_EXACT_DUPLICATES_AND_CANONICAL_PATHS.md
- docs/GENIE_BROKEN_EXECUTION_CONNECTIONS.md
- docs/GENIE_OWNER_WORKFLOW_REPAIR_RESULTS.md
- docs/GENIE_FINAL_CLEANUP_AND_RELEASE_GATES.md

No cleanup was run. The external recovery checkpoint remains read-only and protected. The external RAR collection and stale/locked worktrees remain unclassified at content level. The current checkout and its pre-existing changes were preserved.

**Date:** 2026-10-08  
**Workspace:** `E:\G3\GENIE`  
**External recovery checkpoint:** `E:\G3\GENIE-preclean-checkpoint-20261008`  
**Current acceptance state:** NOT SELL-READY; owner purge and post-purge clean-room gates remain.

## Completed non-deletion preparation

- Canonical model store is `%LOCALAPPDATA%\GENIE\models`. Laya was copied out of the checkout and `scripts/provision_laya.py --verify-only` passed using the packaged Python runtime. It reports pinned source `1e28ac20c0896b1c37a744cd11f740eb98f8b178`, weights `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, verified worker, and external model root. Laya stays shadow-only with sampling disabled.
- Faster-Whisper small is already installed under the same external model root; the packaged runtime manifest records its pinned file hashes and model-load result. The Vosk fallback model was copied to that canonical root; its source duplicate remains until the owner purge.
- `.build` was copied to `%LOCALAPPDATA%\GENIE\build`; the copy has the same file and byte totals as the source, and its .NET SDK reports 8.0.425. `build_backend_runtime.py` now uses `GENIE_BUILD_ROOT` or the external build root.
- User overrides resolve from `data_dir()/user.json`; bundle builders exclude `config/user.json`. The machine-specific ignored file was replaced with `{}`, and `config/user.example.json` contains safe sample defaults. Provider overrides remain under the configured per-user data directory.
- Browser profile defaults now use the configured data directory. Product source and package templates do not need repository profiles, local vault/database files, captures, logs, Laya source or `.build`.
- Source-generated WPF `bin/` and `obj/` trees contain reproducible outputs and absolute build intermediates; the owner manifest includes them for removal before the clean-room build. The existing installer remains preserved.
- `docs/HISTORY_REPAIR_LOG.md`, `docs/THIRD_PARTY_NOTICES.md`, `docs/DEPENDENCY_LICENSES.md`, and `docs/CLEAN_ROOM_BUILD.md` were added. Direct dependency metadata and model/source pins were inspected. A version-specific transitive SBOM, bundled native notices and owner asset-rights record are still required before commercial distribution.
- A source/config secret-pattern scan found zero credential-shaped literal files; matched values were never printed. Local DBs, vault, provider overrides, profile state, screenshots, logs and agent memory were identified without reading/decrypting their sensitive contents.
- `scripts/OWNER_FINAL_WORKSPACE_PURGE.ps1`, `scripts/verify_final_workspace.py`, and `docs/OWNER_WORKSPACE_PURGE.md` are prepared. The owner purge has not been executed.

## Gate status

| Gate | State |
|---|---|
| Model/config migration and replacement verification | **PREPARED** — Laya worker verification passed; other model copies verified by inventory. |
| Candidate manifest/reference review | **PREPARED** — exact current statuses recorded; unreadable pytest temporary trees are `MIGRATION_REQUIRED` and will not be purged by the owner script. |
| Owner purge | **PENDING_OWNER** — requires the owner to run the documented single command and type the confirmation phrase. |
| Post-purge read-only verifier | **PREPARED, NOT RUN** — it is expected to fail before purge. |
| Clean-room WPF/backend build, runtime sync, tests, installer rebuild/install/uninstall | **NOT RUN** — run only after owner purge and verifier. |
| New installer hash/manifest and sell-ready certification | **NOT RUN** — current `dist/GENIE-Setup.exe` is preserved but not accepted as a fresh release. |
| Owner-authenticated browser, WhatsApp, physical voice/monitor and visual acceptance | **PENDING_OWNER** — no owner session or hardware acceptance is claimed. |

No workspace purge, clean-room build or new installer acceptance is claimed. This document must remain incomplete until the owner purge receipt and all post-purge gates are reviewed. Durable past repair evidence is retained in `docs/HISTORY_REPAIR_LOG.md` and the external checkpoint.
