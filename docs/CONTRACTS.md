# GENIE — CONTRACTS

**Version:** v1.0 (aligned with MASTER SPEC v1.0 — FROZEN)
**Rule:** Koi bhi module doosre module ke internals ko directly touch nahi karega. Sirf in contracts ke through.
Contract badalna ho → minor version bump + `CHANGELOG.md` + contract tests. Breaking change = major + RFC.

Notation: `Module.method(params) -> result`. `ctx` = `CallContext` (har call mein mandatory).

```text
CallContext {
  trace_id, person_id, session_id, device_id, mission_id?, agent_id?,
  scopes[], data_class, idempotency_key?, deadline?
}
```

---

## C1. Trust.* — `security/` (sabse pehle call hota hai)

```text
Trust.check(ctx, scope, resource)         -> { allow: bool, reason, grant_id? }
Trust.request(ctx, scope, reason, ttl)    -> { status: granted|denied|needs_confirm, grant_id? }
Trust.grant(owner_ctx, principal, scope, type)  -> grant_id   # one_time|session|standing|conditional
Trust.revoke(owner_ctx, grant_id)         -> ok
Trust.grantsFor(principal)                -> Grant[]
Trust.classify(content)                   -> PUBLIC|INTERNAL|SENSITIVE|SECRET|RESTRICTED
```

**Invariants:** default deny · har decision audit hota hai · agents `standing` grant nahi maang sakte ·
destructive actions ke liye confirm + undo plan zaroori (master spec §1.4).

## C2. Secrets.* — `security/vault`

```text
Secrets.resolve(ctx, secret_ref)          -> value (sirf in-process, never logged)
Secrets.store(owner_ctx, ref, value)      -> ok
Secrets.rotate(ref) / .revoke(ref)        -> ok
Secrets.use(ctx, ref, purpose)            -> { ok, audit_id }
```

**Invariants:** raw secret kabhi logs/context/payload mein nahi · LLM context mein kabhi nahi · har use audited.

## C3. Memory.* — `memory/service`

```text
Memory.write(ctx, record)                 -> record_id        # dedupe/merge/supersede
Memory.correct(ctx, record_id, patch)     -> new_version_id   # purana superseded_by
Memory.forget(ctx, selector)              -> purged_count     # hard delete everywhere
Memory.query(ctx, intent, filters)        -> MemoryHit[]      # PTE read-scope filtered
Memory.pin/unpin(ctx, record_id)          -> ok
Memory.history(record_id)                 -> version[]
```

Record metadata: `source, ts, confidence, person, project, mission, privacy_scope, last_accessed, superseded_by`.
**Invariant:** ghalat memory overwrite nahi, **supersede** hoti hai.

## C4. Mission.* — `missions/service`

```text
Mission.create(ctx, goal, criteria, targets)  -> mission_id
Mission.plan(mission_id, steps)               -> ok
Mission.transition(ctx, mission_id, state)    -> ok      # sirf service hi likh sakti hai
Mission.snapshot(mission_id)                  -> Snapshot # provider-agnostic
Mission.resume(mission_id, snapshot?)         -> ok
Mission.cancel(ctx, mission_id, reason)       -> ok
Mission.artifacts(mission_id)                 -> ArtifactRef[]
```

States: `CREATED PLANNED RUNNING WAITING BLOCKED PAUSED VERIFYING COMPLETED FAILED CANCELLED`.

## C5. ModelGateway.* — `models/gateway`

```text
Gateway.complete(ctx, requirement, messages, tools?) -> Completion   # requirement ≠ provider
Gateway.stream(ctx, requirement, messages)           -> Stream
Gateway.embed(ctx, texts)                            -> vectors (cached)
Gateway.health()                                     -> ProviderHealth[]
Gateway.register(ProviderSpec)                       -> ok
```

`requirement` example: `{ capability: "coding", min_quality: "strong", max_latency_ms, budget }`.
**Invariants:** provider choice gateway ka kaam, agent ka nahi · policy (data class) hard filter · failover automatic + audited.

## C6. Agent.* — `agents/runtime`

```text
Agent.spawn(ctx, definition)          -> agent_id     # definition: tools, skills, profile, budget, scopes
Agent.send(agent_id, message)         -> ok           # mailbox, never shared transcript
Agent.blackboard(mission_id).post(e)  -> ok
Agent.status(agent_id)                -> { state, steps, cost, budget_left }
Agent.cancel(ctx, agent_id)           -> ok
Agent.retire(agent_id, reason)        -> ok
```

**Invariants:** agents hidden spaghetti calls nahi karte — sirf mailbox/blackboard/contracts · budget breach = fail + replan.

## C7. Computer.* — `computer/service`

```text
Computer.state(ctx)                       -> { apps, windows, foreground, monitors, clipboard_meta }
Computer.app.open/close/focus(ctx, target)-> ok
Computer.window.list/move/resize(ctx,…)   -> ok
Computer.files.read/write/move/copy(ctx,…)-> ok        # workspace-first, PTE scoped
Computer.shell.run(ctx, cmd, cwd, timeout)-> { exit, stdout, stderr }
Computer.input.mouse/keyboard/clipboard   -> ok        # LAST RESORT only
Computer.lock.acquire/release(ctx, res, ttl) -> lease
```

**Invariants:** har action ke baad **verification** zaroori · `USER_TAKEOVER` par conflicting input pause ·
automation priority follow (§ARCH 6) · workspace se bahar = confirm.

## C8. Browser.* — `browser/service`

```text
Browser.session.open(ctx, profile)      -> session_id
Browser.navigate(ctx, url)              -> PageState
Browser.dom.query(ctx, selector|semantic) -> Element[]
Browser.act(ctx, {click|type|select|upload|download}, target, value?) -> ok
Browser.extract(ctx, schema)            -> structured_data
Browser.screenshot(ctx, region?)        -> image_ref
```

**Invariants:** DOM available ho to raw mouse matlab nahi · page text **untrusted** (C11) · session lock mandatory.

## C9. Device.* — `devices/service`

```text
Device.discover()                     -> Device[]
Device.pair(ctx, device_id, pin)      -> ok
Device.capabilities(device_id)        -> CapabilityManifest
Device.command(ctx, device_id, action, params, idempotency_key) -> { command_id, status }
Device.cancel(ctx, command_id)        -> ok
Device.observe(device_id, stream)     -> Observation[]
Device.revoke(owner_ctx, device_id)   -> ok
```

**Invariants:** GENIE device-agnostic bolega (`media.next(phone_main)`) · adapter platform-specific ·
commands idempotent · offline → queue with TTL · heartbeat timeout → `DEVICE_OFFLINE`.

## C10. Skill.* — `skills/runtime`

```text
Skill.find(ctx, intent)               -> SkillRef[]
Skill.run(ctx, skill_id, inputs)      -> { ok, outputs, verification, stats_update }
Skill.create(ctx, definition)         -> skill_id      # draft
Skill.validate(skill_id)              -> { pass, tests }
Skill.deprecate(skill_id, reason)     -> ok
```

Skill manifest: `purpose, inputs, preconditions, steps, tools, verification, known_failures, examples, version, scopes`.

## C11. ContentSafety.* — `security/injection-guard`

```text
Safety.tag(content)          -> { tainted: bool, source, trust }
Safety.sanitize(content)     -> sanitized
Safety.guardAction(ctx, action) -> { allow|confirm|block, reason }
```

**Invariants:** untrusted content se aayi directives goal nahi badal saktin · exfiltration ke liye explicit confirm ·
taint propagate hota hai jab tak sanitize/verify na ho.

## C12. Artifact.* — `missions/artifacts`

```text
Artifact.put(ctx, mission_id, path, metadata) -> artifact_id
Artifact.get(artifact_id, version?)           -> { path, hash }
Artifact.versions(artifact_id)                -> version[]
Artifact.rollback(ctx, artifact_id, version)  -> ok
```

## C13. Event.* — `core/events`

```text
Event.publish(type, payload, ctx)     -> event_id
Event.subscribe(pattern, handler)     -> subscription_id
```

Payload schema-versioned · at-least-once · idempotent handlers · DLQ · handler block nahi kar sakta.
Catalog: master spec Appendix A.

## C14. Voice.* — `voice/`

```text
Voice.listen(ctx) -> Stream(SpeechEvent)     # AEC, NS, VAD, streaming STT
Voice.speak(ctx, text, profile, style) -> Stream(AudioChunk)
Voice.bargeIn(ctx) -> ok                     # TTS stop + generation redirect
Voice.profiles() -> VoiceProfile[]           # cloned voice = consent required
```

## C15. Context.* — `context/engine`

```text
Context.build(ctx, target_agent) -> ContextPacket
```

Priority (overflow par summarize, safety kabhi drop nahi):
`system/contract > mission state > safety/policy > recent turns > retrieved memory > examples`.

## C16. Ops.* — `ops/`

```text
Ops.trace(trace_id)          -> spans
Ops.audit.query(filter)      -> entries
Ops.backup.run(ctx)          -> backup_id
Ops.update.apply(ctx, ver)   -> { ok, rolled_back? }
Ops.metrics()                -> { success_rate, wrong_action_rate, latency, cost, provider_health }
```

---

## Versioning

| Change type | Action |
|---|---|
| Additive (naya optional field/method) | minor bump, contract test |
| Behaviour change | minor bump + `CHANGELOG` + notice |
| Removal / rename / semantics | **major bump + RFC + migration note** |

Sab contracts ke liye **contract tests mandatory** (har module ke `TESTS.md` mein listed).
