# GENIE — MEMORY

Authoritative: master spec §3. Ye file implementation quick-reference hai.

## Types

`Working · Conversation · Episodic · User · Preference · Semantic · Project · Mission · Agent · Skill · Device · Application · Environment · Relationship Graph`

## Write pipeline

```text
event → NEDLE2 *proposes* → Memory Service validates → dedupe/merge
→ supersede (never overwrite) → confidence+source+privacy_scope → write → MEMORY_UPDATED
```

Har record par mandatory metadata:
`source · timestamp · confidence · person · project · mission · privacy_scope · last_accessed · last_updated · superseded_by · provenance`

## Retrieval

```text
NEDLE2 intent → Memory Service → hybrid retrieval → PTE read-scope filter
→ Context Builder → model
```

Model ko **kabhi lifetime dump nahi** — sirf relevant context packet.

## Search bina local embedding model ke

| State | Strategy |
|---|---|
| Online | provider embeddings → **vectors locally cached** (hash + model version keyed) |
| Offline | FTS + structured fields + graph relations + recency + exact entity match |

## Correction / deletion

| Op | Behaviour |
|---|---|
| `correct` | Naya version, purana `superseded_by`, contradiction policy |
| `forget` | Hard delete: tables + FTS + vector cache + backup queue |
| `rollback` | Previous version restore (logged) |
| `merge` | Duplicate → canonical + aliases |
| `pin` | Auto-summarization/TTL se protect |

Contradiction: explicit user statement > higher confidence > recent. Tie → user se poochho (ek baar), phir yaad rakho.

## Boundaries

`private:<person>` · `shared:<space>` · `system`.
Cross-person read **sirf** share scope se. `RESTRICTED` kabhi remote provider ko nahi.

## Storage (Phase 1)

SQLite WAL + structured tables + FTS5 + graph tables + vector cache + artifact store.
PostgreSQL migration tabhi jab multi-device concurrency genuinely badhe (D-006).
