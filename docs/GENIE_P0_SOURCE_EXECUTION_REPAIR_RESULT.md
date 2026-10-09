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

## P0.4 status

## P0.5 status

## P0.6 status

## P0.7 status

## P0.8 status — latest authoritative runtime result

## P0.9 status

## P0.10 status

- Real isolated browser fixture: **PASS_FIXTURE** — runner executed `p1_browser_acceptance.py`; 19/19 assertions passed in 7.81 s, exit code 0.
- Fixture tab switching/scrolling: included in the executed 19 assertions; no owner browser/profile was used.
- Interactive desktop preflight: **BLOCKED_INTERACTIVE_SESSION** — Windows input desktop accessible, but `GENIE_WINDOW=NOT_FOUND`; this is specifically `BLOCKED_GENIE_NOT_RUNNING`, not a machine-wide desktop failure.
- Existing Brave, YouTube, WhatsApp and physical voice: **NOT_RUN / BLOCKED_AUTHORIZATION**; no owner session or test-conversation authorization was available.

- Local Windows acceptance runner: **PASS_FIXTURE** — `scripts/p09_local_windows_acceptance.py --mode fixture --no-external-messages`; Preview and packaged Python paths present, pywinauto available, external messages disabled.
- Interactive desktop acceptance: **BLOCKED_INTERACTIVE_SESSION** — current session has no interactive desktop (`INTERACTIVE_DESKTOP=False`). No owner browser, YouTube, WhatsApp, or voice result is claimed.
- Existing-browser fallback, tab switching and scrolling remain unverified real GUI; fixture/headless contracts remain separate from owner acceptance.

| Test | HTTP/status | Error/stage | Duration | Result |
|---|---:|---|---:|---|
| Unauthenticated GET | 403 | HTTP reachability | 156 ms | PASS_HEADLESS_INTEGRATION |
| Authenticated models GET | 200 | none | 250.3 ms | PASS_REAL |
| OpenAI-compatible POST | 200 | none | 1,946.6 ms | PASS_REAL |
| Native Gemini POST | not run | bounded differential not required after compatible success | — | NOT_REQUIRED |
| Gateway/packaged Chat | 200 | `trace_054ea7bd98af`, reply `GENIE_PROVIDER_OK` | 2,599 ms | PASS_REAL |
| Chat-driven C: storage | 200 | `trace_b89ad1e604ed`, `files.disk_usage`, verified receipt | 127 ms tool latency | PASS_REAL |

The earlier P0.7 timeout is superseded by this successful authenticated GET, direct POST, Gateway Chat, and Chat-to-tool execution. Direct network phases remain healthy: DNS, TCP, TLS and curl/urllib reachability all passed. GUI-only browser/WhatsApp/voice acceptance remains `BLOCKED_ENVIRONMENT` because the trusted `sky` bridge is unavailable.

- DNS: **PASS_HEADLESS_INTEGRATION**, resolved in 16 ms.
- Direct TCP: **PASS_HEADLESS_INTEGRATION**, connected to 443 in 47 ms.
- Direct TLS: **PASS_HEADLESS_INTEGRATION**, TLS 1.3 in 47 ms.
- Proxy: **PASS_HEADLESS_INTEGRATION**, no proxy configured.
- urllib HTTPS: **PASS_HEADLESS_INTEGRATION**, unauthenticated HTTP 403 in 140 ms (HTTP responder reached; not Chat evidence).
- curl HTTPS: **PASS_HEADLESS_INTEGRATION**, HTTP 403, TCP 25 ms, TLS 376 ms, first byte 752 ms.
- Authenticated Chat POST: **FAIL / BLOCKED_PROVIDER**, transport timeout after request start at ~20,068 ms.
- Gateway/packaged Chat and Chat-driven C: storage: **BLOCKED_PROVIDER**; no authenticated completion.
- HTTPS downgrade protection added and packaged source synchronized; no system network settings changed.

- Packaged diagnostic checkpoints: **PASS_HEADLESS_INTEGRATION** — packaged identity, config, Vault, credential presence, endpoint validation, request start and finish were all observed.
- Authenticated Gemini completion: **FAIL / BLOCKED_PROVIDER** — `HTTP_STATUS=0`, `HTTP_ERROR_CODE=TRANSPORT_TIMEOUT`, inner elapsed **20084.0 ms**, process elapsed **20188.0 ms**, exit code 1. This is a transport timeout after request start; it is not a 404/401 claim.
- Hard deadline runner: **PASS_HEADLESS_INTEGRATION** — packaged Python, unbuffered checkpoints, 50-second outer deadline.
- Vault/provider/gateway regressions: **45 passed**. Origin comparison regression check passed.
- Real Chat and Chat-driven C: storage: **BLOCKED_PROVIDER** until the configured Gemini endpoint returns a completion.

- Packaged diagnostic identity: **PASS_HEADLESS_INTEGRATION** — imports `backend-dist/backend-runtime/app`, confirms packaged mode, and uses `get_config(reload=True)` like the daemon.
- Vault safety: **PASS_UNIT_ONLY** — existing unreadable/corrupt Vaults now raise `VaultLoadError` instead of becoming an empty store; no overwrite occurs. Security/provider/gateway tests: **45 passed**.
- Actual resolved state: `vault_exists=True`; authenticated diagnostic reached the configured Gemini completion endpoint but did not complete within the bounded request window. No credential value was printed. Final credential/completion status remains **BLOCKED_PROVIDER** pending the actual sanitized diagnostic result on the owner's runtime.
- Focused workflow regressions remain **78 passed**.

- Authenticated Gemini diagnostic: **BLOCKED_PROVIDER** — `scripts/diag_gemini_authenticated.py` safely inspected the real Vault and reported `GEMINI_CREDENTIAL_MISSING`; no key was printed or changed.
- Endpoint construction: **PASS_HEADLESS_INTEGRATION** — `chat_url()` targets `/v1beta/openai/chat/completions`; the prior unauthenticated GET 404 is not used as Chat evidence.
- Redirect security: **PASS_UNIT_ONLY** — same-origin now compares scheme, hostname, and effective port; redirect chain is captured from the handler and HTTP errors include elapsed timing.
- Real provider completion and Chat-driven C: storage: **BLOCKED_PROVIDER** until the owner configures a Gemini credential.
- Focused regressions: **78 passed**.

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

Baseline `4234e965272e008e09ed84810aae6333924fcea2` was already published. P0.1–P0.5 were published through `4c38080141200d994351d0dbe81046cb950e38f6`; this P0.6 source update is published after safe exclusion checks. No credentials, owner data, profiles, logs, or generated runtime binaries are included.
