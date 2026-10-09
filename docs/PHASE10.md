# GENIE — PHASE 10: AGENT FACTORY, TEAMS & COST CONTROL

**Status: 🟩 COMPLETE — Phase 10 GREEN**

> **Exit gate:** *golden #4 (coding task with tests), #5 (multi-agent build with mailbox/blackboard),
> #7 (provider failover mid-mission).*
> All three verified **with a real local executor** (writes files, runs `pytest`, posts real
> mailbox/blackboard messages) — not a stub returning hardcoded strings. Full evidence:
> [`docs/PHASE10_GOLDENS.md`](./PHASE10_GOLDENS.md).

---

## 1. The architecture, and the one rule that shapes it

```
        NEDLE2
          ↓
    Mission Engine
          ↓
    Team Orchestrator
          ↓
  ┌───────┼───────┐
  ↓       ↓       ↓
Agent A  Agent B  Agent C
  └───────┼───────┘
          ↓
   Shared mission state
```

**Agents do not share one giant transcript.** They share:

| Shared | What it is |
|---|---|
| **task DAG** | `agents/team.py` — a task cannot start until its dependencies are complete |
| **mailbox** | structured messages carrying *references*, never file contents |
| **blackboard** | mission facts only: decisions, interfaces, constraints, blockers, assumptions |
| **artifact store** | one place for bytes, with a hash, a version and a producer |
| **budget controller** | global → mission → team → agent → task |
| **locks** | the existing lease-based `LockService` |

An agent's private reasoning never enters another agent's context. That is enforced by *shape*: the
context an agent receives contains its task, the blackboard, artifact **references** and its
inbox — there is nowhere to put a transcript.

## 2. Modules

| File | Responsibility |
|---|---|
| `agents/contracts.py` | agent kind/lifecycle, task node, message, blackboard entry, artifact, budget, continuation packet, team plan |
| `agents/artifacts.py` | register/version/hash artifacts; read on demand; verify against tampering |
| `agents/budget.py` | budget hierarchy, spend accounting, measurable escalation |
| `agents/team.py` | mailbox, blackboard, task DAG, `TeamOrchestrator` (scheduling, failure handling, reviewer, cancel/pause) |
| `agents/factory.py` | `AgentFactory` (search-before-create, capability-not-vendor, cost-aware planning, versioned candidates) + `ExperienceStore` |
| `agents/service.py` | facade: teams, budgets, artifacts, continuation packet, failover |
| `agents/providers.py` | **`ModelGateway`** (capability→tier routing + measurable escalation) and **`LocalExecutor`** — a controlled, offline provider that does *real work* for the golden missions |
| `core/contracts.py` | `AgentDefinition` extended (role, kind, `model_requirement`, budget, version) — not duplicated |
| `core/db.py` | migration `010_agents` (definitions, tasks, messages, blackboard, artifacts, escalations, experience) |
| `core/lifecycle.py` | service wiring + the team runner bound to the model gateway |
| `core/ipc/server.py`, `genie.py` | `/api/agents*`; `agents`, `agents-plan`, `agents-failover`, `agents-cancel` |

## 2b. Model routing & the executor (§10.9, §10.11, §10.12)

`agents/providers.py` is the seam between "an agent needs a capability" and "a model actually runs
the task".

* **`ModelGateway`** maps `capability + quality` → a priced **tier** (`local < cheap < strong <
  expert`). An `AgentDefinition` never names a vendor (D-090); it requests `coding/strong` and the
  gateway picks the cheapest tier that fits. **Escalation** moves exactly one tier up and records
  the evidence (`escalate(reason, evidence)`) — it is measurable, not guessed.
* **`LocalExecutor`** is a *controlled, offline* provider used by the golden missions. It is not a
  stub: it writes genuine source files, runs a real `pytest` subprocess, and posts real mailbox /
  blackboard messages. A malformed module produces a failing test that the reviewer **rejects** —
  the system proves it catches real failures. It is labelled honestly as
  `protocol/behavior verified (controlled local provider)`; live API providers slide in behind the
  same gateway later without touching the golden missions.

## 3. The factory (§10.1, §10.2, §10.18, §10.19, §10.23, §10.24)

* **Searches before creating.** `create()` returns `reused=True` with the existing healthy agent if
  one matches; a second agent solving the same thing is waste and a source of divergence.
* **Capability, not vendor.** `model_requirement` is `{"capability": "coding", "quality": "strong"}`
  — never "use provider X forever" (D-090).
* **Minimum useful team.** `estimate_team_size(complexity)` returns 1 for simple work; the ceiling
  is 6 and `add_member` refuses beyond it.
* **Shows the cheaper alternative.** Every plan carries
  `single_agent_alternative`: *"a single strong agent would cost about $0.05 and is the right
  choice when the work has no separable parts"*, and warns when the estimate exceeds the budget.
* **Disposable by default.** Mission agents are `ephemeral`; `cleanup()` retires them and removes
  their budget nodes. Only an evaluated candidate may be promoted, and promotion **supersedes**
  rather than mutates the previous profile.
* **Cannot grant itself capabilities.** The factory proposes tools and scopes; the PTE decides.

## 4. Task DAG (§10.4)

Each node stores id, objective, owner, status, dependencies, inputs, outputs, attempt, budget and
completion criteria. Cycles are **refused at insertion**, an unknown dependency is refused, and a
failed task marks its dependents `blocked` immediately so the caller sees what is now unreachable.

Assignment is **role-aware**: a test task goes to the tester, not to whichever agent happened to be
first. (Getting this wrong is how a team ends up with duplicated work and an idle specialist.)

## 5. Failure handling (§10.14)

```
classify → retry same agent → different provider → replacement agent → fail the task
```

A transient failure is retried; a **provider** failure reassigns to another agent, because a
provider problem is not an agent problem. A task that fails outright blocks its dependents but does
**not** automatically fail the mission — the orchestrator reports it and the caller decides.

## 6. Reviewer pattern (§10.15)

Producer and verifier are separable, but only above a threshold: `requires_review` **and**
`complexity >= 0.6` (or an explicit `force_review`). A rejected task goes back to the producer with
the reviewer's reason; a trivial task is never reviewed.

## 7. Cost control (§10.10, §10.11, §10.28)

Budgets are hierarchical and **a child can never exceed a parent's remaining budget** — spending
against an agent is charged to the mission and to the global node too. Dimensions: tokens, cost,
model calls, tool calls, wall clock.

Escalation to a stronger model **requires evidence**: `request_escalation()` refuses without it,
moves exactly one tier, records the reason and the estimated cost, and is refused if the budget
cannot afford it. GENIE records its own cost estimates, so a provider that under-reports cannot
quietly exceed the ceiling.

## 8. Provider failover (§10.12, §10.13) — golden #7

```
Agent working with Provider A
  ↓  Provider A unavailable
snapshot (ContinuationPacket)
  ↓  gateway selects Provider B
compact continuation packet
  ↓
task continues — completed work is NOT repeated
```

The packet carries the objective, current task, plan, completed steps, pending steps, decisions,
artifact **references**, relevant context, known errors and tool state. `render()` is bounded, so it
cannot silently become a transcript dump. The failing provider is reported to the gateway's health
tracker for the circuit breaker.

## 9. Verified

| Test | What it proves |
|---|---|
| **golden #4** | a coding mission produces a real file and a real test-result artifact, the test subprocess runs, and every artifact lives inside the mission workspace (no live-system pollution) |
| **golden #5** | architect → backend → tester over a DAG; the backend learns the contract from the **blackboard** and receives the artifact by **reference**; no duplicate work; the budget is charged |
| **golden #7** | a provider dies mid-mission; completed work is preserved, the packet names it, and the mission continues without repeating it |

Plus: DAG ordering and cycles, blocked dependents, mailbox addressing, blackboard kinds, artifact
tamper detection and shared reference, budget hierarchy and limits, escalation evidence, factory
reuse, team-size decision, reviewer accept/reject/skip, cancellation with no orphans, pause/resume
without rebuilding the team, lifecycle transitions, experience recorded structurally, PTE authority
over a generated agent, and a full audit trail.

## 10. Defects found while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-073 | Task assignment ignored the role, so a test task went to the backend agent and the tester sat idle — duplicated work and an unused specialist | `TaskNode.required_role` + role-aware `_pick_agent` |
| A-074 | A failed task left its dependents `pending` until the next scheduling pass, so the caller could not see what had become unreachable | dependents are marked `blocked` at the moment of failure |

## 11. Surfaces

| Surface | Routes / commands |
|---|---|
| HTTP | `GET /api/agents`, `/api/agents/teams`, `/api/agents/teams/<mission>`, `/api/agents/continuation/<mission>` · `POST /api/agents/plan`, `/api/agents/start`, `/api/agents/failover`, `/api/agents/pause`, `/api/agents/resume`, `/api/agents/cancel` |
| CLI | `agents`, `agents-plan`, `agents-failover`, `agents-cancel` |

## 12. Deliberately deferred (interfaces preserved)

| Deferred | Why | Hook that exists |
|---|---|---|
| A visual agent graph | §10.22 asks for basic observability only | `status()` already returns team, tasks, dependencies, provider, cost, blockers |
| Cross-vendor failover with real keys | no provider credentials on this machine | `docs/ANDROID_DEFERRED.md` → `OWNER-PROVIDER-KEYS`; the mechanism is verified with local providers |
| Agent-to-agent negotiation | the mailbox covers structured handoff | `Mailbox.send` with `to_agent` |
| Learned agent profiles from long-run statistics | needs accumulated experience | `ExperienceStore.best_configuration()` already ranks by success rate and cost |
| `mobile_push` for agent completion notices | Android deferred | the proactive service owns delivery |

## 13. Verification

```
tests/e2e/test_phase10_agents.py     76   DAG, mailbox, blackboard, artifacts, budgets, escalation,
                                          factory, teams, failure, reviewer, cancel/pause,
                                          experience, golden #4/#5/#7
tests/e2e/test_phase10_goldens.py     4    golden #4/#5/#7 driven by the REAL LocalExecutor
                                          (writes files, runs pytest, posts mailbox/blackboard),
                                          plus a negative test proving a broken module is
                                          rejected — not a stub returning {"ok": True}
genie.py agents                         teams, tasks, providers, budgets, orphans
genie.py agents-plan <objective>        the team and the cheaper alternative
genie.py agents-failover <mission>      simulate a provider outage
tools/phase10_goldens.py                regenerates docs/PHASE10_GOLDENS.md
```

### Test categories (the recurring desktop contention failures no longer pollute the default run)

| Category | Result | Notes |
|---|---|---|
| **deterministic** (default `pytest`) | **green** | pure logic, throwaway DB, no shared desktop state |
| **real_machine** serial (`-m real_machine`) | 67 passed / 1 failed | the 1 failure is an intermittent real-Chrome download race (environmental, not a logic regression); it is isolated behind the shared `REAL_DESKTOP_TEST` lock and never reaches the deterministic result |
| **hardware_optional** (`-m hardware_optional`) | passed | microphone/camera/GPIO when attached |
| **owner_acceptance** (`-m owner_acceptance`) | 1 passed / 6 pending | only the owner can perform these; reported as pending, never as a pass |

Full commands: `python tools/test_suites.py` (or `pytest` / `pytest -m real_machine` / `pytest -m owner_acceptance`). See `docs/TESTING.md`.

**Phase 10 exit gate: PASS** — dynamic task agents work, teams and the DAG work, mailbox and
blackboard carry the handoff, artifacts flow by reference, budgets are enforced (GENIE-side
estimates charged, not just vendor self-reports), provider failover preserves progress, failures
recover, agents are cleaned up, and the PTE still controls capabilities. Golden #4/#5/#7 are
verified end to end with a real executor in `docs/PHASE10_GOLDENS.md`.
