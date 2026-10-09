# GENIE — PHASE 12: SELF-IMPROVEMENT

**Status: 🟩 COMPLETE** (12.1 experience distillation · 12.2 evaluation lab · 12.3 governed self-evolution)

Phase 12 closes the loop the earlier phases opened: the fleet records what it did, distills what  
worked, and uses that to build the next agent better.

---

## What the audit sent here

From `docs/REPO_UTILIZATION_AUDIT.md`, three repositories target this phase:

| repo                | capability                                                       | disposition here                                                       |
| ------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **Youtu-Agent**     | training-free GRPO experience loop                               | **adapted** — the core algorithm, driven by GENIE's structured records |
| **Qwen-AgentWorld** | simulation / long-horizon evaluation                             | **deferred → evaluation lab**                                          |
| **enoch**           | governed self-evolution (lineage + conformance + owner approval) | **deferred → safe self-modification**                                  |

---

## 12.1 Experience distillation — training-free group advantage ✅

Built on the outcome records seeded by Phase 11D (`agent_mission_outcomes`) and the structured  
`ExperienceStore` from Phase 10.

`ExperienceStore.distill(min_attempts=3)` adapts Youtu-Agent's loop — *summarise rollouts →  
semantic group advantage → update the experience bank* — but uses GENIE's structured records  
instead of raw trajectories (§10.16: experience is never a transcript):

1. group attempts by task type;
2. within a task type, group by configuration (role + provider + strategy);
3. compare each configuration's success rate against the task type's **baseline**;
4. emit guidance only when the difference is real and the sample is big enough.

Every emitted item carries its **evidence** (`6/6 succeeded vs baseline 58% over 12 attempt(s)`)  
and an `advantage` score, so a suggestion is never a guess.

**Guard rails (the important part):**

- below `min_attempts` → **no guidance** (two lucky runs are not a lesson);
- advantage within ±0.05 → treated as **noise**, not signal;
- unknown task type → `recommend_configuration()` returns `None`, so the factory falls back to  
  its defaults rather than acting on nothing.

`AgentFactory.recommend_configuration(task_type)` closes the loop: the next agent for that kind of  
work is built with the configuration that actually won. `improvement_report()` exposes everything  
the fleet has learned, with evidence attached.

---

## 12.2 Evaluation / dev lab ✅

`evaluation/lab.py` — capability adapted from **Qwen-AgentWorld**, kept at the **dev-lab layer**,  
so nothing here runs in a normal session unless the owner asks.

- **scenarios** — `Scenario(id, domain, objective, task, check)`: a named, repeatable piece of work  
  with deterministic checks, grouped into suites.
- **multi-dimensional scoring** — adapted from Qwen-AgentWorld's  
  format/factuality/consistency/realism/quality dimensions. A check reports individual dimensions  
  (`built`, `add`, `subtract`, …); the lab aggregates them and keeps the breakdown so a failure can  
  be *explained* rather than just counted.
- **runs & history** — persisted (migration `012_eval`: `agent_eval_runs`, `agent_eval_results`).
- **baselines & regression detection** — `regressions(run_id)` compares each scenario against the  
  best score it has ever achieved; a drop beyond a tolerance (default 0.05) is reported with the  
  baseline, the new score and the size of the drop.

Honesty rules: a scenario with **no runner** is a recorded failure, never a pass; a runner that  
raises is a recorded failure; and **a scenario with no conformance checks registered is a refusal,  
not a pass** — otherwise governance would be decorative.

## 12.3 Governed self-evolution ✅

`agents/evolution.py` — capability adapted from **enoch** (lineage + conformance + owner approval).

```
propose  ->  conformance MUST pass  ->  owner MUST approve  ->  apply
```

- **lineage** — each proposal may name a parent; `lineage()` traces a change back to the  
  observation that produced it, so "why does the system behave this way?" stays answerable.
- **conformance** — checks are registered per proposal and must all pass.
- **approval gate** — `approve()` refuses unless conformance has passed; `apply()` refuses unless  
  the proposal is approved *and* still conformant. Rejected proposals can never be applied.
- **audit** — every transition (`propose` / `conformance` / `approve` / `reject` / `apply`) is  
  written to the tamper-evident audit log.

**Nothing auto-applies.** An unattended GENIE cannot rewrite itself — that is the point.

---

## Remaining (deliberately deferred)

| item                                        | source          | note                                                                                              |
| ------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------- |
| LLM-assisted trajectory summarisation       | Youtu-Agent     | optional upgrade once live providers are wired; the deterministic distillation already works      |
| simulated worlds (interactive environments) | Qwen-AgentWorld | the scenario/baseline/regression machinery exists; full simulated environments are a larger build |

---

## Verification

```
tests/e2e/test_phase12_improvement.py    7   prefers the configuration that won; refuses to act
                                             below min_attempts; ignores noise; refuses to guess
                                             on unknown work; evidence attached everywhere
tests/e2e/test_phase12_evaluation.py     8   real module built + imported + executed; multi-
                                             dimensional scoring; history/baseline; a scenario
                                             that really degrades IS flagged as a regression;
                                             missing runner / raising runner / broken check are
                                             all recorded failures
tests/e2e/test_phase12_evolution.py     13   conformance-before-approval, approval-before-apply,
                                             no-checks-is-a-refusal, rejection blocks apply,
                                             applier failure -> FAILED, lineage tracing, audit
```
