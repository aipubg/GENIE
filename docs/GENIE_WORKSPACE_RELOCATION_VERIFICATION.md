# GENIE workspace relocation verification — 2026-10-09

**Canonical editable project: `E:\G3\GENIE`.** The owner confirmed the manual relocation. The previous `E:\git G3\G3\GENIE` location is absent and was not recreated.

## Repository and preservation

Git HEAD is `c188a00a8211c5aa7c2ce6c88362004ac25296cb`, branch `upgrade/genie-continuity-ui`. The repository resolves at the new root without a `core.worktree` override. Python, WPF and owner-policy/execution repairs are present. Existing modified and untracked work was preserved; no reset, reclone, commit, duplicate checkout, backup or cleanup was performed in this relocation pass.

Comparison with the cleanup's 1,093-entry protected inventory found no missing source outside the 21 intentionally removed duplicate model entries from the preceding cleanup. Those model deletions remain visible in Git; they were not caused by relocation. Existing application-code hashes match the protected inventory. The eight changed tracked text entries are the path-helper/project corrections described below.

## Stale references and repairs

The executable references to the previous root were found in ten helper/project files. Normal helpers now derive the project root from their script location; the tracked temporary WPF project uses `$(MSBuildProjectDirectory)`. The historical purge helper keeps an explicit safety guard, corrected to the owner-approved canonical root; it was not executed.

Exact source/helper files modified:

- `scripts/capture_ui.ps1`
- `scripts/create_preview_shortcut.vbs`
- `scripts/make_lnk.py`
- `scripts/owner_session_diagnostic.ps1`
- `scripts/OWNER_FINAL_WORKSPACE_PURGE.ps1`
- `scripts/p1_app_acceptance.py`
- `scripts/p1_browser_acceptance.py`
- `scripts/p1_final_repairs_test.py`
- `scripts/probe_strix_deps.py`
- `ui/windows/Genie.Desktop/Genie.Desktop_0t1ma1la_wpftmp.csproj`

Six generated WPF metadata files also contained the previous root. Their path strings were corrected in place, without deleting files: the Release `Genie.Desktop.csproj.FileListAbsolute.txt` and five old temporary-project SourceLink JSON files. Their exact paths are recorded in `artifacts/relocation-verification-20261009.json`.

Remaining old-root mentions in cleanup evidence and historical documentation describe the locations at which cleanup ran. The read-only `scripts/reconcile_workspace_inventory.py` explicitly probes old candidate names to report their existence; it never launches or creates a checkout. Frozen cleanup evidence scripts in `artifacts` retain their historical target roots and are not active launch/build configuration. These records were not rewritten as current paths.

Searches found no remaining previous-root references in active Python/C#/PowerShell/VBS/configuration/installer source or current packaged application source. Inspected user/machine/process environment variables, Windows service commands, Desktop/Start Menu shortcuts and AppData runtime/model configuration contained zero previous-root matches. No VS Code workspace file was found at the inspected project/parent locations. Owner data, browser profiles, service registrations and shortcuts did not need relocation writes.

The twelve pre-existing stale evolution worktree registrations still point to nonexistent locations and retain staged index state. They do not supply source to the application. They were left intact under this task's no-deletion restriction. The preceding cleanup's protected cache directories also remain outside this relocation task.

## Backend and frontend connections

The packaged interpreter is `E:\G3\GENIE\backend-dist\backend-runtime\python\python.exe` (Python 3.12.6). Its `python312._pth` uses relative packaged library paths. Source and packaged import checks ran in separate Python processes with their respective roots and verified every returned module file belongs to that root.

`GenieBackend.FindRuntimeRoot` already discovers the runtime relative to the executable and then by walking parent directories. Applying that discovery order to the actual Release directory selected `E:\G3\GENIE\backend-dist\backend-runtime`. `BackendProcess` derives `pythonw.exe` and `app/backend_entry.py` from that runtime. `BackendLifecycle` retains runtime-root and source-fingerprint checks before accepting a daemon. `BackendClient` IPC behavior, WPF interface and request-specific Stop repairs were preserved. No application-code rewrite was needed for relocation.

## Verification results

| Check | Result |
|---|---|
| Backend syntax compilation in memory | 206 Python files passed |
| Modified Python helper syntax | 5 files passed |
| Modified PowerShell parser checks | 3 files passed |
| Critical source imports | 13/13; all from canonical source |
| Critical packaged imports | 13/13; all from packaged app |
| Canonical backend synchronization | 264 files; final sync used `--no-prune` |
| Independent source/runtime SHA-256 parity | PASS, 264 files |
| WPF Release net8.0-windows/win-x64 | PASS, 0 warnings / 0 errors |
| Existing build-match verification | 34/34 |
| Previous source root | Absent; not recreated |

The retained SDK at `%LOCALAPPDATA%\GENIE\build\dotnet-sdk-8.0.425\dotnet.exe` restored and built the current WPF project. Build output and runtime manifest now resolve under the new canonical root.

## Model locations

Faster-Whisper, Laya and Vosk retain the persistent `%LOCALAPPDATA%\GENIE\models` store. No model was downloaded, moved or deleted. Faster-Whisper loaded successfully in packaged Python during build-match. Laya's persistent virtualenv starts and imports Torch 2.14.1+cpu and Transformers 4.57.6; its pinned source, weights and provisioning metadata exist. Its mode remains shadow with background sampling disabled. Needle remains the existing production director path; no routing configuration was changed.

Needle's native payload is `%LOCALAPPDATA%\GENIE\runtime\needle2\libneedle.dll`. Faster-Whisper resolves its Silero VAD payload relative to its installed package at `backend-dist/backend-runtime/python/site-packages/faster_whisper/assets/silero_vad_v6.onnx`. GENIE's direct voice VAD implementation uses its existing energy-based code. AppData staging and the distinct sibling Laya checkpoint/reference source remain intact.

Five principal payloads were freshly checked for existence, nonzero size and SHA-256:

| Path | Bytes | SHA-256 |
|---|---:|---|
| `C:\Users\Ghost\AppData\Local\GENIE\models\laya\weights\model.safetensors` | 842,609,210 | `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c` |
| `C:\Users\Ghost\AppData\Local\GENIE\models\vosk-model-small-en-us-0.15\am\final.mdl` | 15,962,575 | `75370a0137f9daf8f469dedd7daa4513ae7a621f03240c6e512e2b50b656a7b6` |
| `C:\Users\Ghost\AppData\Local\GENIE\models\faster-whisper-small\model.bin` | 483,546,902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| `E:\G3\GENIE\backend-dist\backend-runtime\python\site-packages\faster_whisper\assets\silero_vad_v6.onnx` | 1,245,151 | `4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2` |
| `C:\Users\Ghost\AppData\Local\GENIE\runtime\needle2\libneedle.dll` | 14,312,960 | `2955e28436b9d7569b40cf89d17e3e4097e79b1710f1926cca01f9f67a5a579a` |

## Current executable and identity

- Preview EXE: `E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe`
- EXE bytes: 290,304
- EXE local timestamp: `2026-10-09T21:04:55.908575`
- EXE SHA-256: `2af57afd492fbca6eb35e0cf5116fa63d45ef2f4eef87af19a988832a2dee580`
- WPF DLL SHA-256: `1241374b2508a9ca9259ec23778542d5e3343223fa7ff255fddfd55ae71e2370`
- Backend source fingerprint: `504651fc0a12bfda4098db9f91eddc2bc8bc834aad469d23187b4b30415bfad0`

## Remaining limits

No relocation-related compilation, import, model-path or synchronization blocker remains. The historical stale-worktree/cache cleanup items remain documented in `GENIE_SINGLE_WORKSPACE_CLEANUP_RESULT.md` and were not deleted during this task.

No GUI was launched, no computer/browser/WhatsApp workflow was executed, and no physical microphone acceptance was claimed. Daemon live health was not exercised in this build-only relocation pass. The owner can launch the verified Preview path for personal functional testing.

Machine-readable evidence: `artifacts/relocation-verification-20261009.json`. The cleanup report's earlier pending canonical-path question is resolved by the owner's explicit confirmation and this verification.
