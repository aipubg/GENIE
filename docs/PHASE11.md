# GENIE — PHASE 11: REPOSITORY INTEGRATION, WORKSPACE & OBSERVABILITY

**Status: 🟩 COMPLETE**

Phase 11 turns the supplied repositories from "uploaded material" into *accounted-for capability*,
gives GENIE its own computer to work on, and exposes an operator surface over the whole fleet.

| Part | Scope | Status |
|---|---|---|
| **11A** | Repository Utilization Audit — every supplied repo has a documented destination | 🟩 |
| **11B** | Specialist integrations + agent-runtime composite (role templates, adapters) | 🟩 |
| **11C** | GENIE Workspace / background computer (isolated, mission-owned, quota'd) | 🟩 |
| **11D** | Operator Console / observability surface (supporting infrastructure) | 🟩 |

---

## 11A — Repository Utilization Audit

**`docs/REPO_UTILIZATION_AUDIT.md`** records all **22 distinct repositories** (23 archives;
`Kronos-master (1).zip` is a byte-identical duplicate). Every repo was inspected in **real
source** — not README-only — and assigned exactly one disposition:

| disposition | count | repos |
|---|---|---|
| `DIRECT REUSE` | 3 | agency-agents (persona corpus), hack-skills (security corpus), public-apis (catalog) |
| `ADAPTED` | 4 | Youtu-Agent (experience loop), spec-kit (coding workflow), enoch (self-evolution), OpenClaw (concepts) |
| `REIMPLEMENTED` (GENIE better, parity recorded) | 4 | hermes-agent, deepseek-harness, munder-difflin, PinchTab |
| `EXTERNAL SERVICE` / backlog | 11 | n8n, ComfyUI, HeyGem, OmniVoice, Kronos, MiroFish, Decepticon, Open-Higgsfield, VoiceStudio, AgentTube, Qwen-AgentWorld |

**Zero repositories are silently unused.** The audit ends with a canonical capability matrix and a
direct answer to *"what capability from this repository exists in GENIE?"* per repo.

Key judgement calls (per the owner's rule — never replace a stronger mature system with a weaker
home-made clone, and never force bad code in):

- **hermes / deepseek-harness / munder-difflin / PinchTab** → GENIE's own implementations are
  demonstrably stronger (budgets, tamper-evident audit, provider failover, verified downloads,
  artifact-by-reference). Recorded as `REIMPLEMENTED` with the parity comparison, **not** adopted.
- **ComfyUI / n8n / Kronos** → their engine *is* the value. Wrapped as external services; never rewritten.
- **Qwen-AgentWorld** → genuinely belongs to the evaluation/dev-lab layer → `DEFERRED` to Phase 12.

---

## 11B — Specialist integrations & agent-runtime composite

### One runtime, strongest parts reused
There is still **one GENIE Agent Runtime** (`agents/`), not five competing ones. Phase 11 adds:

- **`agents/personas.py`** — the agency-agents corpus vendored to `data/personas/`
  (**264 templates / 18 divisions**), exposed as **role templates**, exactly as instructed:
  `role templates → AgentFactory → task-specific agent`.
  - `AgentFactory.create_from_persona(need, ...)` resolves the best persona and builds **one**
    agent. A missing corpus degrades to the built-in worker instead of failing.
  - Search is **name/division-weighted** — a regression guard exists because raw term-frequency
    over long descriptions let a verbose compliance persona outrank a backend engineer on an
    API request.
- **`integrations/specialists.py`** — one `SpecialistAdapter` contract for every external engine:
  `available()` / `invoke()` / `conformance()`. Wraps n8n, ComfyUI, HeyGem, Kronos, MiroFish,
  OmniVoice, Decepticon (gated on explicit authorisation).
  - An absent engine reports **`pending-live-acceptance`** — never a fake success, never a crash.
  - `capability_matrix()` makes every specialist capability visible at runtime.
- **Youtu-Agent's training-free GRPO experience loop** (trajectory summarisation → semantic group
  advantage → experience-bank update) is recorded as the upgrade path for GENIE's
  `ExperienceStore`; it lands in **Phase 12**, seeded by 11D's outcome table.

---

## 11C — GENIE Workspace / background computer

GENIE now has its own work desk, distinct from the owner's interactive desktop (spec §22):

    USER COMPUTER   personal interactive desktop
    GENIE WORKSPACE isolated background computer

Extended `computer/workspace.py` (existing path-enforcement reused, not duplicated) with:

- **identity** — `workspace.identity`: workspace id, root, created time, detected isolation mode.
- **mission ownership** — `workspace.mission_claim` / `mission_release` / `missions`; each mission
  gets a scoped `missions/<id>/{downloads,artifacts,temp}` tree so concurrent missions never collide.
- **browser profile & sessions** — `workspace.session_dir` / `browser_profile_dir`: a dedicated
  Chrome profile inside the workspace, so logins/sessions persist without touching the user's Chrome.
- **downloads & artifacts** — mission-scoped download areas.
- **shell** — `workspace.shell` runs commands rooted inside the workspace; **relative and absolute
  path escapes are refused** (verified by tests that really attempt them).
- **cleanup rules** — `workspace.cleanup(max_age_days, ...)`; **dry-run by default** so nothing is
  guess-deleted.
- **storage quotas** — `workspace.quota` / `set_quota` / `check_quota` (default 2 GiB ceiling) so
  agents cannot fill the owner's disk.

Exposed through the existing **Computer contracts** (`workspace.*` capabilities with PTE scopes
`computer:workspace:{read,write,exec}`) and real verifiers — not a parallel system.

---

## 11D — Operator Console / observability (supporting infrastructure)

`agents/ops.py` `OperatorConsole`, reached via `AgentService.ops_*`:

- **`ops_dashboard()`** — fleet view: per-team tasks/agents/orphans/budget/artifacts/mailbox/
  blackboard/concurrency/throughput, plus audit trail and improvement findings.
- **`ops_control(action, ...)`** — audited actions on live teams: `pause`, `resume`, `cancel`,
  `force_failover`, `retire_orphans`, `set_budget_cap`, `throttle`. Unknown actions are **refused**,
  not swallowed.
- **`ops_record_outcome()`** → `agent_mission_outcomes` (migration `011_ops`) — the structured
  per-mission snapshot Phase 12 consumes.
- **`ops_improve()`** — evidence-backed findings (orphan leaks, failover instability, team-size).
- Surfaces: IPC (`/api/ops/*`), CLI (`genie.py ops`, `ops-control`, `ops-audit`, `ops-outcomes`,
  `ops-improve`), and a vanilla-JS console at `/ui/ops`.

---

## Verification

```
tests/e2e/test_phase11_workspace.py       10   identity, mission ownership, path-escape refusal,
                                              quota, cleanup dry-run, dedicated profile, executor wiring
tests/e2e/test_phase11_integrations.py     9   real HTTP round-trip vs a live stand-in engine,
                                              pending-live-acceptance honesty, gating, registry
tests/e2e/test_phase11_personas.py         8   corpus loads, name-weighted search, factory creates
                                              one task-specific agent, graceful degradation
tests/e2e/test_phase11_ops.py              8   dashboard reflects a real completed mission, control
                                              mutates state + is audited, orphan retirement, outcomes
full deterministic suite                 717 passed, 0 failed, 74 deselected
```

**Phase 11 exit gate: PASS** — every supplied repository has a documented destination, GENIE has
an isolated mission-owned computer with quotas and cleanup, specialist engines are reachable behind
one honest adapter contract, and the operator can see and control the whole fleet with every
action audited.
