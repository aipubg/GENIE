# GENIE — DECISION LOG

Format: `D-0xx | status | decision | rationale | consequences`
Status: `OPEN` (decision needed) · `ACCEPTED` · `SUPERSEDED`

Ek baar ACCEPTED ho gaya to AI developer uspe dobara bahas nahi karega — implement karega.
Badalna ho → naya decision entry + purana `SUPERSEDED`.

---

## Owner answers (baseline v1)

| ID | Status | Decision |
|---|---|---|
| **D-001** | ✅ ACCEPTED | **Stack:** Python 3.13 core/backend · TypeScript + Electron desktop · Kotlin Android. All major systems communicate through stable contracts so any frontend/backend component can be replaced later. **UI and brain remain separate processes.** |
| **D-022** | ✅ ACCEPTED | **Target OS:** Windows 10 + 11 (+ future compatible). Windows 7/8/8.1 **not supported**. Must run on modest hardware (no GPU / low RAM assumption). Detect RAM, CPU, GPU, virtualization, cameras, mics, monitors, Bluetooth, network at setup/runtime and adapt. Workspace isolation preference: Windows-native → Hyper-V → WSL2 → other safe option → controlled isolated local workspace. **Missing virtualization must not block development.** |
| **D-023** | ✅ ACCEPTED | **Provider system:** no single-provider binding. Extensible provider registry with predefined providers (Tencent Cloud/TokenHub, GLM, Kimi International, MiniMax International, DeepSeek, Gemini, OpenRouter, Together AI, Custom) + user-added OpenAI-compatible endpoints. Per-model capabilities (tool calling, image input, reasoning, custom protocol, context/output limits), test connection, health, fallback models, priorities, cost, quota state. **Keys only in the vault. Adding/removing models must never need source changes.** |
| **D-024** | ✅ ACCEPTED | **NEDLE2 = `Cactus-Compute/needle2`.** Local director for intent/task/tool/agent/provider/device/mission routing, event + memory classification, retrieval proposals, structured extraction, and deciding when a big remote model is needed. **Not** behind Ollama or a heavyweight local server. Wrapped behind a `DirectorProvider` abstraction so it can be upgraded/benchmarked/replaced. NEDLE2 decides; services own truth. |
| **D-025** | ✅ ACCEPTED | **Android node in Kotlin**, conforming to GENIE's Device Contract. May reuse/adapt supplied repos (esp. OpenClaw) rather than rewriting solved infrastructure. Contracts ready earlier than Phase 6 implementation. |
| **D-026** | ✅ ACCEPTED | **Voice:** push-to-talk during early development (debuggable), but architecture designed for continuous conversational mode, wake/listening modes, barge-in, natural turn taking and proactive speech. **Not** designed around push-to-talk only. |
| **D-027** | ✅ ACCEPTED | **Multi-user:** owner-first implementation, but schemas/permission contracts multi-user capable from day one (owner, family, guests, separate memories/devices, shared projects, cross-user scopes). |
| **D-028** | ✅ ACCEPTED | **Language:** default conversational Hinglish; seamless Hindi / English / code-switching; no forced single language per session. UI in English initially, localization kept possible. |

## Engineering decisions (accepted)

| ID | Status | Decision | Rationale |
|---|---|---|---|
| **D-002** | ✅ ACCEPTED | Master spec is a **BASELINE / CONTROLLED SPEC** (not untouchable). Implementation-proven errors go evidence → RFC/decision → contract → implementation. Invariants stay much harder to change. | Owner correction: don't maintain a bad architecture just because v1 said so |
| **D-003** | ✅ ACCEPTED | **Only NEDLE2** is local reasoning; all heavy models remote | User constraint |
| **D-004** | ✅ ACCEPTED | NEDLE2 decides, services own truth | Prevents hallucinated "task complete" |
| **D-005** | ✅ ACCEPTED | Memory **supersedes**, never overwrites | Recoverable correction |
| **D-006** | ✅ ACCEPTED | SQLite WAL + FTS + vector cache; PostgreSQL later | No DB zoo day one |
| **D-007** | ✅ ACCEPTED | PTE + Vault + Audit land in **Phase 1** | Retrofit is far more expensive |
| **D-008** | ✅ ACCEPTED | External content = **DATA, not authority** | Prompt-injection defence |
| **D-009** | ✅ ACCEPTED | Core software evolution only via isolated branch → tests → benchmark → adoption | Memory/skills may auto-learn, code may not |
| **D-010** | ✅ ACCEPTED | Desktop agents work in **GENIE Workspace** | Protect the live PC |
| **D-011** | ✅ ACCEPTED | Automation priority: API → OS → A11y → DOM → Vision → raw mouse | Reliability |
| **D-012** | ✅ ACCEPTED | Proactivity = scored decision, never a 20-second timer | Avoid robotic behaviour |
| **D-013** | ✅ ACCEPTED | Agent comms via mailbox + blackboard, not shared transcripts | Context bloat + hidden coupling |
| **D-014** | ✅ ACCEPTED | Per-module docs `README/CONTRACT/STATE/TESTS` | Scoped context for AI developers |
| **D-029** | ✅ ACCEPTED | **Module count is not a goal.** The 113-micro-module scaffold was consolidated into real packages with one responsibility each. Trivial micro-modules merged; later-phase folders created only when their phase starts. | Owner rule: minimum useful complexity |
| **D-030** | ✅ ACCEPTED | **Zero third-party runtime dependencies** for the daemon (stdlib only: sqlite3, http.server, urllib, ctypes). pytest is dev-only. | Low-end PC support, simple packaging, fewer supply-chain risks |
| **D-031** | ✅ ACCEPTED | UI is a **thin client** (Electron shell + `ui/web` assets) talking to the daemon over HTTP + SSE. No business logic in the UI; a future 3D UI replaces only `ui/`. | Owner requirement |
| **D-032** | ✅ ACCEPTED | Mock provider is always available and used when no credentials exist | GENIE must run offline / before keys are configured |
| **D-033** | ✅ ACCEPTED | Scaffolding generator (`tools/gen_skeleton.py`) removed | It encoded the 113-module tree that D-029 abolished |

## License-driven decisions (see `LICENSE_MATRIX.md`)

| ID | Status | Decision |
|---|---|---|
| **D-015** | ✅ ACCEPTED | `ComfyUI` (GPL-3.0), `MiroFish` (AGPL-3.0), `VoiceStudio` (AGPL-3.0) → **separate repo + process boundary**, never imported |
| **D-016** | ✅ ACCEPTED | `Open-Higgsfield-AI` (no license file) → **code use blocked**; public API reference only |
| **D-017** | ✅ ACCEPTED | `n8n` (Sustainable Use) → external service; `.ee` files excluded |
| **D-018** | ✅ ACCEPTED | `HeyGem.ai` → not bundled; >1,000 MAU triggers commercial license → replacement shortlist |
| **D-019** | ✅ ACCEPTED | `Decepticon` + `hack-skills` → isolated authorized security environment only |
| **D-020** | ✅ ACCEPTED | `Qwen-AgentWorld` → dev lab / eval harness only |
| **D-021** | ✅ ACCEPTED | `VoiceStudio` dropped unless a unique capability is proven (OmniVoice Apache-2.0 preferred) |
| **D-034** | ✅ ACCEPTED | License/provenance files are **preserved, never deleted** when adapting code; final distribution review is owner-side and does not block implementation | Owner instruction |

---

## Assumptions made during implementation

Per the working rule: pick the practical default, document it, keep going.

| ID | Assumption | Where |
|---|---|---|
| **A-001** | Configuration is JSON (stdlib `json`), not YAML | `core/config.py` |
| **A-002** | Config precedence: `GENIE_*` env > `config/user.json` > defaults | `core/config.py` |
| **A-003** | `models.mock_mode` default true until real keys exist | `config/user.json` |
| **A-004** | SQLite WAL is the v1 store; migrations forward-only in transactions | `core/db.py` |
| **A-005** | Vault encryption: Windows DPAPI (ctypes) with a scrypt+XOR fallback elsewhere | `security/vault.py` |
| **A-006** | Owner gets bootstrap standing grants so GENIE is usable out of the box; non-owner stays default-deny | `security/trust.py` |
| **A-007** | `SECRET`/`RESTRICTED` data classes are never sent to any remote provider | `security/policy.py` |
| **A-008** | Token estimate = 4 chars/token when the API doesn't return usage | `models/providers/base.py` |
| **A-009** | Unknown provider protocol degrades to OpenAI-compatible instead of failing hard | `models/providers/base.py` |
| **A-010** | Provider transport uses stdlib `urllib` (no httpx/requests) | `models/providers/openai_compat.py` |
| **A-011** | If the Needle runtime is absent, the deterministic HeuristicDirector takes over and the fallback is visible in `/api/status` | `director/nedle2.py` |
| **A-012** | Absolute volume uses Core Audio if `pycaw` is present, else relative key presses (approximate) | `computer/windows.py` |
| **A-013** | Context budget: 4 chars/token; safety + user blocks are never trimmed | `context/builder.py` |
| **A-014** | IPC = stdlib `http.server` + SSE instead of FastAPI/WebSockets | `core/ipc/server.py` |
| **A-015** | Providers without a resolvable vault secret are skipped during selection (no wasted attempts) | `models/gateway.py` |
| **A-016** | Capability existence is validated **before** the permission check (unknown capability = programming error, not a permission problem) | `computer/service.py` |
| **A-017** | Mission state must pass through `PLANNED` before `RUNNING` | `core/contracts.py` |
| **A-018** | Needle integration mirrors the official harness: `reset()` per request, read `function_calls`/`validation`/`confidence`, drop `ungrounded`/`negation` calls | `director/nedle2.py` |
| **A-019** | Confidence gate default **0.4** (the official production-contract value); below it, the decision is treated as a refusal and escalated to a remote model | `director/nedle2.py` |
| **A-020** | The director `SYSTEM` prompt stays short (4 clauses). Verified: a 6-clause prompt dropped the smoke pass rate 12/12 → 9/12. Disambiguation belongs in tool descriptions | `director/tools.py` |
| **A-021** | Device arguments are requested as the user said them ("phone") and canonicalised by GENIE; if omitted, GENIE infers the device from the user's words | `director/tools.py`, `director/nedle2.py` |
| **A-022** | `NEEDLE_TELEMETRY=0` is set on activation (no telemetry from GENIE installs) | `director/needle_runtime.py` |
| **A-023** | Official `fetch_library()` can return a 0-byte file via the HF xet bridge; the provisioner falls back to a direct HTTPS download of the same official wheel + sha256 verification | `director/needle_runtime.py` |
| **A-024** | Daemon boot provisions NEDLE2 in a background thread and then runs the routing smoke test; boot never blocks on a download. Fallback state is always visible in `/api/status` | `core/lifecycle.py` |
| **A-025** | Logs go to **stderr** so CLI commands emit clean JSON on stdout | `core/logging_setup.py` |
| **A-026** | Win32 APIs declare explicit `argtypes`/`restype`. On 64-bit Python, ctypes truncates unknown returns to 32-bit and silently corrupts handles (caused a real access violation in clipboard writes) | `computer/windows_api.py` |
| **A-027** | `SHFileOperationW` may report a non-zero code after successfully recycling an item → delete success is decided by **state**, not the return code | `computer/files.py` |
| **A-028** | Destructive-intent matching uses word boundaries and ignores `-Parameter` forms, so `Get-Date -Format o` is not treated as destructive | `security/trust.py`, `computer/shell.py` |
| **A-029** | `input.*` verification confirms **delivery to the OS**; the effect must be verified by an observable follow-up capability | `computer/verifier.py` |
| **A-030** | Event sources are polling-based (2 s) in v1 rather than WinEventHook / ReadDirectoryChangesW — simpler, sleep/resume-safe, cannot deadlock the daemon | `computer/events.py` |
| **A-031** | Isolation detection is **passive** (installed binaries/services on disk). GENIE must never require being allowed to execute `wsl.exe`/`dism` to boot | `computer/workspace.py` |
| **A-032** | File search with no root searches the user's usual folders (Downloads/Documents/Desktop), read-only, PTE-gated | `computer/executor.py` |
| **A-033** | Verification timing covers OBSERVE + VERIFY as one phase (observing the new state is part of verifying it) | `computer/executor.py` |
| **A-034** | A browser already holding our profile forwards the new launch to itself and never opens the debug port → each launch attempt uses a fresh profile directory and the next free port | `browser/service.py` |
| **A-035** | Browser navigation verification waits for **rendered content**, not just `readyState == complete` (SPAs report complete before rendering) | `browser/service.py` |
| **A-036** | Hinglish verb-last word order is normalised to verb-first ("notepad kholo" → "open notepad") before routing | `director/normalize.py` |
| **A-037** | SAPI is driven through the official COM typelib (`comtypes`). Raw ctypes vtable calls were abandoned: ISpVoice inherits ISpEventSource ← ISpNotifySource and a wrong index crashes the process | `voice/providers.py` |
| **A-038** | The VAD never calibrates its noise floor on a frame that is already above the current threshold | `voice/vad.py` |
| **A-039** | The SAPI voice object is shared and owned by one worker thread; `stop()` runs on that thread through a queue | `voice/providers.py` |
| **A-040** | `wait_until_done` polls the speaking flag instead of calling `WaitUntilDone`, so a wait can never block a purge | `voice/providers.py` |
| **A-041** | Async TTS keeps the turn in SPEAKING for the real audio duration, otherwise GENIE looks idle while it is still talking | `voice/pipeline.py` |
| **A-042** | Cloned voice profiles can be *registered* without consent but can never be activated until an active consent record exists | `voice/profiles.py` |
| **A-043** | The pipeline normalises any handler result (dict / ActionResult / to_dict) so voice works with every capability shape | `voice/pipeline.py` |
| **A-044** | Spoken numbers are converted to digits before routing ("volume thirty" → "volume 30") | `director/normalize.py` |
| **A-046** | `Page.setDownloadBehavior` (page session) is the reliable download-path control; the browser-level command is only a fallback, and Chrome may still use the profile default — GENIE watches both and reports the real location | `browser/service.py` |
| **A-047** | Chrome blocks downloads triggered without a user gesture, so `browser.download` dispatches a trusted `Input.dispatchMouseEvent` at the element's own rectangle (DOM-derived, not screen coordinates) | `browser/service.py` |
| **A-048** | A closed tab leaves a dead CDP connection behind, which makes every later call block until timeout → the cached connection is liveness-checked before reuse | `browser/service.py` |
| **A-049** | `/json/new` requires PUT on modern Chrome; the client tries PUT then falls back to GET | `browser/cdp.py` |
| **A-050** | Test HTTP servers must be threaded: Chrome keeps connections alive and a single-threaded server deadlocks the whole test session | `tests/e2e/test_browser_phase4.py` |
| **A-045** | The microphone-to-speaker SNR of the verification machine is 1.0 dB, so the automated acoustic loop cannot be used as proof; the owner runs `voice-e2e` once with their own voice | `docs/VOICE.md` §4 |
| **A-051** | Skill selection treats **intent as a gate**: with zero intent overlap the score is capped below the threshold, so an unrelated-but-recent skill can never be selected on baseline points alone | `skills/registry.py` |
| **A-052** | Withdrawing the active skill version clears the `active` flag and promotes the newest still-ACTIVE version; an archived/rejected row must never stay active | `skills/registry.py` |
| **A-053** | The raw statistics accumulators (`total_latency_ms`, `total_cost_usd`) are persisted; serialising only the derived averages silently reset the statistics on every save/load | `skills/models.py` |
| **A-054** | `search()` reads capabilities/plugins from **both** the explicit kwargs and the context dict; otherwise a caller passing `context={"capabilities": …}` silently disabled compatibility filtering | `skills/registry.py` |
| **A-055** | `SkillRunResult.status` starts at `"running"`, not `"failed"`. `run()` reads `status == "failed"` as "a step failed", so a `"failed"` default made **every** skill run short-circuit verification and report failure | `skills/runtime.py` |
| **A-056** | A sensitive **target field** taints the payload written into it, so a password typed into a password field is redacted even though the payload key (`text`) is not itself sensitive | `teaching/recorder.py` |
| **A-057** | Application resolution prefers the Windows system directories over a generic PATH hit: Git-for-Windows puts a POSIX `notepad` on PATH that shadows the real built-in and blocks forever on stdin | `computer/apps.py` |
| **A-058** | `target`/`title` stay **literal** for `application.*`/`window.*` steps. Parameterising them produced a mandatory `target` input and an `app_installed ${target}` precondition that could never be evaluated | `skills/learning.py` |
| **A-059** | The generalizer reconciles inputs after building steps: every `${var}` used in a step is guaranteed to be a declared input (required unless it has a default) | `skills/learning.py` |
| **A-060** | Inputs are derived from the variables **actually created**, not from parameter names — a literal `target` no longer demands a `target` input | `skills/learning.py` |
| **A-061** | When the demonstrated typing and the saved content are the same value, the generalizer reuses one `${text}` variable instead of inventing a duplicate `${content}` input | `skills/learning.py` |
| **A-062** | Verification substitutes the **whole** check spec, not just the target: `file_contains` was comparing against the literal string `"${text}"` | `skills/runtime.py` |
| **A-063** | `continue`/`resume` are command verbs, so Hinglish verb-last forms are reordered ("mera project continue karo" → "continue my latest project"). Without it the router saw a verb-last fragment and mis-routed it to `media.play` at 0.99 confidence — a **wrong action** instead of a safe escalation. | `director/normalize.py` |
| **A-064** | A post-routing **semantic guard** refuses a media/device action when the request has continuation semantics and no media grounding, even at 0.99 confidence. The media task is withdrawn, the decision is escalated and pointed at memory/mission retrieval, the refusal is audited (`route.semantic_guard`) and published as `POLICY_VIOLATION_BLOCKED`. | `director/semantic_guard.py` |
| **A-065** | Pre-pairing frames use a shared `UNPAIRED_SECRET`: the `pair_required` reply cannot be signed with a secret the node does not have yet. The node verifies with its pairing secret, then falls back to the handshake secret. | `devices/protocol.py`, `devices/node.py` |
| **A-066** | **A node cannot promote itself.** The manifest sent over `hello` carries no authority; `upsert()` preserves the stored trust tier (and the owner-known name/type) unless an explicit `trust_tier=` is passed. Otherwise any node could reset its own trust by reconnecting. | `devices/registry.py` |
| **A-067** | Pairing accepts declared capabilities, and an **unknown** capability surface defers to the node (the authority on its own capabilities) while a **declared** one is enforced by the service. | `devices/service.py` |
| **A-068** | `computer/state.snapshot()` reads `foreground_window()` **once**. Asking twice raced with the desktop: if the window vanished between the calls the second returned `None` and the snapshot raised `AttributeError` mid-action, failing a real command. | `computer/state.py` |
| **A-069** | The home bridge disables HTTP proxying. `urlopen` honours `http_proxy`, so a controller on the LAN was routed through a corporate proxy that answered 502 — a confusing failure that would also leak the request off-LAN. | `plugins/installed/home/adapter.py` |
| **A-070** | A plugin adapter receives the **fully qualified** capability name (`plugin.home.turn_on`) from the host, not the short name. | `plugins/installed/home/adapter.py` |
| **A-071** | The vision provider is consulted **once per motion event** (rising edge) with a minimum interval — not once per frame. Classifying every frame of someone walking past is exactly the continuous streaming the spec forbids, and would cost one model call per frame. | `perception/service.py`, `perception/motion.py` |
| **A-072** | A **neutral** `user_preference` (0.5) was used as a literal multiplier in a multiplicative score, silently halving *every* notification. Neutral now maps to ×1.0; the owner can suppress a class but cannot inflate one. | `proactive/scoring.py` |

---

## OPEN — owner input needed

| ID | Sawal | Kyun zaroori | Blocking? |
|---|---|---|---|
| **D-036** | Kaunse providers ke API keys hain? (UI → Providers → Save key se add ho jaayenge) | Live model calls; abhi mock provider active | No — degraded mode works |
| **D-037** | Voice stack provider (STT/TTS) choice for Phase 3 | Phase 3 scope | No |

### Answered

| ID | Status | Answer |
|---|---|---|
| **D-035** | ✅ ACCEPTED (implemented) | **Official Cactus Needle 2 runtime**, provisioned automatically — no manual paths. Order: official Python `cactus-needle` → official native engine → localhost server only as fallback. GENIE-managed dir `%LOCALAPPDATA%\GENIE\runtime\needle2\`. Installer detects CPU arch, installs/bundles the matching runtime, verifies checksum/version, initializes, runs a routing smoke test, and exposes health/version through `DirectorProvider`. Nothing Needle-specific outside `director/`. **Result: engine 2.0.4 loaded, smoke 12/12.** |
| **D-038** | ✅ ACCEPTED | **Hinglish normalisation layer in front of the director.** Needle 2 is English-first and correctly refuses Hinglish command words; GENIE maps command words/particles to English (`director/normalize.py`) and NEDLE2 still makes the routing decision. Values (song/file/app names) keep their original casing; the original text is preserved for trace/audit/memory. |
| **D-039** | ✅ ACCEPTED | **A NEDLE2 tool call is a routing decision only.** No execution, no completion claims, no memory writes (proposes only). Services own truth. Enforced by tests (`test_needle_never_claims_completion`). |
| **D-040** | ✅ ACCEPTED | **Browser provider = built-in CDP** (stdlib WebSocket client). PinchTab stays an optional external provider behind the same `BrowserProvider` contract. | Embedding PinchTab would add a Bun/Node runtime to a daemon whose purpose is being light on low-end Windows machines |
| **D-041** | ✅ ACCEPTED | **UI Automation is an optional, lazily imported provider** (`pywinauto`). Core stays dependency-free; without it, semantic targeting degrades to keyboard/accelerators and raw input. | Add-Type/COM-UIA paths are blocked or impractical; an optional provider keeps the core clean |
| **D-042** | ✅ ACCEPTED | **COMPLETED requires verification.** No computer action may be reported complete because the right capability was generated: PLAN → ACT → OBSERVE → VERIFY → RECOVER, and a capability with no registered verifier can never complete. | Owner requirement — this is the whole point of Phase 2 |
| **D-043** | ✅ ACCEPTED | **The routable NEDLE2 catalogue is kept small (14 tools).** Measured: 13 tools → smoke 12/12, 20 tools → 9–11/12. Capabilities outside the catalogue remain available and are reached through a mission / remote model. | Routing quality beats local coverage; forcing the tiny router to cover everything produced worse decisions |
| **D-044** | ✅ ACCEPTED | **Deterministic director fast paths are allowed** for unambiguous inputs (web addresses today: `fast-path:web-address`). NEDLE2 still routes everything else. | The model cannot separate "open youtube.com/x" from "open notepad"; a URL is not a judgement call |
| **D-045** | ✅ ACCEPTED | **Voice providers are optional and swappable** behind five contracts (input / STT / realtime / TTS / output). Windows SAPI is a real fallback provider, not the architecture. | The owner must never be locked to one speech vendor |
| **D-046** | ✅ ACCEPTED | **A dedicated worker thread owns the SAPI COM object**; commands (speak/stop) are serialised through a queue. | COM apartments are per-thread and UIA switches callers to STA — cross-thread use blocks forever (A-039/A-040) |
| **D-047** | ✅ ACCEPTED | **Barge-in is implemented through the turn manager**, so it applies to every provider: VAD speech-start while SPEAKING → purge TTS, stop playback, mark the turn interrupted, keep the conversation state. | Owner requirement: barge-in is not a polish feature |
| **D-048** | ✅ ACCEPTED | **Speech never decides anything.** Transcripts go through the same director/trust/execution/verification/audit path as text; only the result is spoken. | Voice must not become a permission bypass |
| **D-049** | ✅ ACCEPTED | **Real offline STT = Vosk/Kaldi**, used as the default recognizer when no cloud key exists. Windows SAPI recognition was attempted first but its event wiring is unusable from this environment (no typelib load; .NET/Add-Type and reflection assembly loading are blocked by host policy). | The owner required real STT before Phase 3 could be called green; Vosk needs no key and no vendor |
| **D-051** | ✅ ACCEPTED | **Plugins run in separate host processes** and speak a line-delimited JSON protocol; the daemon supervises them (timeouts, crash counter, restart, disable). Plugin installation grants nothing. | A plugin must never be able to take GENIE down or reach its databases |
| **D-052** | ✅ ACCEPTED | **Two-gate plugin permissions**: PTE scope `plugin:<id>:<cap>` *and* the plugin's declared permissions, which stay denied until the owner grants them explicitly. | "Installation must not silently grant filesystem/network/device access" (owner requirement) |
| **D-053** | ✅ ACCEPTED | **Plugins are an accelerator, never a requirement.** The chain stays plugin → OS automation → UIA → vision → raw input. | GENIE must still operate unknown software |
| **D-054** | ✅ ACCEPTED | **Browser waits are condition-based** (`browser.wait` with element/visible/url/text/dom/network conditions + timeout). Fixed sleeps are only sub-second settles inside an action. | The owner explicitly forbade sleep-based synchronisation |
| **D-055** | ✅ ACCEPTED | **Downloads/uploads go through artifact handling** and are recorded with url, path, mime, bytes, sha256, mission and timestamp. | Provenance for anything GENIE brings onto the machine |
| **D-056** | ✅ ACCEPTED | **Untrusted content is tagged, scanned and fenced** (`security/injection_guard.py`); high-risk capabilities are blocked while tainted unless the user confirms, and secrets are never reachable. | Website text must never become GENIE authority |
| **D-050** | ✅ ACCEPTED | **Audio preprocessing is a required pipeline stage** (DC removal, high-pass, bounded AGC with the applied gain reported). | Real microphones deliver peaks around −36 dBFS; without AGC a clear sentence is "heard" but not understood |
| **D-057** | ✅ ACCEPTED | **A demonstration is not a skill.** A taught demonstration and a successful mission are both only *inputs*: they become a candidate, are generalised into a structured procedure, and may become ACTIVE **only** after the sandbox validator passes a real run. A raw mouse/keyboard recording is never saved as a skill. | Owner-mandated hard rule for Phase 5 |
| **D-058** | ✅ ACCEPTED | **A skill is a structured procedure, not a prompt or a macro.** Steps are capability invocations with declared inputs, preconditions, variables, verification and failure paths; environment values are never baked in. | Prompts cannot be verified; macros break on the first layout change |
| **D-059** | ✅ ACCEPTED | **The skill registry owns selection.** NEDLE2 receives exactly one generic tool (`skill_execute(goal)` → `skill.execute`); individual skills are never exposed as tools, and a below-threshold match means GENIE simply plans normally. | A 14 MB router model cannot carry an unbounded tool list, and selection must be testable in one place |
| **D-060** | ✅ ACCEPTED | **The skill runtime orchestrates, it does not bypass.** Every step runs through the same capability worker, so PTE, plugins, the computer engine, locks, verification and audit all still apply; a skill may never invoke `skill.execute`/`skill.search`. | A learned artifact must not become a permission shortcut |
| **D-061** | ✅ ACCEPTED | **A user correction is evidence, never a silent rewrite.** Fixes are appended to `skill_corrections`; the active skill document is left byte-identical and a new version must be created deliberately. | Silent mutation of a verified procedure destroys reproducibility |
| **D-062** | ✅ ACCEPTED | **Verification decides success, not step dispatch.** A skill only counts as successful when its declared verification holds against the real machine state; otherwise the run is reported as a failure and the statistics record the failing step. | "The steps were issued" is not "the work was done" |
| **D-063** | ✅ ACCEPTED | **Teaching redaction happens at capture time.** Secrets never enter the recording, and a sensitive target field taints the payload written into it. | Redacting at export means the secret already exists in memory and in logs |
| **D-064** | ✅ ACCEPTED | **Routing is validated after the model, not trusted from it.** A deterministic semantic guard (`director/semantic_guard.py`) sits around NEDLE2 output: a media/device action is only accepted when the request (or live context) carries actual media grounding. Continuation semantics — project/mission/memory/skill continuation — are not media grounding, and **high model confidence never overrides an incompatible intent**. A refused route is replaced by the correct intent (memory/mission retrieval) plus escalation, is audited, and is published on the event bus. | NEDLE2 answered "mera latest project continue karo" with `media.play` at ~0.99 confidence. A 14 MB router is fast, not authoritative; making its prompt longer does not make a statistical model a correctness guarantee |
| **D-065** | ✅ ACCEPTED | **The guard is a pure, idempotent function applied at both boundaries**: inside `NeedleDirector.classify` (so every consumer of NEDLE2 receives validated output, including the routing smoke test) and again in the orchestrator (so every director, including the heuristic fallback, is covered). Same single implementation — defence in depth, not a second guard. | The failure must be impossible to reintroduce through a new call path |
| **D-066** | ✅ ACCEPTED | **A device is the same capability contract over a channel.** `media.next(phone_main)` goes through the identical path as a local capability: PTE scope check, verification, audit. The node dials the daemon (no inbound port, no router config, no NAT traversal on LAN), and a relay later becomes a transport behind the same interface — not a second protocol. | Device-agnosticism (docs/DEVICES.md) only holds if there is one contract, and NAT/firewall problems disappear when the node initiates |
| **D-067** | ✅ ACCEPTED | **A device's trust is decided by pairing, never by the device.** The manifest a node sends over `hello` carries no authority; the stored trust tier is authoritative and only `pair()`/`set_trust()` may change it. Trust is also checked *before* capabilities, so an untrusted principal learns nothing about a device's surface. | A node that could promote itself by reconnecting would make the whole tier system decorative (A-066) |
| **D-068** | ✅ ACCEPTED | **Pairing grants nothing.** A pairing code establishes a secret; every capability family still needs an explicit `device:<id>:<family>` grant. | Same rule as plugins (D-052): installation/connection is not authorisation |
| **D-069** | ✅ ACCEPTED | **A device result is success only when it is verified.** A node that cannot observe its own effect must report `verified = false` — the Android media key is exactly such a case, and it says so rather than claiming success. | "Accepted" is not "done" (same rule as D-062 for skills) |
| **D-070** | ✅ ACCEPTED | **Offline work is queued with a TTL and expires.** A device that is away may receive queued work when it returns, but never stale work: an expired command is dropped, not delivered late. | A command the owner issued an hour ago may be actively wrong when it finally lands |
| **D-071** | ✅ ACCEPTED | **An unknown capability surface defers to the node; a declared one is enforced.** If a device has never connected it has declared nothing, so the service queues and the node (the authority on its own capabilities) validates on arrival. Once a surface is declared, the service enforces it. | Otherwise work the owner legitimately queued for a device that will connect later would be refused (A-067) |
| **D-072** | ✅ ACCEPTED | **Unbuilt code is not a completed feature.** The Android node is a complete protocol implementation but cannot be compiled here (no JDK/Kotlin/Gradle/Android SDK), so it is recorded as *implemented, unverified* while the mesh it plugs into is recorded as *verified*. A pinned protocol conformance vector lets a build be checked against the Python implementation byte for byte. | The project rule "do not fake completion" applies to the node exactly as it does to a capability |
| **D-073** | ✅ ACCEPTED | **The final Android application is DEFERRED BY OWNER — an intentional roadmap decision, not a failure and not a blocker.** Phase 6 Device Mesh Core is GREEN. The Kotlin node stands as a foundation; the app is built later as its own project against the already-frozen device contract, and must not require redesigning GENIE Core. | GENIE Core is still evolving; building the app now would mean rebuilding it later. Android is one GENIE *body*, not the brain |
| **D-075** | ✅ ACCEPTED | **Peripheral writes must be idempotent; `toggle` is not offered.** `relay.set` and the home plugin's `turn_on`/`turn_off`/`set_brightness` are idempotent, so a mesh retry is harmless. A toggle cannot be retried safely — a retried toggle flips a physical switch — so it is deliberately absent from both surfaces. `relay.pulse` exists because a pulse is genuinely needed, is asked for by name, and is bounded (10 s max). | A light turned off by a retry is a bug the owner cannot debug |
| **D-076** | ✅ ACCEPTED | **A write is verified only when the backend can read back.** sysfs can read an output pin, so GPIO writes report `verified: true`; a USB relay board cannot, so it reports `verified: false`. Claiming success for an unobservable effect would be a lie. | Same rule as D-062/D-069, applied to hardware |
| **D-077** | ✅ ACCEPTED | **The test peripheral backend is never auto-selected.** `select_provider()` refuses to pick it; it must be passed explicitly. A deployment must never silently believe it controlled hardware. | The rule "do not fake completion" applies to backends, not just to features |
| **D-078** | ✅ ACCEPTED | **GENIE speaks one interface to a home controller; the controller owns the appliances.** The bridge is a first-party plugin, so it inherits process isolation, two-gate permissions, timeouts and audit from Phase 4, and never receives GENIE's vault or databases. Credentials come from the plugin workspace or the environment, never from the repo. | docs/DEVICES.md: GENIE will not implement every bulb protocol |
| **D-079** | ✅ ACCEPTED | **A camera is never streamed, and the vision model is consulted once per *event*.** Motion is decided locally, in memory, by frame differencing; a frame reaches a vision provider only on the rising edge of motion, at most once per event, with a floor between events. A bounded session (≤120 frames, ≤300 s) makes "on demand" checkable. | The spec forbids 24/7 cloud streaming, and one call per frame is streaming by another name (A-071) |
| **D-080** | ✅ ACCEPTED | **Private zones ship with sensing OFF and cannot be enabled implicitly.** `bedroom` and `bathroom` default to camera off, wake-only microphone, motion off, short retention. The owner can enable them — the rule is "never implicit", not "never allowed". Bathroom is included because the principle is private space, not the literal word bedroom. | A default that leaks is a default that is wrong |
| **D-081** | ✅ ACCEPTED | **A camera fails closed when its mandatory indicator cannot be shown.** No indicator → no activation → no session. | A silent camera is worse than no camera |
| **D-082** | ✅ ACCEPTED | **Fusion degrades gracefully and names what is missing.** A missing or failing sensor never stops fusion, but the resulting `EnvironmentState` carries `degraded` and the list of unavailable sensors. A perception failure never breaks a turn. | Confident nonsense is worse than admitting ignorance |
| **D-083** | ✅ ACCEPTED | **No vision provider means no classification — not a guess.** Without a configured vision model the event stays `motion_detected` with `vision_skipped`; it is never upgraded to a fabricated `person_entered`. The vision path reuses the existing model gateway rather than a second provider stack. | The rule "do not fake completion" applies to inference too |
| **D-084** | ✅ ACCEPTED | **The proactivity score is multiplicative, and the interruption factors are inverted.** `urgency × relevance × confidence × (1 - current_task) × (1 - interruption_cost) × preference`. Multiplication means one fatal factor (irrelevant, or deep in someone's flow) beats three mediocre ones. An additive score would let mediocre signals outvote a fatal one. | "Speak every 20 seconds" is robotic; the spec demands a decision function, and multiplication is what makes it a *decision* |
| **D-085** | ✅ ACCEPTED | **Duplicate suppression yields to escalation.** A repeat of the same event class inside the window is withheld unless its score beats the previous by a margin. Suppressing an escalating problem is how a warning becomes useless. | The owner must be able to trust that silence means "nothing new", not "nothing worse" |
| **D-086** | ✅ ACCEPTED | **Quiet hours clamp; they never silence an urgent override.** Quiet hours reduce the intrusiveness of an outcome and can never make it *more* intrusive than the score earned. Only an explicit `urgent_override` (with the class on the whitelist) pierces them. | A destructive action at 2am must still be visible; a hallway motion event must not be |
| **D-087** | ✅ ACCEPTED | **A destructive step is flagged before it runs, not after.** The orchestrator pre-scans the plan and consults the proactive service, so the warning is timely by construction and travels with the turn result. | A warning that arrives after the file is gone is a log line, not an intervention |
| **D-088** | ✅ ACCEPTED | **Every notification is explainable.** Each one stores its score, every factor, the source event and the mission; `explain()` answers "why am I seeing this?". | An unexplainable notification trains the owner to ignore notifications |
| **D-074** | ✅ ACCEPTED | **Standing rule for Android-only requirements: park and continue.** When a later phase needs something only the unfinished Android app can provide, record the interface/contract requirement in `docs/ANDROID_DEFERRED.md`, implement the PC/local/test side now, and keep moving. It is never raised as a blocker. | Otherwise every later phase would stall on an app that is deliberately postponed |
| **D-089** | ✅ ACCEPTED | **Agents request a capability, never a hardcoded provider.** `AgentDefinition.model_requirement = {capability, quality}`; the `ModelGateway` maps it to a priced tier. An agent must not carry a vendor name unless the owner configured one. | Vendor lock-in would freeze the team design; escalation and failover need provider-independence |
| **D-090** | ✅ ACCEPTED | **Generated agents cannot grant themselves capabilities — PTE stays authoritative.** The factory proposes `tools`/`scopes`; the trust engine decides. A generated agent requesting `filesystem.delete` is recorded as a denial, not granted. | A self-elevating agent is a privilege-escalation hole |
| **D-091** | ✅ ACCEPTED | **The golden missions are verified with a real local executor, not stub strings.** `LocalExecutor` writes files, runs a real `pytest` subprocess and posts real mailbox/blackboard messages; a broken module fails the test and the reviewer rejects it. | A test that returns `{"ok": True}` proves nothing; the owner explicitly required genuine evidence |
| **D-092** | ✅ ACCEPTED | **Failover is driven by the service, not the orchestrator's worker-retry.** Provider A dying mid-mission triggers `service.failover` (snapshot → circuit breaker → continuation packet → Provider B); the orchestrator's retry reassigns a *worker*, the service switches the *provider*. | Confusing the two would loop recovery on a dead vendor |
| **D-093** | ✅ ACCEPTED | **The continuation packet is structured and bounded, never a transcript dump.** It carries objective, plan, completed/pending steps, decisions, artifact references and known errors — enough to continue, not the whole mission replayed. | A transcript dump defeats the "no giant transcript" rule and wastes tokens on resume |
| **D-094** | ✅ ACCEPTED | **The team-size ceiling is enforced at the orchestrator, not just the planner.** `MAX_TEAM_SIZE = 6`; `add_member` refuses beyond it. Spawning many agents is never the default — the factory starts from the minimum useful team. | "multi-agent" must mean "useful", not "many" |
| **D-095** | ✅ ACCEPTED | **Every supplied repository ends in exactly one documented disposition.** `docs/REPO_UTILIZATION_AUDIT.md` records purpose, capabilities, GENIE equivalent, disposition (direct reuse / adapted / external service / reimplemented / deferred), integration point and target phase for all 22 distinct repos. Nothing is silently ignored. | Uploaded material must become accounted-for capability, or be explicitly deferred with a reason |
| **D-096** | ✅ ACCEPTED | **Never replace a stronger mature system with a weaker home-made clone — and never force bad code in.** Where GENIE is demonstrably better (hermes-agent skills, deepseek-harness plugins, munder-difflin orchestration, PinchTab browser) the repo is recorded as reimplemented with parity proven by tests. Where the repo's engine *is* the value (ComfyUI node engine, n8n workflows, Kronos model) GENIE **wraps** it as an external service instead of rewriting it. | Architectural purity must not cost capability |
| **D-097** | ✅ ACCEPTED | **There is ONE GENIE Agent Runtime, not five.** Supplied agent systems are mined for their strongest component (agency-agents → role templates, Youtu-Agent → experience loop, spec-kit → coding workflow, enoch → governed self-evolution) and folded underneath the existing runtime. No competing agent loops. | "composite, not competing" |
| **D-098** | ✅ ACCEPTED | **Personas are role templates, never permanent agents.** The 264-entry agency-agents corpus is vendored as *data* (`data/personas/`); `AgentFactory.create_from_persona()` resolves one persona and builds exactly one task-specific agent on demand. A missing corpus degrades to the built-in worker rather than failing. | Hundreds of permanent agents would be dead weight |
| **D-099** | ✅ ACCEPTED | **GENIE gets its own computer: an isolated, mission-owned workspace.** `computer/workspace.py` provides identity, per-mission scoped trees, a dedicated browser profile (never the user's Chrome), a workspace-rooted shell that refuses path escapes, dry-run-by-default cleanup and a storage quota. Agents do autonomous browsing/research/building there instead of hijacking the owner's foreground desktop; crossing to the USER DESKTOP still needs an explicit PTE scope plus confirmation. | Autonomous work must not disturb the owner's machine, and must not be able to escape its sandbox |

## Rejected / considered-and-dropped

| ID | Idea | Kyun nahi |
|---|---|---|
| R-001 | Local embedding model | Only NEDLE2 is local (D-003) |
| R-002 | Neo4j day one | Relational graph tables suffice |
| R-003 | NEDLE2 as memory truth | Hallucination corrupts missions |
| R-004 | Live NEDLE2 self-training in production | Versioned offline training + regression only |
| R-005 | Speaking every 20 seconds | Robotic |
| R-006 | Copy-paste repo soup | Architectures would dictate GENIE |
| R-007 | 113 empty micro-modules | Documentation overhead without isolation value (D-029) |
| R-008 | Ollama / heavyweight local model server for NEDLE2 | Explicitly rejected in D-024 |
| R-009 | Business logic inside the Electron renderer | Would block the future 3D UI swap (D-031) |
