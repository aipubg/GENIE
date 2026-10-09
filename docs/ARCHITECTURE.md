# GENIE — ARCHITECTURE

> **Authoritative spec:** [`GENIE_MASTER_SPEC_v1.md`](./GENIE_MASTER_SPEC_v1.md) (FROZEN v1.0)
> Ye file uska **navigation + implementation view** hai. **Conflict hone par master spec jeetega.**
> Is file ko padhne ke baad module-spec kaam ke liye `CONTRACTS.md` + module ka `CONTRACT.md` padho.

---

## 1. Ek paragraph mein GENIE

GENIE ek **persistent personal intelligence platform** hai. Local **NEDLE2** director requests ko route karta hai;
**Memory Fabric** truth rakhti hai; **Mission Engine** kaam ko stateful/resumable banata hai; **Provider Gateway**
remote models ko interchangeable rakhta hai; **Capability Bus** computer / browser / devices / plugins / skills ko
ek hi uniform interface se chalaata hai. Models badalte hain — **GENIE nahi badalta.**

## 2. Runtime topology

```text
                Voice · Screen · Camera
                        │
                 PERCEPTION / EVENTS
                        │
              ┌─────────────────────┐
              │   NEDLE2 DIRECTOR   │  ← sirf local LLM
              └──────────┬──────────┘
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
  MEMORY FABRIC    MISSION ENGINE     MODEL GATEWAY
        │                │                 │
        │          AGENT RUNTIME      API PROVIDERS
        │                │
        │        ┌───────┼───────┐
        │     Agent A  Agent B  Agent C
        └───────┴───────┴───────┘
                        │
                 CAPABILITY BUS ──── [ PTE / VAULT / AUDIT ]  ← har call inke through
                        │
     ┌──────────┬───────┼────────┬──────────┐
  COMPUTER   BROWSER  SKILLS  PLUGINS    DEVICES
     │                                  │
  Workspace                      Phone · Pi · PC2
```

**Har capability call PTE (permission) → execute → verify → audit** se guzarti hai. Koi shortcut nahi.

## 3. The 8 layers (ek line kaamdhandha)

| # | Layer | Ek line | Truth owner |
|---|---|---|---|
| 1 | Identity + Trust | Kaun hai, kya kar sakta hai | `security/` (PTE, Vault) |
| 2 | Director + Mission | Kya karna hai, kis state mein hai | `missions/`, `director/` |
| 3 | Memory + Context | Kya yaad hai, kya bhejna hai model ko | `memory/`, `context/` |
| 4 | Models + Agents + Skills | Kaun sochta hai, kaun karta hai | `models/`, `agents/`, `skills/` |
| 5 | Computer + Browser + Plugins | PC aur apps kaise chalte hain | `computer/`, `browser/`, `plugins/` |
| 6 | Devices + Perception | Baaki devices + environment | `devices/`, `perception/` |
| 7 | Voice + Comm + Proactivity | Kaise bolta hai, kab bolta hai | `voice/`, `context/communication` |
| 8 | Ops + Security + Testing | Kaise chalta/secure/improve hota hai | `ops/` |

## 4. Process model

```text
genie-daemon      ← core: kernel, bus, NEDLE2, services, missions (always on)
├─ plugin-host    ← alag process; crash ≠ daemon crash
├─ computer-svc   ← Windows control, locks
├─ device-svc     ← device mesh + networking
└─ voice-svc      ← audio pipeline

genie-ui          ← Electron/desktop (front-end only)
genie-companion   ← floating widget (independent of main UI)
genie-node-*      ← Android / Pi / second PC agents
```

Rules:
- UI crash, plugin crash, device offline — **koi bhi GENIE crash nahi hai** (invariant 12).
- Daemon hi missions own karta hai; UI sirf subscribe karta hai.
- Har service independently restartable + health-checkable.

## 5. Chaar core flows

**A. Voice → action**
```text
mic → AEC/NS/VAD → STT → NEDLE2 (classify) → PTE check
→ [simple] capability call  |  [complex] mission create
→ execute → verify → respond → memory write (async)
```

**B. Mission lifecycle**
```text
CREATED → PLANNED → RUNNING (WAITING|BLOCKED|PAUSED|VERIFYING)
→ COMPLETED | FAILED | CANCELLED
```
Har transition event + audit. Mission resumable; state sirf Mission Service likhta hai.

**C. Provider failover**
```text
provider quota/circuit open → mission snapshot (provider-agnostic)
→ gateway picks alternate (policy+health+cost) → resume
```
Snapshot mein: goal, plan, completed steps, artifacts, decisions, errors, agent state, relevant memory.

**D. Memory write**
```text
event → NEDLE2 *proposes* → Memory Service validates/dedupes
→ supersede (never overwrite) → MEMORY_UPDATED → embed (online) / FTS (offline)
```

## 6. Automation priority (reliability ka base)

```text
Native API/Plugin → OS Automation → Accessibility Tree
→ Browser DOM → Vision → Raw Mouse+Keyboard (last resort)
```

## 7. Data stores (Phase 1)

```text
SQLite (WAL)  — structured tables, FTS, graph tables
vector cache  — provider embeddings, content-hash keyed
artifact store— files + versions, hash-addressed
logs          — trace spans + append-only audit (hash chain)
```
PostgreSQL tabhi jab multi-device concurrency genuine ho. **Pehle din DB zoo nahi.**

## 8. Security boundaries (non-negotiable)

- Default **deny**; har capability ko grant chahiye.
- External content (web/PDF/email/file) = **DATA, authority nahi** → taint + injection guards.
- Secrets kabhi model context mein nahi; sirf vault reference.
- `RESTRICTED`/`SECRET` data class kabhi remote provider ko nahi.
- Security tooling (Decepticon/hack-skills) sirf isolated authorized environment mein.

## 9. Tech stack

**OPEN — decision required.** See [`DECISIONS.md`](./DECISIONS.md) `D-001`.
Recommendation: Python 3.13 (core services, Windows control, agent runtime) + TypeScript/Electron (desktop UI,
companion) + Kotlin (Android node). Rationale: Windows automation ecosystem (pywin32, uiautomation, pywinauto)
Python mein strongest hai; UI/companion ke liye Electron best; `openclaw` Android app reference ke kaam aata hai.

## 10. Kahin bhi doubt ho to

1. Master spec §11 — **12 frozen invariants** (ye kabhi mat todo).
2. Module ka `CONTRACT.md`.
3. `DECISIONS.md` — pehle kya decide ho chuka hai.
4. Wahan bhi na ho → **RFC kholo, apni taraf se invent mat karo.**
