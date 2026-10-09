# GENIE — PHASE 5: SKILLS + TEACHING MODE

**Status: 🟩 COMPLETE** — implementation, real-machine tests and exit gate all pass.

> **Hard rule of this phase (owner-mandated):** *a demonstration is not a skill.*
> Nothing a user demonstrates, and nothing GENIE merely did once, becomes an active skill
> until it has been generalised into a structured procedure **and** passed validation against
> a real run. A raw mouse/keyboard recording is never saved as a skill.

---

## 1. What a skill is (and is not)

| | |
|---|---|
| **Is** | a structured, versioned, verifiable **procedure**: declared inputs, preconditions, capability steps, variables, verification, failure paths, provenance, statistics, scope |
| **Is not** | a prompt · a macro · a raw coordinate recording · a transcript |

Environment-specific values are never baked in. Steps refer to `${variables}`:

```json
{
  "skill_id": "notepad-dated-note",
  "version": 1,
  "status": "active",
  "scope": "user",
  "inputs": { "path": {"type": "string", "required": true},
              "text": {"type": "string", "required": true} },
  "preconditions": [ { "check": "app_installed", "value": "notepad" } ],
  "steps": [
    { "capability": "application.open", "params": {"target": "notepad"},
      "wait_for": {"condition": "window_present", "value": "notepad", "timeout_s": 20} },
    { "capability": "window.focus", "params": {"target": "Notepad"}, "max_retries": 5 },
    { "capability": "input.type_text", "params": {"text": "${text}"} },
    { "capability": "files.write", "params": {"path": "${path}", "text": "${text}"} }
  ],
  "verification": [ { "check": "file_exists",  "path": "${path}" },
                    { "check": "file_contains", "path": "${path}", "text": "${text}" } ]
}
```

Note what is **absent**: no coordinates, no window geometry, no absolute paths, no fixed
sleeps. That absence is exactly why the golden demo survives a moved/resized window.

## 2. The two ways a skill is born

```
successful mission ─→ candidate detector ─→ generalizer ─→ sandbox validator ─→ ACTIVE
user demonstration ─→ teaching session   ─→ generalizer ─→ sandbox validator ─→ ACTIVE
```

Neither path may skip the validator. `learn_from_mission` and `learn_from_demonstration`
both refuse to register anything that has not passed a real run.

### 2.1 Candidate detector (§5.10)
A mission only becomes a candidate when it is **multi-step**, **succeeded**, **verified**,
and **generalizable**. Read-only workflows are rejected (little value in saving them). A
trivial workflow is accepted only when the owner explicitly asked to remember it.

### 2.2 Generalizer (§5.8, §5.12)
Deterministic by default, so learning never depends on a provider being available. It:
* turns task-specific values into `${variables}` and leaves **configuration** literal
* keeps `target`/`title` **literal** for `application.*` / `window.*` (the target is the
  skill's identity, not a per-run variable)
* derives the input list from the variables that were *actually* created
* guarantees the invariant *every `${var}` in a step is a declared input*
* infers verification from the observable effect (and adds a content check, so a zero-byte
  file cannot pass)
* gives idempotent state-establishing steps (`window.focus`, …) a small retry budget
* optionally asks a large model to improve the abstraction (`refine_with_model`) — never required

### 2.3 Sandbox validator (§5.9)
Three stages: **structure** → **dry run** → **real run**. Only the real run can prove a
skill works, and success is decided by the skill's own verification, not by "the steps ran".

## 3. Teaching mode (§5.11–§5.13)

```
CREATED → RECORDING → STOPPED → ANALYZING → CANDIDATE_CREATED → VALIDATING → SAVED
                                                                    └─────→ REJECTED
```

The recorder captures **semantic** events (`app_launch`, `uia_invoke`, `typing`, `file_save`,
`browser_action`, `plugin_action`, semantic keystrokes). Raw coordinates are kept only as
flagged debug evidence and never become skill logic.

**Secret redaction happens at capture time, not at export.** A sensitive *target field*
taints the payload written into it, so typing a password into a password field is redacted
even though the payload key (`text`) is not itself sensitive. Card numbers, tokens, emails
and `password=…` assignments are redacted by pattern.

## 4. Runtime: how a skill executes (§5.5, §5.28)

Every step goes through **the same capability worker** as the rest of GENIE, so the
permission engine, plugin routing, computer engine, verification, locks and audit all still
apply. The skill runtime is an **orchestrator, not a security bypass**.

* preconditions are checked first; a missing one **rejects** the run (never blind replay)
* missing required inputs and unresolved variables are rejected before anything runs
* a skill may **never** invoke `skill.execute`/`skill.search` — recursion is blocked at both
  the dispatch wrapper and the capability worker
* user takeover pauses the run and records **correction evidence**
* success is `verified`, and only a verified outcome updates the statistics

## 5. NEDLE2 integration (§5.28)

NEDLE2 does **not** receive every learned skill as a tool — that would flood a 14 MB router
model. It sees exactly one generic surface:

```
skill_execute(goal)  →  capability "skill.execute"  →  SkillService
```

The **registry owns selection**. If nothing scores above the threshold (0.45) GENIE simply
does nothing and plans normally. A weak match is never run "just because a search returned it".

Ranking terms: intent/tag/name overlap, capability+plugin compatibility, proven success rate,
recency, specificity, scope affinity. **Intent is a gate**: baseline points alone can never
clear the threshold, so an unrelated-but-recent skill cannot be selected.

## 6. The golden demo (real machine)

`tests/e2e/test_skills_phase5.py` — the roadmap's named workflow: **write a dated note in Notepad**.

| Stage | What really happens |
|---|---|
| GENIE performs the work | Notepad launches, the window is focused, the dated note is typed, the file is written |
| Learning | the successful trace → candidate → generalised skill (`${path}`, `${text}`) |
| Validation | the candidate runs for real and its verification passes |
| **Environment change** | window moved to a new position **and** resized; note saved to a **different path** |
| Replay | the skill runs from the goal alone and succeeds |
| Proof | the file exists **at the new path** with the **right content** — not "the steps were issued" |

A second test replays after two further geometry changes, and a third drives the same
workflow through the **teaching** path (demonstration → generalise → validate → save → replay).
A fourth proves an unsatisfiable verification is reported as a **failure**, and a fifth proves
that no matching skill is an honest no-op.

## 7. Storage & lifecycle (§5.19–§5.22)

* `skills` table, primary key `(skill_id, version)` — the registry is the single source of truth
* `skill_corrections` table — a user fix is **evidence**, never a silent rewrite
* `update()` creates a **new version** by default; in-place editing must be requested
* `rollback()` moves the active pointer back; withdrawing the active version **clears the
  active flag and promotes the newest still-ACTIVE version**
* duplicate detection by similarity (≥ 0.75)
* scope: `global | user | project | application | device`

## 8. Surfaces

| Surface | Commands / routes |
|---|---|
| CLI | `skills`, `skills-search`, `skills-run`, `skills-versions`, `skills-rollback`, `skills-learn`, `skills-teach start\|stop\|learn\|discard\|status`, `skills-selftest` |
| HTTP | `GET /api/skills`, `/api/skills/stats`, `/api/skills/search`, `/api/skills/duplicates`, `/api/skills/corrections`, `/api/skills/<id>`, `/api/skills/<id>/versions`, `/api/teaching` · `POST /api/skills/execute`, `/api/skills/learn-mission`, `/api/skills/rollback`, `/api/skills/status`, `/api/skills/correction`, `/api/teaching/start`, `/api/teaching/stop`, `/api/teaching/learn`, `/api/teaching/discard`, `/api/teaching/event` |
| Events | `TEACHING_STARTED`, `TEACHING_STOPPED`, `SKILL_LEARNED`, `SKILL_CANDIDATE_REJECTED`, `SKILL_RUN`, `SKILL_CORRECTION` |

## 9. Defects found and fixed while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-051 | A skill with **zero intent overlap** cleared the selection threshold on baseline points (compatibility + recency + unproven) alone | intent is a **gate**: no overlap → score capped below threshold |
| A-052 | Archiving the active version left `active=1` on the archived row | withdrawing clears the flag and promotes the newest still-ACTIVE version |
| A-053 | `to_dict()` persisted the derived averages but not the raw `total_latency_ms`/`total_cost_usd`, so statistics silently reset on every save/load | persist the accumulators |
| A-054 | `search()` read capabilities only from kwargs, so a caller passing `context={"capabilities": …}` disabled compatibility filtering | read from both |
| A-055 | `SkillRunResult.status` defaulted to `"failed"`, which `run()` reads as "a step failed" → **every skill run short-circuited verification and reported failure** | neutral `"running"` default |
| A-056 | Typing into a password field was **not redacted** — only the payload key was checked, not the target field | a sensitive target field taints the payload |
| A-057 | App resolution took the first PATH hit, so **Git-for-Windows' POSIX `notepad`** shadowed Windows Notepad and hung the caller on stdin | Windows system dirs win over generic PATH |
| A-058 | `target` was parameterised for `application.*`/`window.*`, producing a required `target` input and an unevaluable `app_installed ${target}` precondition | keep the target literal |
| A-059 | A generalised skill could reference `${content}` while declaring it optional → "unresolved variables", never runnable | reconcile: every `${var}` becomes a declared input |
| A-060 | Inputs were derived from parameter *names*, so a literal `target` still demanded a `target` input | derive from the variables actually created |
| A-061 | Typing and saving the same value produced two identical inputs (`text` and `content`) | reuse `${text}` |
| A-062 | Verification substituted only the target, so `file_contains` compared against the literal `"${text}"` and every content check failed | substitute the whole check spec |

## 10. Verification

```
tests/unit/test_skills_core.py        35   schema, registry, versioning, lifecycle, selection, duplicates, stats, scope
tests/unit/test_skills_learning.py    27   detector, generalizer, validator, demonstration-is-not-a-skill
tests/unit/test_teaching_recorder.py  13   semantic capture, secret redaction, session shape
tests/unit/test_skills_runtime.py     21   correction evidence, takeover, preconditions, recursion guard, NEDLE2 surface
tests/e2e/test_skills_phase5.py        5   REAL golden demo on the machine (Notepad, changed position/size/path)
tests/contract/test_ipc_api.py       +10   skill + teaching HTTP surface
```

**Phase 5 exit gate: PASS** — 111 new tests, all green; the golden demo replays a taught
workflow at a changed window position, a changed window size and a changed path.

### Real-machine note
The desktop-sensitive tests share one interactive session. Windows refuses foreground
changes from background processes, so a focus attempt can transiently fail while another
app holds focus. The tests therefore **wait for the observed state** (window moved, window
focused) instead of sleeping, and a learned `window.focus` step carries a retry budget.

---

## 11. Post-acceptance correction — the semantic routing guard (D-064/D-065, A-064)

**The defect.** NEDLE2 answered

```
"mera latest project continue karo"  ->  media.play   @ 0.99 confidence
```

Executing that is not a small error: the owner asked to continue their work and GENIE would
have started playing media on a device. The root cause is architectural, not lexical — a 14 MB
statistical router is fast, but **its confidence is not evidence**, and making its prompt
longer cannot turn it into a correctness guarantee.

**The fix — `director/semantic_guard.py`.** A deterministic post-routing validator that runs
around NEDLE2 output:

| | |
|---|---|
| **Rule** | A media/device action is accepted only when the request (or live context) carries **actual media grounding**. Continuation semantics are not media grounding. |
| **Continuation semantics** | an explicit phrase ("continue from where", "kal wala kaam", "previous mission", "latest project", "wahi se continue", "resume task") **or** a continuation marker together with a work noun ("latest project continue", "kal wala project resume") |
| **Media grounding** | concrete media nouns/brands (song, gaana, music, track, video, movie, spotify, youtube, playlist, media session …) and playback verbs (play, pause, bajao, chalao …), or a live media session in context |
| **On conflict** | the media task is **withdrawn**, the decision is escalated (`reasoning_required`, category `reasoning`) and pointed at memory/mission retrieval; the refusal is audited as `route.semantic_guard` and published as `POLICY_VIOLATION_BLOCKED` |
| **Never** | silently drop the request, or let confidence override semantics |

Verified behaviour:

```
"mera latest project continue karo"   -> media withdrawn, memory:query retrieval + escalation
"kal wala project resume karo"        -> media withdrawn, memory:query retrieval + escalation
"continue the previous mission"       -> media withdrawn, memory:query retrieval + escalation
"Spotify ka song continue karo"       -> media allowed  (spotify, song)
"video resume karo"                   -> media allowed  (video)
"phone ka next song"                  -> media.next     (song, next)
```

Applied at **both boundaries** with the same pure, idempotent function (D-065): inside
`NeedleDirector.classify` so every consumer of NEDLE2 receives validated output, and again in
the orchestrator so every director — including the heuristic fallback — is covered.

**Effect on the routing smoke: 11/12 → 12/12.** The previously failing Hinglish case now
withdraws the media route, performs `memory:query` retrieval and escalates. The test was not
relaxed; the system genuinely behaves correctly.

**Regression coverage:** `tests/unit/test_semantic_guard.py` — 60 tests, including the five
owner-specified phrases, an explicit *high confidence does not override semantics* case, the
context-grounded media case, and three tests that drive the real orchestrator turn loop and
assert the audit entry and the published event.
