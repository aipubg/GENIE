# GENIE Vision and Reality Audit

**Date:** 2026-09-22  
**Authority order:** current source and packaged runtime first; historical chats and old reports second.  
**Verdict:** GENIE is already much more than a skeleton, but the whole Jarvis-level vision is **not** complete yet. The core runtime, provider/model system, director, missions, agents, browser/computer services, security controls, voice pipeline, and WPF desktop client are substantial. The biggest remaining work is to close the connections between those parts so they behave as one dependable personal operating layer.

## 1. What was scanned

This audit used the current `E:\G3\GENIE` source tree, its packaged backend runtime, focused tests, WPF build, runtime-sync manifest, duplicate scan, the four owner-provided onboarding/history documents, and the owner-provided shared chat history.

I inspected code and contracts across the runtime layers: models, providers, NEDLE2/director, context, memory, orchestrator, missions, agents, competition, experience, browser, computer, voice, security, WPF UI, packaging, and scripts.

This is a deep architecture and behavior scan, not a claim that every generated cache, binary byte, private vault secret, browser profile, or third-party account was read. Secrets were intentionally not read or copied.

## 2. The exact product you are building

You are not trying to make a normal chat app. You are building one persistent personal AI operating layer called **GENIE**.

The owner should be able to speak or type naturally, for example:

> "Open YouTube in my other browser and play a sad rain song."

GENIE should understand the owner, decide whether this is a normal conversation, a small verified action, or a durable mission, select the right model and tools, act on the PC, observe the real result, verify it, retain only useful memory, and report the truth.

Your intended system has these ideas:

1. A top-level pool of any number of models from OpenRouter, OpenAI-compatible endpoints, local models, or other providers. Two, ten, or one hundred models must be data, not source-code changes.
2. A small local NEDLE2/Needle-style director that classifies work cheaply. It should decide the task category and constraints, not become a second assistant or select a model by hard-coded name.
3. A Model Gateway that chooses the actual candidate model using capability, credential availability, provider health, cost/latency policy, and failover.
4. A Context Compiler that filters relevant memory, active project state, mission checkpoints, recent conversation, verified artifacts, owner preferences, and corrections before a model sees a request.
5. One main assistant that can answer, create missions, create temporary specialist agents, use skills, control browser/computer tools, and verify external actions.
6. Durable missions for multi-step, scheduled, risky, or deliverable-producing work.
7. Multi-agent work and carefully controlled competition where proposals can compete, a verifier chooses the best proposal, and only one winner performs an external side effect.
8. Learning from verified outcomes, owner corrections, latency, cost, and failures. This must be evidence-based operational learning, not uncontrolled self-training.
9. A future asset/account registry for owner-approved browsers, identities, social accounts, devices, and permissions. This is required before permanent multi-account automation can be called real.
10. A WPF desktop shell that is thin and dependable. The backend remains the authority for state, security, routing, mission truth, and audit.

That is a coherent vision. The important correction is that it must remain **one brain and one execution truth**, not a growing pile of unrelated agents, routers, memories, and UI-only state.

## 3. Correct runtime shape

The Provider/Model Pool can be the first major page in the UI. Runtime order should still be based on responsibility, not visual position:

```text
Typed input / Voice input
  -> identity, permission, and safety policy
  -> lightweight intent classification and one context retrieval plan
  -> Context Compiler
  -> NEDLE2 classification (optional local director)
  -> Model Gateway selects an eligible exact model
  -> assistant / agent / mission planner
  -> tools and skills
  -> observe -> verify -> receipt/audit
  -> owner response + durable memory/experience update
```

### What each layer must own

| Layer | Owns | Must not own |
|---|---|---|
| Provider registry | Provider endpoints, models, capabilities, secret references | The final model-routing decision |
| NEDLE2 | Intent, task class, rough complexity, required capabilities | API keys, provider health, exact model choice, success claims |
| Model Gateway | Eligibility, health, budget, capability matching, retry/failover | Memory retrieval and mission lifecycle |
| Context Compiler | One bounded, attributed context packet | Tool execution or changing memory on its own |
| Orchestrator | Conversation/action/mission decision and one coherent turn | Direct UI state ownership |
| Mission runner | Durable plan, idempotency, resume, checkpoints | Duplicate external execution |
| Competition service | Isolated candidates and winner selection | Multiple live actions against the same account/browser |
| Verification/audit | Evidence, receipts, success/failure truth | Optimistic success language |

This division prevents the most dangerous future failure: two components both believing they own routing, memory, or execution.

## 4. Current completion truth

| Area | Current source/runtime reality | Status |
|---|---|---|
| Multiple providers and many models | Data-driven registry, provider catalog, custom OpenAI-compatible provider flow, model add/remove, vault-backed secrets, health/eligibility, and model-level failover exist. | Substantial; one important custom-provider update bug remains. |
| "Models first" UI | Current WPF source contains the custom provider and model management UI. Release binary contains expected current markers. | Present in source build; current owner-visible installed UI was not running during audit. |
| NEDLE2/local director | NEDLE2 provisions asynchronously, classifies requests, and has heuristic fallback. Packaged boot reached Needle ready. | Present and working as a classifier. |
| Gateway-selected exact model | Gateway has capability/credential/health selection and failover behavior. | Present; legacy role state should be simplified. |
| Context and memory | Long-term memory and a context builder exist. | Partial: duplicate retrieval and session-continuity gaps weaken the design. |
| Conversation, action, mission split | Orchestrator and durable mission services/runner/scheduler exist. | Substantial. |
| Multi-agent teams | Agent/team services and task-specific execution exist. | Present. |
| Competition | Candidate competition, blind verification, budget-aware candidate counts, cancellation, one winner commit, and experience recording exist. | Engine exists, but normal missions do not automatically use it. |
| Learning / reinforcement | Trajectories, group advantages, lessons, and experience storage exist. | Partial: current feedback is not wired into normal future routing/planning. |
| Browser and computer control | Browser/CDP and computer/PTE/verification services are substantial. | Present; not proof of permanent autonomous social-account control. |
| Voice | Packaged runtime imports `faster-whisper`; local multilingual model loaded successfully. | Core STT path is ready; live owner acceptance and typed/voice shared session need work. |
| WPF hang fixes | Historic SSE/UI blocking path now uses async line reading and off-UI-thread pumping; scrolling was deferred. | Source fixes are present. A specific current owner crash was not reproduced because no running client/crash dump was available. |
| Security and truth | Vault, trust checks, audit records, verification, and action receipts are part of the backend architecture. | Substantial, but every new automation path must keep using them. |
| Permanent multi-account autonomy | No complete asset/account registry with consent, scopes, rate limits, platform adapters, account state, and publish policy was found as a finished owner feature. | Not complete; do not describe it as delivered. |

## 5. What is demonstrably working now

The following evidence was collected from the current tree, not old claims:

1. Focused backend suite: **92 passed**. It covered voice/STT, competition, provider failures, missions, provider settings contracts, and chat streaming.
2. WPF Release build: **0 warnings, 0 errors**.
3. Build identity check: **17/17 passed**. The source-built WPF DLL contains current UI markers such as `Custom / OpenAI-compatible`, `Mission specialists`, `RingsHost`, and `Build identity`.
4. Runtime source-sync check passed: **238 files** match the packaged backend manifest at source commit `6f2bb7ec217a`.
5. Packaged embedded Python imports `faster-whisper 1.2.1`, `ctranslate2 4.8.2`, and `av 18.1.0`.
6. The packaged `FasterWhisperSttProvider` loaded the local `faster-whisper-small` model successfully with `ready: true` and multilingual auto-detection.
7. A packaged backend boot reached ready state, reported its packaged backend root and current manifest commit, started NEDLE2, and selected the multilingual voice recognizer.
8. The existing duplicate audit reports no duplicate service wiring other than the explicitly accepted NEDLE2 hot-swap. It does not detect every kind of duplicate described below.

These are strong implementation signals, but they are not the same as owner acceptance. A passing source build cannot prove that an old shortcut, old installer, missing model, provider credential, real microphone, browser profile, or user workflow behaves correctly on the owner desktop.

## 6. Confirmed wrong connections and duplicates

### P1 - `GENIE_APP_DATA` is documented but ignored

`backend_entry.py` says `GENIE_APP_DATA` can override mutable app data. The current path implementation only honors the absolute `GENIE_DATA_DIR` variable.

Result: an attempted isolated packaged-runtime validation using `GENIE_APP_DATA` used the normal local GENIE data directory instead. This is a real correctness and test-isolation bug. It can mix release tests with owner state and makes documentation untrustworthy.

**Fix:** choose one supported variable, preferably `GENIE_DATA_DIR`, document it everywhere, and add a startup assertion/test that reports the resolved data directory. If backward compatibility is needed, map `GENIE_APP_DATA` to `GENIE_DATA_DIR` once, with an explicit deprecation notice.

### P1 - Saving an existing custom provider leaves endpoint details stale

In `SettingsViewModel.SaveProviderAsync`, a custom ID is generated from the display name. If that ID already exists, the code skips `AddProviderAsync`, then saves only the key and selected models. It does not update `base_url`, headers, auth scheme, discovery URL, or timeout.

Result: reusing a provider name can silently keep the old endpoint while the owner believes the new URL/settings were saved. This directly matches the reported custom-provider confusion.

**Fix:** when the ID exists, either:

1. send a full `UpdateProviderAsync` patch before storing the key/models; or
2. clearly redirect the owner to Edit Provider; or
3. require a different provider identity.

The best UX is one provider endpoint with many models. The endpoint should be saved once; model selection should be separate and repeatable.

### P1 - Typed and voice conversations do not actually share a session

The WPF client sends `session_id = "desktop"`. The voice pipeline creates a new `CallContext(person_id="owner")`, then the lifecycle voice handler calls `chat(...)` without passing the voice context's session ID. That falls back to `"ui"`.

Result: a comment says typed chat and voice share transcript history, but they use different session IDs. The in-memory transcript is also lost on daemon restart.

**Fix:** introduce one owner conversation/session identity managed by the backend. Pass it through WPF, voice, mission follow-ups, and API. Persist bounded session summaries/turns separately from long-term memories.

### P1 - Competition is not connected to ordinary mission execution

The competition service and API endpoints exist, with good unit coverage. However, the normal mission runner, mission service, and orchestrator do not call it.

Result: "agents always compete for every task" is not happening today. This is a capability available through a separate path, not the normal execution policy.

**Fix:** add a policy gate before a mission step. It should choose `single`, `parallel-read-only`, or `competition` based on task value, risk, cost, and whether an external action is pending.

### P1 - Experience is recorded but not a closed learning loop

`ExperienceBank` records trajectories and can calculate group advantages and persisted lessons. Current production call sites record outcomes from agent teams and competition. The source scan found no normal production caller that retrieves those lessons and feeds them into future Gateway, planner, or agent decisions.

Result: this is valuable evidence storage, but not yet reinforcement learning or automatic improvement.

**Fix:** have the Context Compiler or routing policy retrieve only evaluated lessons for the current task family. Every use must include confidence, sample size, time window, and a rollback switch.

### P2 - Memory is retrieved twice and rendered twice

`Orchestrator._reason_messages` queries memory using `decision.memory_query`, puts plain values in `environment["relevant_memory"]`, then calls `ContextBuilder.build`. `ContextBuilder.build` performs another memory query using the user text and adds another memory section.

Result: two potentially different retrievals consume time/tokens and can repeat or conflict. The first is flattened into a generic environment list, so it loses memory type, entity, score, and provenance.

**Fix:** create one `ContextCompiler.compile(...)` call. It should retrieve once using a normalized query/intent, receive typed memory hits, and build one bounded packet with source metadata.

### P2 - Recent-turn support exists but is not wired into model context

`ContextBuilder.build` accepts `recent_turns`, but the current orchestrator caller does not pass it. A separate global in-memory `_HISTORY` exists only for a few deterministic "what did I just say?" patterns.

Result: normal model requests do not receive a coherent recent conversation excerpt, and restart loses the transcript.

**Fix:** make session transcript retrieval a first-class Context Compiler input, with a token budget and persistent summary/checkpoint layer.

### P2 - Legacy model roles overlap the intended automatic Gateway

The registry still stores `everyday`, `fast`, and `deep_work` roles, while the Gateway's candidate selection does not use them. The current UI build intentionally hides the old role controls.

Result: the data model still preserves a second routing concept that is not the active runtime authority.

**Fix:** either make roles an explicit, documented fallback policy consumed by Gateway or migrate/sunset them. Do not let hidden legacy role state compete with automatic routing.

### P2 - Runtime sync proves source files, not dependency inventory

`scripts/sync_backend_runtime.py --check` hashes the packaged application source files. It does not hash `requirements-runtime.txt`, enumerate embedded site packages, or prove critical imports/model loads.

Result: the current package happens to contain the multilingual voice dependencies, but a future source dependency change could be missed by the normal sync check.

**Fix:** include requirements hash, installed package/version inventory, and critical import/model-load probes in the runtime manifest and release gate.

### P3 - Duplicate `agents` key in daemon status dictionary

`core/lifecycle.py` has the `"agents"` status entry twice in the same dictionary. Python keeps the latter, so behavior is unchanged, but the first is dead code.

**Fix:** remove one entry and add an AST/lint rule for duplicate dict literal keys. The current duplicate script is useful but does not catch this category.

## 7. Crash and responsiveness truth

### What is fixed in source

The historical WPF chat freeze had a credible root cause: synchronous `StreamReader.EndOfStream` behavior on the UI path during slow SSE. The current client uses `ReadLineAsync`, an off-UI-thread read pump, and an idle timeout. Chat scrolling was also deferred to avoid the previous WPF `ItemsControl` timing failure.

### What cannot be claimed yet

There was no active owner desktop client, no current listener on port 8787 before the packaged validation, and no current crash dump/event log tied to the reported crash. Therefore this audit cannot honestly claim that every current owner crash is fixed.

The latest source build is healthy, and the packaged backend reached ready state. That proves a lot, but it does not reproduce:

1. a real owner click path;
2. a slow or failing provider under the installed UI;
3. a live microphone turn in Hindi/Hinglish;
4. a real browser/account action; or
5. a fresh installer/shortcut launch.

### Remaining responsiveness review items

These are risks to test, not confirmed root causes of the user's current crash:

1. Startup code contains bounded `Thread.Sleep` retry behavior on the UI startup path. A stubborn old frontend can visibly delay launch.
2. Background activation/shutdown paths use synchronous UI dispatch patterns. They should prefer non-blocking `BeginInvoke` with dispatcher-shutdown guards.
3. A slow provider needs an end-to-end UI acceptance test: connect, send, stream, cancel, error, retry, and close while a request is active.

## 8. Voice: current truth and next acceptance gate

Voice should not pretend an English-only recognizer can understand Hindi/Hinglish. The current code correctly tries HTTP STT first, then multilingual `faster-whisper`, and only permits Vosk/file fallbacks for explicit development configuration.

The packaged runtime now has the required Python packages and loaded the local multilingual model successfully. This is a real improvement over an unverified source-only implementation.

Still missing before calling voice owner-ready:

1. Three real recorded/live Hindi or Hinglish commands with transcript evidence.
2. A full microphone -> STT -> normal GENIE turn -> verified PC action -> spoken reply acceptance test.
3. Same-session continuity between typed and spoken turns.
4. A clear UI state that distinguishes `model loaded`, `microphone available`, `speech recognized`, `action verified`, and `voice output played`.
5. Dedicated automated tests for `FasterWhisperSttProvider`, not only Vosk/file-fixture tests.

## 9. The correct competition design

Do **not** make every agent compete on every request. That would make simple tasks slower, costlier, and less predictable.

Use three modes:

| Task type | Mode | Example |
|---|---|---|
| Small, reversible, low-risk action | Single agent/model | "Set volume to 30" |
| Research, plan, coding, content where alternatives help | Parallel read-only proposals | "Compare three implementation approaches" |
| High-value work with a measurable verifier | Competition | "Produce a tested code patch" or "choose a verified research plan" |

For a competition:

```text
task policy -> isolated candidate A/B/C -> evaluator/verifier -> one winner
  -> one commit/external action -> receipt -> experience record
```

Rules that must never be broken:

1. Candidate agents may propose or operate in sandboxes only.
2. Browser clicks, posting, messaging, purchases, deletions, and account changes happen once, after a winner is selected and policy allows it.
3. A losing temporary candidate can be discarded, but its evaluated trajectory and lesson should remain in the experience store.
4. Do not automatically delete permanent agent profiles. Retire, disable, or deprecate them with lineage and rollback instead.
5. The verifier must use evidence: tests, receipts, screenshots/DOM evidence where allowed, checked files, or owner approval. It must not reward persuasive prose.

## 10. Reinforcement learning: the safe useful version

The right first version is **operational reinforcement**, not live model-weight training.

### Reward signal

For each attempted strategy/model/agent, record a structured outcome:

```text
reward = verified_success
       + owner_approval
       - owner_correction
       - safety_violation
       - verification_failure
       - latency_cost
       - money/token_cost
```

`verified_success` must come from a receipt or verifier, never from an agent saying "done".

### Phased implementation

1. **Instrumentation:** capture task family, context class, candidate model, provider, tools, plan, evidence, outcome, latency, cost, owner correction, and failure code.
2. **Offline evaluation:** replay logged work against a held-out evaluation set. Compare policy changes before they affect the owner.
3. **Contextual bandit routing:** for a known task family, choose among approved models/strategies using bounded exploration and the verified reward history.
4. **Shadow mode:** let challengers produce proposals silently; compare them with the active policy without allowing external actions.
5. **Promotion:** allow a challenger to become preferred only after minimum evidence, safety gate, benchmark improvement, and rollback record.

Never allow an agent to modify its own policy, tools, permissions, or model routing purely because it gave itself a high score. Self-modification belongs behind explicit proposals, tests, security checks, approval, version control, and rollback. Existing evolution/worktree ideas are the right direction for that boundary.

## 11. Memory and Context Compiler design

The phrase "all memory should filter into every model call" needs one important correction: sending all memory makes answers slower, more expensive, less private, and more confused.

The Context Compiler should choose only the smallest justified packet:

1. Current owner/session identity and permissions.
2. Active mission objective, current step, checkpoint, and blockers.
3. Recent conversation summary plus a small number of exact recent turns.
4. Owner preferences and stable facts relevant to the intent.
5. Project/workspace state: branch, changed files, test results, artifact paths, and verified receipts.
6. Relevant long-term memories with type, source, score, created time, and confidence.
7. Relevant evaluated experience lessons with sample count and confidence.
8. Explicit corrections/exclusions, for example: "do not use default browser".

Every item should carry provenance and a token budget. The final model sees context, not raw unbounded database rows.

Suggested contract:

```text
ContextCompiler.compile(request, session, mission, routing_intent) -> ContextPacket

ContextPacket:
  safety
  session_summary
  recent_turns
  mission_checkpoint
  workspace_receipts
  memories[]        # typed, scored, attributed
  experience[]      # evaluated lessons only
  environment
  token_budget_report
```

## 12. Browser, PC, and multiple account automation

The desired capability is reasonable, but it cannot safely be implemented as "GENIE permanently controls all my Instagram accounts" with only a browser tool.

Before that claim is made, add an **Asset and Account Registry**:

| Field | Why it is needed |
|---|---|
| Asset/account ID and owner label | Prevent wrong-account actions |
| Platform adapter and allowed browser profile | Prevent cross-profile leakage |
| Secret/session reference | Keep credentials out of prompts and logs |
| Allowed action scopes | Read, draft, publish, message, delete, billing, etc. |
| Approval policy | Which actions require owner confirmation |
| Rate limits and quiet windows | Avoid accidental spam and platform abuse |
| Verification rule | Define evidence of success for each action |
| Audit/receipt history | Make every action traceable and reversible where possible |

Start with owner-approved draft/read/prepare actions. Posting, messaging, deletion, payments, account settings, or irreversible social actions must be single-winner, verified, and policy-gated. Platform terms, authentication challenges, and rate limits remain real constraints rather than bugs GENIE should bypass.

## 13. Speed plan

The fastest reliable GENIE is not the one that launches many agents. It is the one that does the minimum justified work.

1. Use NEDLE2 or a lightweight local classifier only for cheap intent/complexity classification.
2. Perform memory retrieval once, cache short-lived routing/context results, and bound the packet.
3. Default to one eligible fast model for normal conversation and small actions.
4. Escalate to deeper models, agents, or competition only when policy says the expected benefit exceeds latency/cost.
5. Stream actual model tokens and show non-token progress honestly.
6. Cache provider health/eligibility and fail fast from known-bad credentials/endpoints.
7. Preload the local voice model only when it improves owner experience without making startup slow; otherwise lazy-load with truthful readiness state.
8. Run parallel work only for read-only proposals or isolated worktrees. Never parallelize the final external side effect.
9. Make every tool return an evidence receipt so retries do not repeat completed actions.

The immediate performance win is removing duplicate memory retrieval and replacing disconnected transcript handling with one Context Compiler.

## 14. Recommended implementation order

### Phase 0 - Fix correctness and make reality visible

1. Fix `GENIE_APP_DATA`/`GENIE_DATA_DIR` mismatch and add a resolved-data-dir status field.
2. Fix existing custom-provider save to patch endpoint details, then key, then models.
3. Make typed and voice routes use one backend-owned session identity.
4. Replace the two memory queries with one Context Compiler call and wire `recent_turns`.
5. Remove the duplicate `agents` status key and add a duplicate-dict-key lint check.
6. Extend package manifest/release verification to include requirements hash, embedded dependency inventory, and voice model-load probe.

### Phase 1 - Owner acceptance and reliability

1. Add a build identity/status screen that reports UI build, backend root, source commit, data dir, active STT, active model/provider, and degraded reasons.
2. Capture a reproducible slow-provider/chat cancel/close test from the actual WPF app.
3. Add a fresh install/shortcut launch test, rather than relying only on a source build.
4. Run real Hindi/Hinglish voice acceptance with microphone, verified action, reply, and same-session follow-up.
5. Add provider flow acceptance: new provider, multiple models, restart, edit endpoint, replace key, test, rediscover, delete.

### Phase 2 - Join agents, competition, and learning

1. Add a mission policy gate for `single`, `parallel-read-only`, and `competition`.
2. Make the mission runner consume only one winning action plan.
3. Feed evaluated experience lessons into routing/planning in shadow mode first.
4. Establish an evaluation suite and promotion thresholds before automatically preferring a challenger.

### Phase 3 - Personal operating layer expansion

1. Build the Asset and Account Registry with permission scopes and receipts.
2. Add safe browser/account adapters one platform/action class at a time.
3. Add durable project state, workspace receipts, and mission checkpoints to Context Compiler.
4. Add owner-controlled proactive behavior only after all action/notification policies are explicit.

## 15. Release acceptance checklist

Do not call a feature complete until its acceptance proof exists.

| Feature | Minimum proof |
|---|---|
| Provider/model pool | Add custom endpoint, save 3 models, restart, edit endpoint, test, delete; no secret echoed |
| Automatic routing | Logged NEDLE2 class, Gateway candidate reasons, fallback reason, actual selected model |
| Memory/context | One retrieval per turn, provenance visible in trace, typed and voice follow-up works after restart summary |
| Browser/PC action | Plan, permission, execution evidence, verification receipt, retry does not duplicate action |
| Competition | Candidates isolated, verifier proof, one external action only, loser lessons retained |
| Learning | Holdout evaluation improves without safety regression; rollback works |
| Voice | Three Hindi/Hinglish live commands and one multi-turn typed/voice continuity scenario |
| Desktop stability | Launch, close, slow stream, cancel, provider failure, restart, installer upgrade all pass on real owner machine |

## 16. Bottom line

GENIE already has a real technical foundation. It is not correct to call it only a mockup or a normal chatbot.

It is also not correct to say that every part of the final Jarvis-level vision is already complete. The largest gap is not "add more agents." It is to make the existing components share one truthful routing path, one context path, one session identity, one external-action commit point, and one evidence-based learning loop.

The immediate priority is:

```text
fix wrong connections -> prove owner workflows -> connect competition safely
-> close the learning loop -> add account autonomy behind strict policy
```

That order will make GENIE faster, safer, easier to debug, and genuinely more capable without creating a second uncontrolled system inside the first one.

## 17. Evidence notes

- Current source commit in packaged runtime manifest: `6f2bb7ec217abb58db0fe94f59c2bc7449f3e0c2`.
- Historical share link and pasted history were used for intent and migration context, not as proof of current implementation: <https://chatgpt.com/share/6ab1b997-262c-83e9-9861-ce72c9a9f4bc>.
- The source tree has pre-existing uncommitted work. This audit does not revert or overwrite it.
- The packaged runtime boot test was stopped after validation. Because `GENIE_APP_DATA` is currently ignored, that test exposed the data-directory override mismatch described above; future isolated tests must use the actual supported override until the bug is fixed.
