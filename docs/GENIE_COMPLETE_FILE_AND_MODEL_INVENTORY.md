# GENIE File and Model Inventory

**Inventory date:** 2026-10-09 (Asia/Calcutta)  
**Scope:** read-only inventory of identified GENIE paths. No files were removed, moved, or opened for secret contents. The protected recovery checkpoint was only read.

## Identified roots

| Path | Files / directories | Bytes | Git / purpose | Disposition |
|---|---:|---:|---|---|
| `E:\G3\GENIE` | 53,194 / 8,070 | 6,172,910,695 | Active checkout; branch `upgrade/genie-continuity-ui`, HEAD `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2` | Active source and owner changes; retain |
| `E:\G3\GENIE-preclean-checkpoint-20261008` | 53,172 / 8,073 | 6,181,102,521 | Protected recovery checkpoint | Read-only; never purge |
| `C:\Users\Ghost\AppData\Local\GENIE` | 37,859 / 4,877 | 4,092,816,224 | Persistent data, build tools, runtime, models | Owner data; preserve |
| `E:\e\G3\GENIE-ui` | 356 / 66 | 4,774,837 | External Git worktree; `.git` link points at missing `E:/G3/GENIE/.git/worktrees/GENIE-ui`; Git commands fail | Unknown/unavailable worktree state; preserve |
| `E:\G3\GENIE-worktrees` | 15 registered entries | Not separately sized | Git reports most as `locked initializing`; 3 as `prunable` | Active/uncertain worktrees; preserve |
| `E:\G3 zip\G2 zip` | 33 RAR archives | 5,395,045,508 | User archive collection, includes `tmp.rar`, `c.rar`, `model.rar`, `laybbna.rar`, `venv.rar`, `GENIE-worktrees.rar`, and two checkpoint-named archives | Archive contents not inspected: RAR listing utility unavailable; unknown, preserve |
| `E:\G3GENIEartifactscaseCGENIE-C` | 3,918 / 537 | 494,631,500 | Historical Electron bundle (`GENIE.exe`, Chromium/Electron payload) | Not used by current WPF build; historical artifact, retain until owner disposition |
| `E:\tmp\genie_diag_938` | 3 / 10 | 609,843 | GENIE diagnostic temp folder | Preserve pending owner review |
| `E:\tmp\genie-test-deps` | 990 / 52 | 13,243,946 | GENIE test dependency environment | Developer tool; preserve |
| `E:\tmp\genie-upgrade-preview` | 5 / 9 | 1,397,913 | GENIE upgrade preview temp folder | Preserve pending owner review |
| `E:\tmp\tmp.BQVwszkCm2` | 5 / 9 | 1,483,280 | Temporary directory, provenance uncertain | Unknown; preserve |
| `C:\Users\Ghost\.cache\huggingface\hub` | 7 / 10 | 26,603,191 | HF cache contains Cactus needle2 and a 40-byte Whisper cache pointer | Cache not canonical; preserve |

Other E: roots were identified by name and shallow listing. `E:\c` contains a `Users\ghostt` profile and is not treated as GENIE data. `E:\e\G3\mirofish-env` is unrelated. `E:\free` resembles an OS/app image and was excluded. `C:\tmp` was absent. No unrelated trees were inventoried or changed.

## Model and model-like payloads

SHA-256 is over exact file bytes. Paths and classifications below distinguish inference weights from runtime test assets and tokenizers.

| Model / artifact | Exact path(s), size, SHA-256 | Purpose / loader | Classification and canonical path |
|---|---|---|---|
| Faster-Whisper small model | Repo `Systran/faster-whisper-small`, revision `536b0662742c02347bc0e980a01041f333bce120`. `C:\Users\Ghost\AppData\Local\GENIE\models\faster-whisper-small\model.bin` — 483,546,902 bytes, `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` | Hindi/Hinglish STT; `voice.service._resolve_fw_model` and `FasterWhisperSttProvider` | `DISTINCT_REQUIRED_MODEL`; canonical persistent location above. `config.json` 2,370 bytes SHA `b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828`; `tokenizer.json` 2,203,239 bytes SHA `fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab`; `vocabulary.txt` 459,861 bytes SHA `34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913`. Packaged build-match loaded it. Live microphone is untested. |
| Whisper staging copy | Same three files under `C:\Users\Ghost\AppData\Local\GENIE\models\.staging\faster-whisper-small`; tree comparison: 12 files, 486,214,552 bytes, byte-for-byte equal to final tree | Provisioning staging | `EXACT_DUPLICATE` of canonical Whisper directory; not deleted because it is in owner data and staging may be transaction/recovery state |
| Laya weights | `C:\Users\Ghost\AppData\Local\GENIE\models\laya\weights\model.safetensors` — 842,609,210 bytes, `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c` | Pinned Laya source `1e28ac20c0896b1c37a744cd11f740eb98f8b178`, weights revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`; isolated Laya worker | `OPTIONAL_MODEL`, provisioned and worker-verified; runtime status remains shadow-only, sampling disabled. Canonical path is the AppData path. |
| Laya duplicate weights | `E:\G3\GENIE\data\models\laya\weights\model.safetensors` and protected checkpoint `E:\G3\GENIE-preclean-checkpoint-20261008\data\models\laya\weights\model.safetensors`; each 842,609,210 bytes with the same SHA above | Previous checkout provision plus protected checkpoint | `EXACT_DUPLICATE` of canonical Laya weights; 7-file, 846,219,445-byte weights trees match exactly. Workspace copy is listed in the owner purge manifest; not removed. Checkpoint copy is protected. |
| Laya tokenizer/config | Canonical AppData and workspace/checkpoint copies: tokenizer 3,583,228 bytes SHA `6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30`; encoder config 2,083 bytes SHA `bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979` | Laya worker | Exact byte matches across all three roots. No cleanup performed. |
| Vosk `small-en-us-0.15` | AppData root has 15 files / 70,901,476 bytes including manifest; workspace and protected checkpoint each have 14 model files / 70,898,967 bytes. The canonical manifest records each relative path and SHA-256. | English fallback STT; `voice.service._build_stt` selects Faster-Whisper first when available | `DISTINCT_REQUIRED_MODEL` as an optional English-only fallback; canonical AppData path. Workspace/checkpoint 14-file payloads match AppData exactly. |
| Silero VAD | `faster_whisper/assets/silero_vad_v6.onnx` — 1,245,151 bytes, `4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2` | Faster-Whisper VAD dependency | Runtime dependency, not a second GENIE routing model. Exact copies in source `.build`, packaged Python, checkpoint `.build`/runtime, and AppData build. |
| Needle2 HF cache | `C:\Users\Ghost\.cache\huggingface\hub\models--Cactus-Compute--needle2\snapshots\32e9e3a93b205f786929697446ae669cf0a84579\config.json` — 1,087 bytes, `df9fc3b605a7c2135303b5f2e164cf1f500e04956393c73a9596284914072295`; cache tree totals 26,602,956 bytes | Cache copy; active Director reports `needle` and loads local native library from `%LOCALAPPDATA%\GENIE\runtime\needle2` | `UNKNOWN_REQUIRES_VERIFICATION` as to whether full cache is needed. Not a byte duplicate of the active Needle library; preserve. |
| Other ONNX and binaries | `.build` and packaged Python contain ONNX Runtime sample data (`logreg_iris.onnx` 670 bytes SHA `8224784c98d73412d9fd99abcd57a38568bd590980d0fbe5916464531c52e8fc`, `mul_1.onnx` 130 bytes SHA `71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10`, `sigmoid.onnx` 103 bytes SHA `5340aba67a7e3475162ad794378af55f1718f55f9a5d74b4af60ecc7f7a624b6`) | Third-party package test fixtures | `OPTIONAL_MODEL`/dependency fixtures, not product inference models. |

Archives `model.rar`, `laybbna.rar`, `vosk-model-small-en-us-0.15.rar`, `tmp.rar`, and `c.rar` were identified by exact archive path/name/size only. Their internal contents, model hashes, and overlap remain `UNKNOWN_REQUIRES_VERIFICATION`; do not remove or extract them without a dedicated read-only archive inspection and owner-approved disposition.

## Runtime, SDK, and output facts

- Canonical packaged Python runtime: `E:\G3\GENIE\backend-dist\backend-runtime` (263 synchronized app files); WPF Preview source is `ui\windows\Genie.Desktop`; current Release EXE is under its `bin\Release\net8.0-windows\win-x64` output.
- External .NET SDK/build tree: `%LOCALAPPDATA%\GENIE\build` (8,926 files / 1,119,205,707 bytes). SDK used for the final WPF build reports version 8.0.425.
- `E:\G3\GENIE\artifacts\owner-acceptance\GENIE-1789798628` is a historical Electron application output (includes `GENIE.exe`, Chromium libraries, `snapshot_blob.bin` 316,538 bytes SHA `f62f0a9aa940d438b1fdcb4dc0cfe576bcb95dc9e83fc4474b9c66383f186bfa`, and `v8_context_snapshot.bin` 687,473 bytes SHA `5a4cb4fe30e23586f4e81a9bad26908b25fcb951358fe7a86a8c56b9af3a9c68`). Current WPF packaging does not use this directory; it is not deleted.
- No external archive was extracted; no credential, vault, browser-profile or personal owner-data content was read. Per-user `data` and browser profiles remain protected.
