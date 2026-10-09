# GENIE — MODEL PROVIDERS

Authoritative: master spec §4.1–4.3, §1.6, §2.3.

## Rule

```text
Agent → Model Requirement → Provider Gateway → Model Registry → Available provider
```

Agents **provider hardcode nahi karte**. NEDLE2 bhi sirf category maangta hai ("strong coding model"),
exact provider nahi. Choice = policy + health + cost.

## Local vs remote

```text
LOCAL:  NEDLE2 (sirf ek reasoning model) + kernel + DB + services + control + plugins
REMOTE: reasoning · coding · vision · research · long-context · image · video
        · speech intelligence · embeddings · specialist models
```

ComfyUI / OmniVoice / Kronos core PC par **nahi** — adapters hain, external GPU worker/service ke roop mein.

## Registry fields (har model)

`model · provider · capabilities · context_window · latency · cost(in/out) · vision? · tools? ·
reasoning? · coding? · availability · health · quota · data_policy_class`

## Selection order

1. **Policy filter** (data class, region, retention, code-exec) — hard
2. **Capability match** (coding/vision/context length)
3. **Health** (circuit breaker, error rate, latency)
4. **Cost** (budget ceiling, cheap-first + escalation)
5. **Latency** (voice path ke liye strict)

## Failover

```text
Provider A (quota/circuit) → Mission Snapshot → Provider B → continue
```
Snapshot **provider-agnostic** hona chahiye: goal, plan, completed steps, artifacts, decisions, errors,
agent state, relevant memory. Event: `PROVIDER_FAILOVER` (audited, cost accounted).

## Cost & quota controller

Budgets: `per_mission · per_hour · per_day · per_provider · per_project`.
Soft limit → degrade (chhota model / kam retries) + notify. Hard limit → block + ask.
Cheap model pehle; escalate on failure/low-confidence/known-complex class.
Metrics: `provider_calls · tokens · cost · retries · cache_hits`.

## Model Policy Registry (security side)

`allowed_data_classes · vision_allowed · code_execution_allowed · regions_allowed · retention_policy ·
fallback_allowed_from · max_cost_per_mission · sensitive_topics`
Violation → `POLICY_VIOLATION_BLOCKED`.

## Embeddings (no local model)

Online → provider embeddings → **vectors locally cached** (content hash + model version keyed).
Offline → FTS + structured + graph + recency fallback. Cache miss → async backfill.

## Provider SDK

Register: model list · capabilities · health endpoint · cost model · data policy class.
semver; breaking change = major + migration note.
