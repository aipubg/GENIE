# GENIE single-workspace cleanup result — 2026-10-09

> **Relocation follow-up resolved:** The owner confirmed `E:\G3\GENIE` as canonical. See `GENIE_WORKSPACE_RELOCATION_VERIFICATION.md` for completed path reconciliation, parity, WPF build and build-match 34/34. Earlier pending-path statements below are historical.

The authorized cleanup was executed against `E:\git G3\G3\GENIE`. The named checkpoint was permanently removed after comparison. No full-project backup was created.

**Path change detected after verification:** the authorized root is now absent. The same cleanup evidence (107 deletion records) and matching WPF DLL were found at `E:\G3\GENIE`. This move was not performed by this cleanup. The report is saved there. Owner confirmation of the new canonical path is pending; the build results below were obtained before that external path change. Helper scripts corrected to the former authorized root need reconciliation if the new location is retained.

## Measured deletion results

- Initial E: free space immediately before the first deletion: **398,223,458,304 bytes**.
- Final recorded free space after the last deletion: **409,750,949,888 bytes**.
- Observed free-space increase: **11,527,491,584 bytes (11.527 GB / 10.736 GiB)**.
- Sum of logical file bytes actually removed: **11,355,896,756 bytes (11.356 GB)**.
- Files deleted: **96,772**. Directories deleted: **13,338**.
- Successful deletion records: **107**; failures within those records: **0**.
- NTFS allocation and concurrent filesystem writes explain why free-space delta differs from logical file sizes. These are measured figures, not folder-size estimates.
- Two inaccessible cache directories were not deleted; their unknown contents are excluded from these counts.

Paths in the following table are their absolute locations when cleanup executed.

| Path | Classification | Size (bytes) | Decision | Actual deletion result |
|---|---|---:|---|---|
| `E:\git G3\G3\GENIE-preclean-checkpoint-20261008` | Reconciled duplicate checkpoint | 6,181,102,521 | Delete | Absent; 53,172 files and 8,074 directories removed |
| `E:\git G3\e\G3\GENIE-ui` | Obsolete source checkout | 4,774,837 | Delete | Absent; 356 files removed |
| `E:\git G3\G3\GENIE-worktrees\ui-final-genie-companion` | Obsolete checkout with selected legacy owner data | 18,020,417 enumerated bytes | Preserve unique DB/log, remove reconciled source | All 726 enumerated files removed; inaccessible `.pytest_cache` remains |
| `E:\git G3\G3\G3GENIEartifactscaseCGENIE-C` | Historical Electron distribution | 494,631,500 | Delete | Absent |
| `E:\git G3\G3\GENIE\.build` | Exact duplicate of AppData build tools | 1,119,205,707 | Delete | Absent; 8,926 files removed |
| `E:\git G3\G3\GENIE\data\models` | Duplicate Laya and Vosk payloads | 1,778,218,227 | Delete local copies | Absent; 27,867 files removed; active AppData copies retained |
| `E:\git G3\G3\GENIE\artifacts\exe_icon_report.json` | Regenerable report | 470,629,900 | Delete | Absent |
| `E:\git G3\G3\GENIE\artifacts\sdkdl` | Obsolete SDK download ZIP | 281,358,929 | Delete | Absent |
| `E:\git G3\G3\GENIE\artifacts\ui-verify` | Old verification build output | 152,678,436 | Delete | Absent |
| `E:\git G3\G3\GENIE\artifacts\owner-acceptance` | Old Electron installation output | 281,470,731 | Delete | Absent |
| `E:\git G3\G3\GENIE\dist\GENIE-Setup.exe` | Superseded installer | 98,269,336 | Delete | File and empty dist directory removed |
| `E:\git G3\G3\GENIE\ui\windows\Genie.Desktop\bin\LiveVerification` | Superseded WPF output | 305,360,064 | Delete | Absent |
| `E:\git G3\G3\GENIE\ui\windows\Genie.Desktop\bin\x64` | Superseded WPF output | 152,558,209 | Delete | Absent |
| `E:\git G3\G3\GENIE\ui\windows\Genie.Desktop\obj\LiveVerification` and `obj\x64` | Superseded intermediates | 3,638,814 | Delete | Both absent |
| 42 source `__pycache__` directories under the canonical root | Generated cache | 13,589,753 | Delete | 599 files removed; exact paths in deletion ledger |
| `E:\git G3\G3\GENIE\browser-profile-2`, `browser-profile-3`, `GENIE-worktrees` | Empty directories | 0 | Delete | Absent |
| `E:\git G3\G3\models` | Distinct Laya checkpoint | 678,208,667 | Keep | Retained; weights differ from production payload |
| `E:\git G3\G3\references` | Unique upstream reference repository | 9,139,231 | Keep | Retained |
| `E:\git G3\G3\GENIE\backend-dist` | Active packaged runtime | 550,066,065 at inventory | Keep and sync | Retained; 264 source files verified |
| `E:\git G3\G3\GENIE\browser-profile` and `browser-profile-abs` | Potential owner browser data | 150,638,335 combined | Keep | Retained |
| `E:\git G3\G3\GENIE\data` excluding duplicate models | Owner DB, vault, settings, workspace and evidence | See discovery inventory | Keep | Retained |
| `C:\Users\Ghost\AppData\Local\GENIE\models` | Active persistent model store | 2,750,649,940 at inventory | Keep | Retained, including staging |
| `C:\Users\Ghost\AppData\Local\GENIE\build` | Canonical build dependencies | See model/tool comparison | Keep | SDK used successfully for WPF build |

The deletion ledger lists every exact path, before/after free space, file count, directory count and deletion result, including small Git metadata and generated file removals omitted from the compact table.

## Checkpoint reconciliation

The completed recursive SHA-256 comparison covered **53,172 files / 6,181,102,521 bytes**:

- 52,946 byte-identical to the main project at comparison time.
- 71 older source/configuration/document versions; current repairs were retained.
- 145 generated artifacts, old packaged code or caches.
- 7 Git metadata differences. All 12 checkpoint refs and all 58 unreachable Git objects were already present in the canonical repository.
- 2 state differences: `data/genie.db-shm` is SQLite shared memory; the DB and WAL were identical. `data/plugins/startup_circuit.json` differed only in an older timestamp for the reset `test_crash` fixture.
- 1 initially unique file, `installer/GENIE.iss`, was resolved as obsolete: the current deletion manifest and native migration report explicitly identify it as superseded by `installer/genie_native.nsi`.

All checkpoint browser-profile files were byte-identical to retained main copies. The old bundled `config/user.json` contained defaults, obsolete paths and `models.mock_mode=true`; current code deliberately uses mutable data configuration and production defaults. It was not restored over current configuration.

## Source and worktree preservation

Git HEAD remained **c188a00a8211c5aa7c2ce6c88362004ac25296cb**. No reset, checkout replacement or commit was performed.

A before/after hash inventory covered 1,093 protected entries. Eight tracked text files changed only for intended path corrections. The 21 missing tracked entries were the explicitly authorized duplicate model payloads, whose AppData counterparts were hash-verified. Other protected source entries were unchanged.

The old checkouts contained line-ending differences and superseded source. The companion checkout's 295 persona files were text-equivalent to current files. Its separate SQLite database and log were reconciled into `data/legacy/ui-final-genie-companion/` using a consistent SQLite backup and an exact log copy. Old grants were not imported into active authorization state.

The GENIE-ui and companion worktree registrations were removed. Three nonexistent probe worktrees were also deregistered after their indexes matched their ORIG_HEAD trees. Twelve other nonexistent evolution worktree registrations remain because their staged index trees differ from their bases; pruning would discard unreconciled staged state. They are metadata, not additional on-disk editable checkouts. Details and tree IDs are in `cleanup-worktree-metadata-20261009.json`.

## Models and paths

Production loaders resolve the persistent store at `C:\Users\Ghost\AppData\Local\GENIE\models`.

| Retained model payload | Bytes | SHA-256 |
|---|---:|---|
| `laya\weights\model.safetensors` | 842,609,210 | `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c` |
| `vosk-model-small-en-us-0.15\am\final.mdl` | 15,962,575 | `75370a0137f9daf8f469dedd7daa4513ae7a621f03240c6e512e2b50b656a7b6` |
| `faster-whisper-small\model.bin` | 483,546,902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |

Fresh comparison found all 14 local Vosk files and all 8,926 local build-tool files identical to AppData. Laya initially had 27,852 of 27,853 files identical; the sole difference was obsolete provisioning metadata. AppData provisioning metadata, `pyvenv.cfg` and `activate.bat` were corrected to the persistent location. Laya remains shadow-only. The distinct sibling Laya checkpoint, Hugging Face/staging data and upstream references were retained. Needle's AppData runtime was retained. Current voice VAD uses the source energy-based implementation; unrelated packaged model assets were not removed.

Development/shortcut/acceptance scripts and a tracked WPF temporary project were corrected from the earlier E:\G3 root to the then-authorized E:\git G3\G3\GENIE root. Historical comments, synthetic fixture paths and cleanup inventory targets were not treated as runtime launch settings. No matching stale GENIE shortcut or persistent GENIE/Python/.NET environment override was found in the inspected locations. The later external folder relocation requires a new canonical-path decision.

## Build and runtime verification

- Canonical backend synchronization succeeded: **264 files**.
- Independent source/runtime hash check succeeded.
- Build-match: **34/34**; packaged dependency imports passed and Faster-Whisper loaded successfully.
- WPF Release `net8.0-windows/win-x64` build succeeded: **0 warnings / 0 errors** using the retained AppData .NET SDK.
- Source fingerprint: `504651fc0a12bfda4098db9f91eddc2bc8bc834aad469d23187b4b30415bfad0`.
- Preview EXE SHA-256: `2af57afd492fbca6eb35e0cf5116fa63d45ef2f4eef87af19a988832a2dee580`.
- WPF DLL SHA-256: `3c3c7d2f50c676b52e9cb6427b207779ca146e80180f7198b530e900a76a50c1`.
- EXE timestamp: **2026-10-09 15:59:50.017 +05:30**, size **290,304 bytes**. Cleanup did not change application code or binary identity.
- Last observed executable after external relocation: `E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe`.

No Preview launch, GUI automation, browser action, WhatsApp action or physical microphone test was performed. These build checks do not establish owner workflow acceptance. Launch readiness at the externally changed path has not been revalidated.

## Unresolved items

1. Windows denies enumeration of the canonical `.pytest_cache` and the old companion worktree `.pytest_cache`. Both were left unchanged. A direct deletion attempt on the companion cache was rejected by automatic approval review with the stated reason **blocked by policy**; no more detailed reason was supplied.
2. Twelve stale evolution worktree metadata entries contain index trees different from their original bases. They were retained to avoid losing unreconciled staged content.
3. The authorized canonical path disappeared after the successful build gates. Owner confirmation is needed before changing launch/build paths to the externally observed `E:\G3\GENIE` location.

## Evidence files

All evidence is under the current project's `artifacts` directory:

- `cleanup-deletions-20261009.json`: exact deletion ledger.
- `cleanup-discovery-20261009.json`: initial directory inventory.
- `cleanup-comparison-GENIE-preclean-checkpoint-20261008-20261009.jsonl` and its summary: complete checkpoint file hashes and classifications.
- `cleanup-comparison-GENIE-ui-20261009.jsonl` and `cleanup-comparison-ui-final-genie-companion-20261009.jsonl`: old checkout comparisons.
- `cleanup-model-tool-comparison-20261009.json`: fresh duplicate model/tool hashes.
- `cleanup-git-history-20261009.json` and `cleanup-git-unreachable-20261009.json`: Git object preservation evidence.
- `cleanup-protected-source-before-20261009.json`: protected source hashes.
- `cleanup-worktree-metadata-20261009.json`: staged-tree and registration inspection.
