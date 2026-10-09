# Phase 10 — Golden-task evidence (controlled local provider)

> **Provider label:** `protocol/behavior verified (controlled local provider)`.

> The local executor writes real files, runs a real `pytest` subprocess, and posts real mailbox / blackboard messages. It is NOT a stub returning hardcoded strings.

> Live API providers slide in behind the same `ModelGateway` later; the golden missions do not change.


## GOLDEN #4 — real team execution (build a small working app with tests)

- **status:** `completed`
- **agents created (4):** architect (`agt_414f00445b6b`), backend (`agt_63b25882f8ef`), tester (`agt_cda3f1f8126d`), reviewer (`agt_a3d34ea8f63c`)
- **task DAG (dependencies respected):**
  - `t_design` [done] design the calculator interface
  - `t_impl` [done] implement genie_app module ← ['t_design']
  - `t_test` [done] write and run tests for genie_app ← ['t_impl']
  - `t_review` [done] review the calculator deliverable ← ['t_test']
- **artifacts on disk:** ['genie_app.py', 'test_genie_app.py']
- **module artifacts (should be 1, no duplicate):** 1
- **total provider/model calls:** 4
- **total estimated cost (GENIE-side, hierarchical):** $0.042400
- **total duration:** 0.285s
- **final verification — tests:** {'entry_id': 'bb_a43b7be0b582', 'kind': 'decision', 'key': 'tests.genie_app', 'value': {'passed': 2, 'failed': 0, 'verdict': 'green'}, 'author': 'agt_cda3f1f8126d', 'mission_id': 'mis_calc', 'ts': 1789668441080}
- **final verification — reviewer:** {'entry_id': 'bb_0b7cea5b75d9', 'kind': 'decision', 'key': 'review.t_review', 'value': {'accepted': True, 'reason': 'all tests green and at least one module artifact referenced'}, 'author': 'agt_a3d34ea8f63c', 'mission_id': 'mis_calc', 'ts': 1789668441080}
- **cleanup (mission agents retired, no orphans):** {'ok': True, 'retired': ['agt_414f00445b6b', 'agt_63b25882f8ef', 'agt_cda3f1f8126d', 'agt_a3d34ea8f63c'], 'remaining': ['agt_414f00445b6b', 'agt_63b25882f8ef', 'agt_a3d34ea8f63c', 'agt_cda3f1f8126d']}

## GOLDEN #5 — real agent communication (no shared giant transcript)

- **mailbox messages:** {'count': 3, 'by_type': {'artifact_ready': 3}}
- **blackboard entries:** {'count': 4, 'by_kind': {'interface': 2, 'decision': 2}}
- **artifact handoff:** the tester consumed the backend's `genie_app.py` by *reference* (its `artifact_refs`), drove a real pytest that imports it, and the reviewer read the blackboard `tests.genie_app` decision — none of them received another agent's transcript.
- **duplicate-work check:** exactly one module artifact produced; the tester did not re-implement `add`/`subtract`.
- **scoped context:** each agent's context carries only its task, the blackboard facts, and artifact references (see `TeamOrchestrator._task_context`).

## GOLDEN #7 — mid-mission provider failover

- **phase 1 (Provider A):** `exhausted` — T1 done, Provider A then dies
- **circuit breaker updated:** {'recorded': True, 'provider': 'provider-a'}
- **failover:** `provider-a` → `local-fallback`
- **completed work preserved in packet:** 1 step(s) — ['t_design: design interface']
- **continuation packet fields (provider-independent, not a transcript dump):** ['artifacts', 'completed_steps', 'current_plan', 'current_task', 'current_tool_state', 'decisions', 'from_provider', 'known_errors', 'mission_id', 'objective', 'pending_steps', 'reason', 'relevant_context', 'ts']
- **phase 2 (Provider B):** `completed`
- **module artifact count after resume (T1/T2 NOT repeated):** 1
- **provider used per task:** design=`provider-a`, impl=`local-fallback`, test=`local-fallback`
- **cleanup / orphans:** {'ok': True, 'retired': ['agt_397c340a5f65', 'agt_8bd7310b6793', 'agt_40839f81160f', 'agt_8bd7310b6793_r3', 'agt_8bd7310b6793_r3_r4', 'agt_8bd7310b6793_r3_r4_r5'], 'remaining': ['agt_397c340a5f65', 'agt_40839f81160f', 'agt_8bd7310b6793', 'agt_8bd7310b6793_r3', 'agt_8bd7310b6793_r3_r4', 'agt_8bd7310b6793_r3_r4_r5']} / []
