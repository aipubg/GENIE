# GENIE P0 Source Execution Repair Result

**Workspace:** `E:\G3\GENIE`  
**Validation date:** 2026-10-09

## Implemented corrections

| Requirement | Evidence |
|---|---|
| Model-to-tool routing | `core/orchestrator.py::_reason_messages()` now derives `needs_tools` from the actual conversational tool loop as well as planned tasks/memory writes. |
| Hinglish navigation | `core/tool_dialogue.py::action_requested()` now recognizes `jao`, `chalo`, `switch to`, `go to`, `navigate to`, and `wapas jao` while retaining the instruction-question guard. |
| Mutation receipts | `_required_tool_groups()` now requires verified receipts for tab switching, scroll, reload, and video-volume requests. |
| Grounded Windows input | `input_hotkey` and `input_scroll` accept optional observed HWND/title pairs, validate them, and pass them through the bridge. Ctrl+1..9 are allow-listed safe browser shortcuts. |
| Provider catalog | Obsolete Gemini fallback IDs were replaced with `gemini-3.7-flash` and `gemini-3.1-pro-preview`. |

## Verification

- Focused workflow, owner-policy, and interaction regressions: **78 passed**.
- Backend synchronization: **264/264** files, parity check passed.
- Build-match: **34/34**.
- WPF Release `net8.0-windows/win-x64`: **0 warnings, 0 errors**.
- Packaged daemon `/health`: previously verified HTTP 200; `/api/status` reported `ready=true`, `director=needle`, `voice=gemini-live`, and Laya shadow-only.

## Real execution boundary

The Computer Use bridge remains unavailable with `Trusted RPC service is not configured: sky`. Consequently browser, YouTube, WhatsApp, visual fallback, physical voice, and WPF GUI workflows remain `BLOCKED_ENVIRONMENT`; no GUI success is claimed. A packaged ordinary Chat attempt also remains unresolved because the primary Gemini request timed out; the stale fallback IDs are repaired, but provider credential/network access still needs verification on the owner's machine.

## Build identity

- Preview DLL: `E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.dll`
- Preview EXE: `E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe`
- Backend runtime: `E:\G3\GENIE\backend-dist\backend-runtime`

## P0.1 status

## P0.2 status

## P0.3 status

- Provider transport classification: **PASS_HEADLESS_INTEGRATION** — `models/provider_http.py` now preserves stable DNS/TLS/timeout/refusal/reset codes and elapsed timing; OpenAI-compatible adapter surfaces the category.
- Direct ComputerService storage query: **PASS_HEADLESS_INTEGRATION** — packaged `files.disk_usage('C:\\')` returned total 563.9 GB, used 114.6 GB, free 449.3 GB.
- Real non-mock Chat and Chat-driven storage query: **BLOCKED_PROVIDER** — no authorized provider completion was available; static HTTP probe reached Gemini with HTTP 404 and no credentials were used. No Chat success is claimed.
- Browser CDP fixture, existing-browser fallback, tab switch/scroll, WhatsApp navigation: **BLOCKED_ENVIRONMENT** for real GUI; trusted `sky` bridge remains unavailable.

- Contradictory tool-execution system instruction: **PASS_UNIT_ONLY**; `_reason_messages()` now explicitly authorizes tool use only through supplied computer functions and requires verified receipts.
- Full collection: **1,817 tests collected, 79 deselected**. The broad run was stopped after repeated long-running portions; it is not reported as a full PASS. Focused workflow/owner/interaction regressions remain **78 passed**.
- Ordinary Gemini Chat and safe disk-space tool call: **FAIL/BLOCKED_PROVIDER**; no verified real-provider completion was available, so no success is claimed.
- Existing browser/UIA fallback, tab switching, scrolling, WhatsApp contact selection, physical voice: **BLOCKED_ENVIRONMENT** (`sky` trusted RPC unavailable).
- Packaged parity: **PASS_PACKAGED**, 264/264.
- WPF Release/build-match: **PASS_PACKAGED**, 0 warnings/0 errors and 34/34.

- Model-to-tool routing: **PASS_UNIT_ONLY**.
- YouTube player volume contract: **PASS_PACKAGED_INTEGRATION** (manifest validation, bridge registration, scope/planner/verifier registration, and bound-session implementation).
- Provider override persistence: **PASS_UNIT_ONLY** (field-level default diff; credentials remain references).
- Real provider Chat: **BLOCKED_ENVIRONMENT/PROVIDER** — prior Gemini transport timeout; no fabricated success.
- Existing browser fallback, tab switching, scrolling, WhatsApp navigation: **BLOCKED_ENVIRONMENT** because the trusted desktop bridge is unavailable (`sky` RPC not configured).
- Source/runtime parity: **PASS_PACKAGED_INTEGRATION**, 264/264.
- WPF Release: **PASS_PACKAGED_INTEGRATION**, 0 warnings/0 errors; build-match 34/34.

## GitHub

Baseline `4234e965272e008e09ed84810aae6333924fcea2` was already published. P0.1 was published at `a3ace888016c1e37a7729db607f887cca107401e`; this P0.2 source update will be published after the safe exclusion-checked push. No credentials, owner data, profiles, logs, or generated runtime binaries are included.
