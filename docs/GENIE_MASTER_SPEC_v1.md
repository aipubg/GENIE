# GENIE — MASTER SPECIFICATION v1.0

> **Status: BASELINE / CONTROLLED SPEC.** Yeh implementation reference hai. Koi bhi AI developer apni taraf se architecture redesign nahi karega — lekin agar real implementation se saabit ho ki koi assumption galat hai, to **evidence → RFC/decision entry → contract update → implementation** allowed hai. Invariants (§11.1/§11.2) implementation details se **bahut zyada hard** hain — unhe todne ke liye owner approval chahiye.

| Field | Value |
|---|---|
| Spec ID | `GENIE-MASTER-SPEC` |
| Version | `v1.0` (baseline) |
| Baseline on | 2026-09-16 |
| Supersedes | Sabhi ad-hoc architecture notes (sections 1–88 + cross-cutting addendum) |
| Audience | Human owner, AI developers, agent runtimes, evaluation harness |
| Normative language | RFC 2119 — **MUST / MUST NOT / SHOULD / MAY** |
| Live implementation | `genie.py` + `core/`, `security/`, `memory/`, `missions/`, `models/`, `director/`, `computer/`, `agents/`, `context/`, `ui/` |

---

## §0 — PREAMBLE

### 0.1 GENIE kya hai

GENIE ek **general-purpose persistent agent system** hai jiski surface experience Jarvis-jaisi hai. Technically ise "AGI" kehna galat hoga, aur hum aisa claim nahi karte.

GENIE ki operating loop:

```text
देखना → सुनना → पहचानना → याद रखना → स्थिति समझना
→ काम plan करना → सही AI/agent चुनना → PC/phone/browser/apps operate करना
→ result verify करना → experience से सीखना → जरूरत हो तो खुद initiative लेना
→ same identity के साथ लगातार चलना
```

**GENIE ki identity kisi Gemini/Claude/DeepSeek/OpenAI model mein nahi rehne wali.**
Models badlenge. **GENIE nahi badlega.**

### 0.2 Ek product, kai repos

GENIE ek **product** hai; repos uska **raw material** hain. Kisi bhi individual repo ki architecture GENIE ko dictate nahi karegi.

```text
Library clean hai                 → dependency
Standalone system hai             → service
Useful component hai              → adapter / extraction
License incompatible hai          → isolated service / replacement
```

Har reused source ke saath provenance MUST rahe: `SOURCE.md`, `LICENSE`, upstream version, modified files list, GENIE adapter boundary.

### 0.3 Non-goals (spec ke bahar)

- Medical / emotional truth claims (mood inference sirf communication preference ke liye).
- Unrestricted offensive security tooling — hamesha isolated authorized environment mein.
- GENIE ka khud ka live source rewrite bina isolated branch → tests → benchmark → adoption flow ke.
- Har capability ka pehle din local heavy model banana (local sirf NEDLE2).
- Prediction/simulation output ko truth maanna.

### 0.4 Change control (baseline spec)

Spec **baseline** hai, untouchable permanent architecture nahi. Do levels hain:

**Level 1 — implementation detail (easy to change):**
Agar implementation se saabit ho ki koi assumption galat hai:

```text
evidence → RFC/decision entry (docs/DECISIONS.md) → contract update (docs/CONTRACTS.md)
→ implementation → tests → CHANGELOG
```

Kisi bhi AI developer ko randomly redesign karne ki chhoot nahi hai, lekin **galat architecture sirf isliye maintain nahi karenge ki v1 mein likha tha.**

**Level 2 — Frozen Invariants (§11.1, §11.2):**
Ye **bahut zyada hard** hain. Todne ke liye owner approval + major version bump + migration plan zaroori hai.

**Versioning:** clarification = `v1.0.x` · behaviour change = `v1.x` · invariant change = `v2.0` (owner only).

Koi bhi agent, plugin, skill ya AI developer implicitly spec ko redefine nahi kar sakta.

### 0.5 AI developers is spec ka use kaise karein

Har task par poora repository dump dena **mana** hai. Context = sirf relevant modules.

Example — "Android device bridge fix karo":

```text
ARCHITECTURE.md, CONTRACTS.md
devices/README.md, devices/CONTRACT.md
devices/android/*
core/contracts/device.*
```

Dusre module mein tabhi change jab **contract change** ho, aur wo RFC ke through.

### 0.6 Reading order by role

| Role | Read |
|---|---|
| AI developer (module task) | §0, §9.1, apna module, §11 |
| Agent runtime implementer | §2, §4, §11 |
| Integration/plugin author | §5.4, §5.5, Appendix D |
| Device/embedded author | §6, Appendix D |
| Evaluator / QA | §8.6–§8.9, Appendix E |
| Owner (product decisions) | §0, §1, §7.5, §10, §11 |

### 0.7 Layer map (8 permanent layers)

```text
1. Identity + Trust
2. Director + Mission Control
3. Memory + Context
4. Models + Agents + Skills
5. Computer + Browser + Plugins
6. Devices + Perception
7. Voice + Communication + Proactivity
8. Operations + Security + Testing + Updates
```

---

## §1 — LAYER 1: IDENTITY + TRUST

Ye layer sabse upar isliye hai kyunki bina trust model ke memory, devices aur computer control sab liability ban jaate hain.

### 1.1 Identity model

GENIE ki identity **persistent** hai aur in cheezon se milti hai:

```text
Persistent identity
= Memory Fabric + Mission continuity + Skill library + Voice/personality profile
  + Device mesh + Owner relationship graph
```

Identity kisi provider, kisi model checkpoint, ya kisi single repo mein nahi basati.

**Identity guarantees (MUST):**

- Model/provider swap se identity, memory, skills, behaviour nahi badalne chahiye.
- Har GENIE instance ka ek `instance_id` aur har user ka `person_id` hota hai.
- GENIE apne aap ko doosre assistant ya doosre person ke roop mein present nahi karega.

### 1.2 Users, personas aur sessions

| Concept | Meaning |
|---|---|
| `owner` | Primary user; full privileges; GENIE ka "Boss" |
| `member` | Family/trusted user; apni private memory + shared projects |
| `guest` | Limited, sandboxed; koi persistent personal memory nahi |
| `system` | GENIE khud; audited, scoped |
| `agent:<id>` | Non-human principal; scoped, time-boxed |
| `device:<id>` | Device principal; capability manifest se bound |

Har session mein MUST record ho: `session_id`, `person_id`, `auth_method`, `device_id`, `room`, `trust_tier`, `started_at`.

### 1.3 Trust zones aur data classification

Har data item classified hoga. Classification provider choice, memory scope aur log retention decide karti hai.

| Class | Examples | Default handling |
|---|---|---|
| `PUBLIC` | Web page, docs | Kisi bhi provider ko |
| `INTERNAL` | App list, window titles, generic commands | Approved providers |
| `SENSITIVE` | Personal files, project source, conversations | Policy-approved providers only; audit |
| `SECRET` | API keys, tokens, passwords | **Kabhi provider ko nahi**; vault reference only |
| `RESTRICTED` | Biometrics, private media, other-person data | Local only unless explicit grant |

**Trust tiers for devices:** `owner_primary` > `owner_secondary` > `shared` > `guest` > `untrusted`.
Untrusted device sirf observe/request kar sakta hai, act nahi.

### 1.4 Permission & Trust Engine (PTE)

Ye ek **alag service** hai — NEDLE2 ya koi agent permission decide nahi karega.

**Scope grammar:**

```text
<domain>:<resource>:<action>[:qualifier]

computer:mouse:control
computer:files:write:GENIE/workspace/**
device:phone_main:media.control
memory:user:read
plugin:spotify:play
secrets:provider:* :use
```

**Grant types:**

| Grant | Lifetime | Use case |
|---|---|---|
| `one_time` | Single action | Ek baar file delete |
| `session` | Session end tak | Aaj ke liye browser automation |
| `standing` | Revocable, audited | Spotify control, workspace write |
| `conditional` | Rule-based | "Sirf GENIE workspace mein install allowed" |

**Rules (MUST):**

- Default = **deny**. Capability tabhi milegi jab grant exist kare.
- Har grant mein: `principal`, `scope`, `granted_by`, `granted_at`, `expires_at`, `revoked_at?`, `reason`.
- Agents ko **least privilege** milta hai; Agent Factory (§4.6) grant request karta hai, grant dena PTE ka kaam hai.
- Devices apne capability manifest ke bahar kuchh request nahi kar sakte.
- Plugins sandbox ke andar; unke declared permissions install time par dikhne chahiye.
- Har PTE decision audit log mein jaata hai (§8.2).

**Destructive action confirmation matrix:**

| Action class | Owner | Member/Guest |
|---|---|---|
| Read / observe | allow | allow (scoped) |
| Reversible write in GENIE workspace | allow | allow (workspace only) |
| Write outside workspace | confirm (short) | deny/confirm-owner |
| Delete / overwrite / format / uninstall | **explicit confirm + undo plan** | deny |
| System config, services, registry | confirm | deny |
| Financial / purchase / send-message | explicit confirm + dry summary | deny |
| Credential use / export | confirm, never display raw | deny |
| Physical device actuation (relay, lock) | confirm | deny |

Escalation: agent `standing` grant nahi maang sakta apne aap; sirf `one_time` request + human confirm.

### 1.5 Secrets Vault

- API keys, OAuth tokens, device pairing secrets, plugin credentials — sab vault mein.
- Modules sirf **reference** (`secret://provider/openai/key`) rakhte hain; raw value kabhi code/config/log/context packet mein nahi.
- Vault MUST support: rotation, refresh (OAuth), revocation, per-scope issuance, audit of every use.
- Provider call ke waqt secret sirf gateway process mein resolve hota hai, phir memory se drop.
- Export/backup encrypted; key derivation owner-controlled.
- Koi bhi secret LLM context mein MUST NOT jaaye — chahe user maang le (system refuse with explanation).

### 1.6 Model Policy Registry

Provider blind choice nahi hota. Registry define karta hai:

| Policy field | Example |
|---|---|
| `allowed_data_classes` | `[PUBLIC, INTERNAL]` |
| `vision_allowed` | true/false + max resolution + retention |
| `code_execution_allowed` | false |
| `regions_allowed` | jurisdiction constraints |
| `retention_policy` | provider-side retention expectation |
| `fallback_allowed_from` | list of providers |
| `max_cost_per_mission` | budget ceiling |
| `sensitive_topics` | finance/health — restricted providers only |

Rules:

- `SECRET`/`RESTRICTED` data class kabhi remote provider ko nahi (except explicit, logged, owner-granted exception).
- NEDLE2 "strong coding model" maangta hai, exact provider nahi; gateway policy + health + cost se choose karta hai.
- Policy violation = hard block + audit event `POLICY_VIOLATION_BLOCKED`.

### 1.7 Untrusted content boundary (prompt-injection defense)

**Core rule: external content = DATA, authority nahi.**

Untrusted sources: web pages, PDFs, emails, downloaded files, chat from guests, OCR text, third-party plugin output, screen text from unknown apps.

Controls (MUST):

1. **Provenance tagging** — har content chunk ke saath `source`, `trust=untrusted`, `fetched_at`.
2. **No instruction inheritance** — untrusted content se aaye directives agent ke goal ko change nahi kar sakte.
3. **Capability firewall** — untrusted content ke context mein naye tool grants, naye device targets, ya secrets access automatically allow nahi hote.
4. **Exfiltration guards** — untrusted context mein maujood data ko outbound network/email/message bhejne ke liye explicit confirm.
5. **Two-phase actions** — agar untrusted content ne action trigger kiya, to plan pehle user ko dikhega, phir execute.
6. **Taint tracking** — untrusted se derived text tainted rehta hai jab tak sanitize/verify na ho.
7. **Render safety** — untrusted text UI mein rendered, not executed.

### 1.8 Multi-user model

- Har `person_id` ki alag: personal memory, preferences, voice profile, conversation history.
- Shared spaces: `household`, project spaces — explicit share scopes ke saath.
- Device ownership: `phone_main → owner`; shared TV → `household`.
- Guest: koi persistent memory write nahi; session end par purge (unless owner ne retain kaha).
- Cross-user request (member asks owner data) → deny + notify owner, except explicit share.
- Conflict: do users ek hi resource par → lock + notify (§2.9).

### 1.9 Privacy controls

| Control | Behaviour |
|---|---|
| Always-listening toggle | Per-room, hardware-level mute respected |
| Hardware mute state | honoured immediately, mic stream dropped, indicator shown |
| Camera indicator | Mandatory visible/audible indicator when camera active |
| Per-room sensing policy | e.g. bedroom → mic only on explicit wake, no camera |
| Retention | Raw audio retained only if `improve_speech` opt-in; default transcript-only, TTL-bounded |
| Local-first | Personal memory primary system par local; cloud ko sirf required context |
| Purge | "Forget X" → hard delete across index/vector/cache/backup queue |

### 1.10 Safe Mode aur kill switch

Recovery / Safe Mode MUST exist:

- Boot with: core kernel + event bus + NEDLE2 + memory read-only + no plugins + no devices + no network providers.
- Triggers: failed update boot, runaway agent (resource/lock violation), repeated plugin crash, manual `--safe`.
- Safe mode se: disable plugin, revoke grant, rollback update, export diagnostics, purge agent.
- **Kill switch:** single action (UI + voice + hotkey) jo sab agent activity, device commands aur computer control turant rok de; missions `PAUSED` mein jayengi, state safe rahegi.

### 1.11 User Control Center

Owner ko hamesha pata hona chahiye:

```text
क्या याद है?          → memory browser + forget/edit
कौन agent चल रहा है?  → active agents, scope, budget, kill
कौन device connected? → device mesh + trust tier + revoke
कौन provider data देख रहा है? → per-mission provider log + policy
क्या kharcha hua?     → cost dashboard
kaunsi permission kise? → grant list + revoke
```

Ye debug UI nahi — normal product surface hai.

---

## §2 — LAYER 2: DIRECTOR + MISSION CONTROL

### 2.1 NEDLE2 kya hai

NEDLE2 GENIE ka smartest model nahi hai. Wo hai:

> **GENIE ka traffic controller / dispatcher / mission director.**

Use giant reasoning nahi karna — use **tez** decide karna hai.

**NEDLE2 ke sawal:**

```text
यह request किस type की है?
किस device से related है?
कौन-सा agent चाहिए?
कोई existing skill है?
कौन-सी memories relevant हैं?
क्या बड़ा model चाहिए?
किस category का provider चाहिए?
क्या plugin available है?
task simple है या mission बनाना है?
क्या agent बनाना पडेगा?
event ignore करना है या process?
GENIE को बोलना चाहिए या चुप रहना चाहिए?
```

Example:

```text
"Phone में अगला song कर और PC पर Blender खोल."
```

```json
{
  "tasks": [
    { "type": "device_action", "device": "phone_main", "capability": "media.next" },
    { "type": "application_action", "device": "pc_main", "capability": "application.open", "target": "blender" }
  ],
  "reasoning_required": false
}
```

Iske baad actual execution deterministic services karte hain.

### 2.2 NEDLE2 kya **nahi** karega

Ye distinction project bachata hai. NEDLE2 directly nahi sambhalega:

```text
Database integrity      Device authentication   Secrets
Actual mission status   File locks              Permissions
Process IDs             Agent ownership         Audit history
Application state       Network sessions
```

Inka source-of-truth code/services hain:

```text
NEDLE2          = decision
Mission Service = truth
Memory Service  = truth
Device Service  = truth
Computer Service= truth
```

Agar NEDLE2 hi memory truth hota aur usne hallucinate kar diya — "Agent X ne kaam complete kar diya" — to mission corrupt ho jaata. Isliye classification galat hone par correction possible hona chahiye.

### 2.3 Local LLM policy

```text
LOCAL                          REMOTE PROVIDERS
━━━━━━━━━━━━━━━━━━━━           ━━━━━━━━━━━━━━━━━━━━━━
NEDLE2                         Reasoning LLM
GENIE Kernel                   Coding LLM
Memory DB / Mission DB         Vision LLM
Device services                Research LLM
Computer control               Large-context model
Browser controller             Image / Video generation
Plugins / Event system         Speech intelligence
                               Embeddings / Specialist models
```

- **Sirf NEDLE2 local reasoning model hai.** Koi doosra reasoning/generative model local nahi.
- VAD, audio DSP, screen capture, motion detection "LLM" nahi hain — lightweight utilities local chal sakti hain.
- Is constraint ka matlab: ComfyUI / OmniVoice / Kronos core PC par default nahi chalenge. Unke **adapters** rahenge; wo external GPU worker / provider ke roop mein connect honge.

### 2.4 Mission Engine

Har serious kaam **Mission** banega.

```text
CREATED → PLANNED → RUNNING
   ├─ WAITING      (external dependency / human input)
   ├─ BLOCKED      (lock / permission / provider)
   ├─ PAUSED       (user / safe mode / resource)
   └─ VERIFYING    (result check)
        ↓
    COMPLETED

   or FAILED / CANCELLED
```

Mission record MUST store:

```text
goal, owner, target devices, agents, steps, dependencies,
artifacts, current status, previous attempts, errors,
provider usage, cost, completion criteria, trace_id
```

Rules:

- Mission state sirf Mission Service likh sakta hai. Agent/NEDLE2 sirf propose.
- Har state transition event emit karta hai (Appendix A) aur audit hota hai.
- Completion criteria pehle se defined hona chahiye — "lag raha hai ho gaya" acceptable nahi.
- Har mission resumable honi chahiye (crash recovery §8.5).

### 2.5 Agent Runtime

Har task ke liye 100 agents nahi chalenge. NEDLE2 decide karta hai kitne chahiye:

| Complexity | Example | Execution |
|---|---|---|
| Simple | "Volume 30" | **Koi LLM worker nahi** — direct capability call |
| Moderate | "Ye article summarize kar" | One agent |
| Complex | "Desktop application banao" | Agent team |

### 2.6 Multi-agent team

```text
Lead
 ├── Architect
 ├── Backend
 ├── Frontend
 ├── Test
 └── Reviewer
```

Har agent shared giant conversation nahi padhega. Shared infrastructure:

```text
Mission Board · Task DAG · Mailbox · Blackboard · Artifact Store · Memory API
```

- Agent A, Agent B ko **message** bhejta hai — poore hidden context ki copy nahi.
- Blackboard = structured shared state, append-only entries with `author`, `ts`.
- Agents ke beech hidden spaghetti calls mana hain; communication **contracts/state** ke through.

### 2.7 Agent Factory

```text
Task → NEDLE2 → Agent Factory → Agent Definition
     → Skill attachment → Tool attachment
     → Provider profile (policy-bounded) → Run
```

Example generated agent:

```yaml
name: blender-material-fixer
purpose: Repair Blender material problems
tools: [blender-plugin, filesystem]
skills: [blender-material-debugging]
model_profile:
  reasoning: medium
  vision: true
memory:
  project: current
permissions:
  scopes: [plugin:blender:*, computer:files:read]
  grant_type: one_time
budget:
  max_cost: 0.50
  max_steps: 40
```

Task ke baad: useful → retain; generic duplicate → discard (§4.7).

### 2.8 Scheduler aur background jobs

Scheduler ek **service** hai, agent nahi.

- Job types: `once`, `recurring` (cron-like), `conditional` (event-triggered), `long_running` (mission-backed).
- MUST support: missed-job policy (skip / run-late / notify), timezone + DST, wake-from-sleep, retry with backoff + jitter, idempotency key, concurrency guard (`max_instances`, `skip_if_running`).
- Recurring job → mission ban sakti hai; dono alag cheezein hain (job = trigger, mission = execution).
- Jobs PTE scopes ke under chalte hain; scheduled job ko apne aap naye grant nahi milte.

### 2.9 Concurrency aur locking

Ek important missing problem: kai agents ek hi cheez edit kar sakte hain.

Lock types (MUST exist):

```text
desktop control lock · file lock · application lock
project write lock · device lock · browser session lock
```

Rules:

- Computer Agent aur doosra agent ek saath mouse nahi chala sakte.
- Locks **lease-based** (TTL + heartbeat), na ki permanent; process marne par expire.
- Lock wait queue + timeout + deadlock detection (ordered acquisition ya wait-graph).
- Lock acquisition/release audit hota hai; stale lock auto-release safe-mode se possible.

### 2.10 Cancellation

Har long-running mission cancelable hogi.

```text
user: "GENIE stop."
  ↓ mission cancellation
  ↓ provider stream cancel
  ↓ agent cancel (cooperative, then forced)
  ↓ browser/tool stop
  ↓ temporary locks release
```

- Cancellation **first-class feature** hai, afterthought nahi.
- Partial artifacts preserve honge with `status=partial`.
- Provider billing stop best-effort; cancel event audit mein.

### 2.11 Human takeover model

Agar user beech mein mouse/keyboard chhoo le:

```text
USER_TAKEOVER event
```

Rules:

- Input se **N ms** andar GENIE conflicting mouse/keyboard actions pause karega.
- Decide: `pause_mission` (default) / `continue_parallel` (sirf non-conflicting, e.g. background download) / `abort`.
- Resumption: explicit user action ya idle timeout ke baad; resume par state re-verify zaroori (screen/app state badal sakta hai).
- Takeover events memory mein "interaction pattern" ke roop mein jaate hain, lekin punitive nahi.

---

## §3 — LAYER 3: MEMORY + CONTEXT

GENIE ki sabse valuable cheez models nahi — **Memory Fabric** hai.

### 3.1 Memory architecture

```text
Memory
├── Working Memory          (current turn/session scratch)
├── Conversation Memory     (dialogue history, summarized)
├── Episodic Memory         (events: "kal Blender export kiya")
├── User Memory             (owner profile, habits)
├── Preference Memory       ("bright themes pasand nahi")
├── Semantic Knowledge      (general facts)
├── Project Memory          (active project context)
├── Mission Memory          (mission-scoped facts)
├── Agent Memory            (agent performance, quirks)
├── Skill Memory            (which skill worked where)
├── Device Memory           (devices, capabilities, quirks)
├── Application Memory      (installed apps, paths, versions)
├── Environment Memory      (rooms, hardware, network)
└── Relationship Graph      (person→device→project→app edges)
```

Memory ka kaam sirf "user ne kya kaha" rakhna nahi. Use pata hoga:

```text
हम किस project पर काम कर रहे हैं · कौन agent active है · कौन task blocked है
किस solution ने पहले काम किया · कौन application कहाँ installed है
कौन device किसका है · किस provider ने failure दिया
कौन workflow reliable है · कौन skill purani ho gayi
```

### 3.2 Memory write pipeline

Har line permanently save nahi hogi.

```text
New Event
   ↓ NEDLE2 memory classification (proposal only)
   ↓ Memory Service (validation, dedupe)
   ↓ Existing memory? → merge / supersede
   ↓ confidence + source + privacy scope
   ↓ write + event MEMORY_UPDATED
```

Example — tum casually bolo: "मुझे वैसे bright themes पसंद नहीं।"

```text
type  = preference
entity= UI theme
value = prefers darker themes
```

Memory Service existing preferences ke saath merge karega.

**Rule:** NEDLE2 bole "ye save karne layak hai" — actual save Memory Service karega.

### 3.3 Memory metadata (har record par MUST)

```text
source · timestamp · confidence · person · project · mission
privacy_scope · last_accessed · last_updated · superseded_by? · provenance
```

- Purani galat memory **overwrite nahi, supersede** hogi.
- Har memory immutable version chain maintain karti hai.

### 3.4 Retrieval

User: "कल वाला Blender method use करना।"

```text
NEDLE2 → memory query intent → Memory Service
       → Project + Episode + Skill retrieval
       → Context Builder → Agent
```

Model ko **poori lifetime history nahi** bhejenge — sirf relevant context.

Retrieval MUST blend: recency, relevance, importance, mission/project affinity, diversity (MMR-style), privacy filter, PTE read scope filter.

### 3.5 Strong search bina local embedding model ke

Kyunki local embedding model nahi chahiye:

**Online:**

```text
provider embeddings → vectors locally cached (with content hash + model version)
```

**Offline fallback:**

```text
full-text search (FTS) · structured fields · graph relations
recent memory · exact entity matching
```

Isse memory unusable nahi hogi. Embedding cache miss par async backfill.

### 3.6 Memory correction, deletion, versioning

Ye Layer 1 (Trust) ka hissa bhi hai, lekin implementation Memory Service ka.

| Operation | Behaviour |
|---|---|
| `correct` | Naya version; purana `superseded_by` link; contradiction resolve policy |
| `forget(entity/scope)` | Hard delete across primary tables, FTS index, vector cache, backup queue |
| `export(person)` | Machine+human readable bundle |
| `rollback(version)` | Restore previous version, logged |
| `merge` | Duplicate detection → canonical record + aliases |
| `pin` | Protect from auto-summarization/TTL |

Contradiction resolution: higher confidence + more recent + explicit user statement > inferred. Tie → ask user (once), then remember the answer.

Privacy boundaries: `private:<person>` / `shared:<space>` / `system`. Cross-person read MUST NOT happen without share scope.

### 3.7 Context Engine

Har LLM call se pehle Context Builder relevant situation banata hai.

```yaml
user: owner
location: office
active_activity: coding
project: GENIE
mission: build Android device bridge
computer:
  active_app: vscode
agents:
  android_worker: working
recent_memory:
  - device protocol already defined
communication: concise
permissions:
  allowed_scopes: [...]
provenance:
  untrusted_blocks: 0
```

Yahi packet model ko jaata hai. Lifetime memory dump nahi.

Context budget rules (MUST): token budget precomputed; priority order = system/contract > mission state > safety/policy > recent turns > retrieved memory > examples; overflow → summarize older, never silently drop safety/policy block.

### 3.8 Sync Engine

Multi-device GENIE (PC + phone + second PC) ke liye.

- Sync units: memory records, mission state, device registry, artifacts metadata, skills, settings/grants.
- Local-first: har node apna local write karta hai; sync replicator reconcile karta hai.
- Conflict resolution: **per-type policy** —
  - Mission state → authoritative owner = Mission Service on node that owns mission; LWW unsafe, use version vectors.
  - Memory → version chain + supersede, last-writer-wins only when no causal conflict; else both kept + contradiction flag.
  - Settings/grants → owner-authoritative.
  - Artifacts → content hash + version, binary via artifact store, not DB.
- MUST: causal metadata (version vector / HLC), idempotent ops, replay-safe, offline queue, delta sync, bandwidth-aware (thumbnails first), and **conflict UI** in User Control Center.
- Sync MUST NOT leak `RESTRICTED` data to lower-trust nodes.

### 3.9 Knowledge graph bina Neo4j ke

Relations:

```text
Person  ─owns→      Device
Person  ─works_on→  Project
Project ─uses→      Application
Mission ─belongs_to→Project
Agent   ─working_on→Mission
Skill   ─works_with→Application
```

Pehle **relational graph tables** kaafi hain. Neo4j tabhi jab traversal query load genuinely zaroori ho.

### 3.10 Storage

```text
SQLite (WAL) + structured relation tables + FTS + cached provider embeddings
+ artifact store + logs
```

PostgreSQL migration tab jab multi-device concurrency genuinely badhe. Pehle din unnecessary DB zoo nahi.

---

## §4 — LAYER 4: MODELS + AGENTS + SKILLS

### 4.1 Provider Gateway

Models ko directly agents mein hardcode nahi karenge.

```text
Agent → Model Requirement → Provider Gateway → Model Registry → Available provider
```

Registry records (MUST):

```text
model · provider · capabilities · context window · latency
cost (in/out) · vision? · tools? · reasoning? · coding?
availability · current health · quota · data policy class
```

- NEDLE2 kahega: "मुझे strong coding model चाहिए." Wo zaroori nahi kahega "use provider XYZ".
- Gateway current availability + policy (§1.6) + cost se choose karega.

### 4.2 Health, failover, snapshot

```text
Provider A → quota exhausted → Mission Snapshot → Provider B → continue
```

Model change ke bawajood bacha rahega:

```text
Goal · Current plan · Completed steps · Artifacts
Decisions · Errors · Agent state · Relevant memory
```

- Health checks: periodic + passive (error rate, latency, timeouts) → circuit breaker.
- Snapshot MUST be provider-agnostic (no provider-specific message format inside).
- Failover audit + cost accounting per attempt.

### 4.3 Cost & Quota Controller

- Budgets: `per_mission`, `per_hour`, `per_day`, `per_provider`, `per_project`.
- Soft limit → degrade (chhota model, fewer retries) + notify; hard limit → block + ask.
- Escalation rule: cheap model pehle; escalate to expensive only on failure/low confidence or explicit complexity class.
- Token accounting MUST be per mission, per agent, per provider; visible in dashboard (§8.10).
- Rate limits: token bucket per provider; queue with priority; shed low-priority background jobs first.
- MUST record: `provider_calls`, `tokens`, `cost`, `retries`, `cache_hits`.

### 4.4 Skills system

GENIE ke kaam karne ke proven tareeke **Skill** banenge. Skill ≠ prompt file matra.

Skill contains (MUST):

```text
Purpose · Inputs · Preconditions · Steps · Tools
Verification · Known failures · Examples · Version · Success statistics
```

Examples: `blender-export-fbx`, `spotify-play-playlist`, `deploy-react-app`, `organize-download-folder`, `fix-node-dependency-conflict`.

### 4.5 Skill learning

```text
Successful mission → Candidate procedure → Generalization
→ Sandbox test → Skill created → Versioned
```

GENIE agali baar scratch se solve nahi karega.

### 4.6 Teaching mode

Tum manually software operate karte ho aur kehte ho: "GENIE, इसे सीखना।"

System records:

```text
active application · UI elements · clicks · keystrokes
files · state changes · result · timestamps
```

Raw macro save nahi hoga. Large model se generalize hoga:

```text
demonstration → intent → stable steps → variable parameters → verification → skill
```

Isliye resolution badalne par skill nahi tootegi. Teaching recorder MUST respect privacy capture policy (§1.9) — sensitive fields masked by default.

### 4.7 Skill lifecycle

| Stage | Rule |
|---|---|
| `draft` | From teaching/generalization; not auto-runnable |
| `validated` | Passed sandbox + golden test; runnable |
| `deprecated` | Success rate drop / app version incompatible |
| `retired` | Removed from router; retained for provenance |

- Har skill: version, compatibility range (app versions), success stats, last_used, regression tests.
- Duplicate skills: similarity check → merge with alias, keep best-performing body.
- Skills PTE scopes ke under chalte hain; skill install par declared requirements dikhne chahiye.
- **Skill install hote hi arbitrary code trusted nahi hota** (§5.5).

### 4.8 Agent lifecycle

| Property | Rule |
|---|---|
| Creation | Agent Factory + explicit scope/budget |
| Type | `ephemeral` (task-scoped) vs `persistent` (named, retained) |
| Idle cleanup | Ephemeral agents idle timeout par reap; state archived |
| Budget | max steps, max cost, max wall time; breach → `AGENT_FAILED` + mission replan |
| Ownership | Har agent ka owner (person/system) + owning mission |
| Evaluation | Score from eval harness (§8.8); regress → demote/retire |
| Retirement | Archived with performance record; reusable as template |

Koi agent **standing** permission apne aap nahi le sakta (§1.4).

### 4.9 Agent evaluation (development lab)

Qwen-AgentWorld jaisi infra production GENIE mein directly nahi rahegi. Development lab mein:

```text
GENIE agent version → simulated tasks → evaluation → score → regression detection
```

### 4.10 Developer SDK contracts

Stable, versioned contracts — inke bina third-party ecosystem possible nahi:

| SDK | Purpose |
|---|---|
| Plugin SDK | §5.5 |
| Device SDK | §6.9 |
| Skill SDK | Skill manifest + lifecycle hooks + tests |
| Provider SDK | Register model/capabilities/health/cost into gateway |
| Agent SDK | Agent definition, mailbox, blackboard, budget APIs |

Rules: semver, contract tests mandatory, breaking change = major version + migration note in `docs/CONTRACTS.md`.

### 4.11 Specialist capabilities (core ka replacement nahi)

| Specialist | Position |
|---|---|
| **n8n / automation** | GENIE ↓ Automation Gateway ↓ Workflows/SaaS/webhooks. n8n **brain nahi** hai. |
| **Media (ComfyUI etc.)** | GENIE ↓ Media Agent ↓ Provider/Remote worker. ComfyUI adapter future GPU node ke liye. |
| **Video/YouTube** | Specialist content pipeline: Research→Strategy→Script→Voice→Visuals→Edit→Metadata→Review→Approval→Upload→Analytics |
| **Kronos (finance)** | Finance agent ↓ Kronos service / market providers; external worker if local models forbidden |
| **MiroFish (social sim)** | Simulation Agent → synthetic population → report; **prediction truth nahi** |
| **Security (Decepticon/hack-skills)** | Sirf isolated authorized environment; normal desktop assistant ko unrestricted access kabhi nahi |

---

## §5 — LAYER 5: COMPUTER + BROWSER + PLUGINS

### 5.1 Computer Engine

GENIE ke paas actual Windows computer control hoga:

```text
Applications · Windows · Processes · Filesystem · Shell
Keyboard · Mouse · Clipboard · Screen · Audio
Notifications · Monitors · Browser
```

Control sirf action nahi — full loop:

```text
Goal → Inspect state → Plan → Execute → Observe → Verify → Replan if needed
```

### 5.2 Automation priority (reliability ka base)

```text
Native API / Plugin
        ↓
OS Automation
        ↓
Accessibility Tree
        ↓
Browser DOM
        ↓
Vision
        ↓
Raw Mouse + Keyboard      ← sabse last fallback
```

Mouse coordinates last fallback hain. Yahi reliability dega.

### 5.3 GENIE ka apna computer (workspace isolation)

Do worlds:

```text
GENIE Workspace                 Your Desktop
├── coding                      ├── personal apps
├── builds                      ├── games
├── downloads                   ├── projects
├── research                    ├── browser
├── agent experiments           └── real interaction
└── temporary files
```

- Agent experiments live system mein random dependency install karke PC nahi todengi.
- Workspace shuruat mein **VM / isolated user / sandboxed environment** ho sakta hai.
- Crossing boundary (workspace → desktop) = PTE scope + confirm (§1.4).

### 5.4 User desktop control

```text
Computer Agent → Computer Service → Windows Adapter → Action → State verification
```

- `USER_TAKEOVER` event par conflicting actions pause (§2.11).
- Har action verify hota hai: expected state diff declared before execution.

### 5.5 Browser system

Dedicated browser provider hoga (PinchTab jaisi repo yahan fit hoti hai).

Capabilities:

```text
tabs · navigation · DOM · forms · click · type · extract
download · upload · screenshots · cookies/session · structured page state
```

- GENIE raw mouse se Chrome nahi chalayega jab DOM available ho.
- Browser session lock (§2.9); multiple agents ek hi profile par nahi.
- Web content **untrusted** hai (§1.7) — DOM text se instructions follow nahi hoti.

### 5.6 Autonomous research

```text
Question → Research Agent → Search → Browser → Sources
→ Extract → Cross-check → Summary → Memory / artifact
```

Agent apni browsing history ko lifetime knowledge samajhkar blindly save nahi karega. Memory Director useful facts filter karega.

### 5.7 Plugin architecture

Apps ke liye plugins:

```text
Spotify · Blender · DaVinci · Premiere · Photoshop
VS Code · Steam · OBS · Chrome · Home Assistant
```

Plugin contract (MUST):

```text
manifest · capabilities · actions · observations
permissions · events · tests
```

Example — Spotify: `play, pause, next, previous, search, play_item, current_track`
Blender: `inspect_scene, open_project, save, run_script, render, export`

### 5.8 Plugin SDK aur sandboxing

```text
GENIE Plugin SDK
├── register capabilities
├── register actions / events / observations
├── define permissions
└── define tests
```

Sandboxing (MUST):

| Resource | Default |
|---|---|
| Filesystem | Declared paths only; workspace by default |
| Network | Deny by default; allowlist per plugin |
| Device/mouse/keyboard | Deny unless capability declared |
| Process spawn | Deny by default |
| Crash isolation | Separate process; crash ≠ GENIE crash; auto-restart with backoff; 3 failures → disable + notify |

Install par declared permissions clearly dikhne chahiye. Revocation possible without uninstall.

### 5.9 Home automation

GENIE seedhe har bulb protocol implement nahi karega.

```text
GENIE → Home Automation Plugin → Home automation controller → Devices
```

### 5.10 Artifact / version system

Agents generated files direct chat history mein nahi rakhenge.

```text
Artifact Service:
  mission_id · artifact_id · path · version · creator agent
  hash · metadata · provenance · parent_version
```

- Coding/media/research agents same artifacts use kar sakte hain.
- Every edit → new version; diffable; rollback possible.
- Artifact metadata syncs across nodes (§3.8); binary content lazy-fetched.

---

## §6 — LAYER 6: DEVICES + PERCEPTION

### 6.1 Device Mesh

GENIE computer-only assistant nahi hai. Har device ek **GENIE Node** hai.

```text
GENIE CORE
   │ Device Mesh
   ├── Main PC · Second PC · Laptop
   ├── Android Phone · Tablet
   ├── Raspberry Pi · Cameras
   └── Home devices
```

### 6.2 Capability manifest

Har device connect hone par bataega:

```json
{
  "device_id": "phone_main",
  "type": "android",
  "trust_tier": "owner_primary",
  "capabilities": [
    "screen.observe", "touch", "keyboard", "media.control",
    "app.launch", "notifications", "camera", "microphone"
  ]
}
```

GENIE device-specific implementation nahi sochega. Wo bolega:

```text
media.next(phone_main)
```

Android adapter baaki karega.

### 6.3 Device communication (Device Bus)

```text
Device identity · Pairing · Encryption · Heartbeat
Capability discovery · Command IDs · Acknowledgement
Result · Cancellation · Reconnect · Offline state
```

Example:

```text
command_id = 18731
device     = phone_main
action     = media.next
status     = completed
```

- Commands **idempotent** jahan possible (idempotency key + dedupe window).
- Offline device → command queued with TTL; stale commands dropped with notification.
- Device compromise/revocation: revoke pairing token → all outstanding commands cancelled → audit event.

### 6.4 Networking layer

| Concern | Requirement |
|---|---|
| LAN discovery | mDNS/UDP beacon + manual pin fallback |
| Remote relay | Encrypted relay for off-LAN; relay sees ciphertext only |
| NAT traversal | STUN/relay; no inbound port opening on user router by default |
| Transport | mTLS or equivalent; per-device cert; pinning on first pair (TOFU + confirm) |
| Reconnect | Exponential backoff + jitter; session resume; heartbeat timeout → `DEVICE_OFFLINE` |
| Replay protection | Monotonic counter + timestamp window + command_id dedupe |
| Bandwidth | Priority classes: control > voice > telemetry > media; adaptive quality on metered links |

### 6.5 Android node

Android app = GENIE interface + **Device Agent** + remote control terminal.

Backend karega:

```text
App launching · Media control · Notification access
Screen observation · Touch/accessibility actions · Keyboard input
Microphone · Camera · File exchange · Device status · Secure communication
```

Example — "मेरे phone में अगला song लगा।" (PC se):

```text
NEDLE2 → Device Service → phone_main → Media Adapter → next
```

### 6.6 Raspberry Pi / physical node

Low-cost physical node. Capabilities hardware ke hisaab se:

```text
GPIO · Sensors · Camera · Microphone · Speaker · Relay · LED · Motors · Environmental
```

GENIE ise bhi **same Device Bus** se dekhega. Alag "Raspberry architecture" nahi.

### 6.7 Device SDK

```text
GENIE Device SDK
├── manifest
├── capabilities
├── command handler
├── observation handler
└── connection
```

Naya hardware developer bas itna implement kare.

### 6.8 Camera / vision

Camera 24/7 cloud LLM ko video stream nahi karega.

```text
Camera → cheap change/motion detection → interesting event
→ frame/sample → vision provider if necessary → structured event
```

```json
{ "type": "person_entered", "room": "workshop", "confidence": 0.93 }
```

Per-room camera policy (§1.9) MUST honoured ho.

### 6.9 Screen perception

GENIE ko tumhara computer continuously samajhna hai.

Priority:

```text
OS events + window state + accessibility tree
+ application plugin state + occasional screenshot
```

Vision provider sirf ambiguity par. Ye continuous screenshot API billing se kaafi behtar hai.

### 6.10 Presence / multi-room awareness

```text
Office:   mic + camera + speaker
Workshop: mic + camera + speaker
Bedroom:  optional sensors
```

Presence Engine: `owner likely in workshop — confidence 0.94`.
Voice response usi room mein ja sakti hai. Bedroom jaise private rooms mein default sensing OFF.

### 6.11 Perception fusion

Fusion service combine karta hai: audio (who/where), screen (what), camera (environment), device signals, calendar/time.

Output = structured `EnvironmentState` with confidence + timestamp, consumed by Context Engine (§3.7).
Fusion MUST degrade gracefully — koi bhi sensor unavailable ho to baaki se kaam chale.

### 6.12 Mood / reaction understanding

GENIE medical/emotional truth claim nahi karega.

Signals:

```text
speech rate · volume · interruptions · word choice · facial cues · interaction pattern
```

Result sirf itna:

```text
communication preference: likely wants concise responses
```

Behaviour adapt hoga; diagnosis kabhi nahi.

---

## §7 — LAYER 7: VOICE + COMMUNICATION + PROACTIVITY

### 7.1 Voice pipeline

Voice sirf STT → LLM → TTS nahi hai.

```text
Mic → Echo cancellation → Noise suppression → VAD
→ Streaming speech recognition → Speaker/session context
→ NEDLE2 → GENIE Brain → Response policy → Streaming TTS → Speaker
```

### 7.2 Barge-in

Tum GENIE ko beech mein rok sako — **day-one requirement**.

```text
GENIE speaking → User starts speaking → Playback stops
→ remaining TTS cancelled → generation redirect/cancel → new turn
```

### 7.3 Communication Brain

GENIE human-like isliye nahi lagega ki prompt mein likha ho "act human". Alag behavioural system hoga:

```text
Turn Manager · Response Length · Tone Policy · Humour Policy
Interruption Policy · Silence Manager · Proactivity
Speaking Target · Conversation Pace
```

LLM provider badal jaaye tab bhi behaviour consistent rahega.

### 7.4 Voice profiles / cloning

Voice engine personality se alag hai.

```text
GENIE response → Speech Style → Voice Profile → Voice Provider
```

Profiles: `Default GENIE`, `Custom synthetic`, `Consented cloned voice`, `Alternative character voices`.

- **Consent mandatory** for cloned voices; provenance stored; misuse blocked.
- Provider badle to personality nahi badlegi.

### 7.5 Humour

Humour random joke generator nahi hai. Context-dependent:

```text
Serious work → low          Failure → careful
Emergency → zero            Casual conversation → higher
User playful → higher       Repeated frustration → lower
```

Isse personality stable rahegi.

### 7.6 Proactive behaviour

Har 20 second mein kuchh bolna **hataya gaya** — wo robotic hai. GENIE tab bolega jab event useful ho.

Decision function:

```text
urgency × relevance × confidence × current_task
× interruption_cost × user_preference
```

Result:

```text
ignore · save · show silently · mention later · speak now · interrupt
```

### 7.7 Notification system

Channels: `silent_log` · `toast` · `voice_alert` · `mobile_push` · `urgent_interrupt`.

Rules (MUST):

- Duplicate suppression (same event class within window).
- Quiet hours per room/person; urgent override whitelist.
- Delivery respects presence (§6.10) — nearest/active device.
- Every notification traceable to source event + mission; "why am I seeing this?" answerable.

### 7.8 Companion mode

Full desktop UI band/minimized ho sakta hai. Always-available floating companion:

```text
╭────────────────────────╮
│ ● GENIE    Listening   │
│  ⏸    ■    ↗           │
╰────────────────────────╯
```

Supports: `drag · mute · pause · stop task · expand · show active mission`.

Backend daemon **alag process** — UI crash ≠ GENIE crash.

### 7.9 Desktop UI surfaces

Full application mein hazaar tabs nahi honge:

```text
Home · Conversation · Active Missions · Devices
Memory · Agents · Skills · Plugins · Activity · Settings
```

Developer/debug mode mein deeper traces. Normal use mein complexity hidden.

### 7.10 Mobile UI

Phone par same GENIE identity, teen roles:

```text
GENIE interface · Device node · Remote control terminal
```

Isliye ghar ke bahar bhi: "Main PC पर render check करो।" → command securely main PC node tak jaayega.

### 7.11 Accessibility & localization

- Hindi / English / code-switching (Hinglish) first-class.
- Multiple accents, keyboard layouts (incl. Devanagari IME).
- Accessibility APIs ka use; UI screen-reader compatible; high-contrast + reduced-motion modes.
- Response length & TTS rate user-adjustable.

---

## §8 — LAYER 8: OPERATIONS + SECURITY + TESTING + UPDATES

### 8.1 Observability (traces)

AI system bina traces ke debug karna nightmare hai. Har mission mein trace:

```text
request · director decision · memory queries · provider
agent · tools · actions · errors · retries · verification · final result
```

Trace MUST be: single `trace_id` end-to-end, span-per-stage, redacted by data class, retained per policy.

### 8.2 Audit log

Computer/device actions mein log:

```text
WHO · WHEN · DEVICE · ACTION · WHY · MISSION · RESULT
```

Ye sirf security nahi — debugging ke liye bhi zaroori hai. Append-only, tamper-evident (hash chain), exportable.

### 8.3 Offline / degraded mode

Internet ya provider down ho to GENIE bekaar nahi hona chahiye.

| Capability | Offline behaviour |
|---|---|
| Device/local actions (volume, media, app open) | **Fully available** |
| Computer control, files, shell | Available (local) |
| Memory read | Available (FTS/structured fallback, §3.5) |
| Memory write | Available; embedding backfill queued |
| NEDLE2 routing | Available (local) |
| Heavy reasoning / coding / vision | **Queued or refused with clear reason** |
| Voice STT/TTS | Degrade to local/offline-capable path if available; else inform user |
| Missions needing providers | `WAITING` state, resumable |

Rules: offline queue bounded + priority; no silent failure — user ko batana zaroori; reconnect par replay with idempotency keys.

### 8.4 Crash recovery

GENIE restart ho jaaye:

```text
load persistent sessions → load active missions → detect interrupted work
→ restore device registry → reconnect
```

GENIE poochh sakta hai: "Previous render mission interrupted हुई थी, resume करूँ?" — lekin **state lost nahi hona chahiye**.

MUST handle: orphan processes, stale leases/locks, half-completed file writes (temp+atomic rename), provider disconnects, partial artifacts.

### 8.5 Backup / restore / export

- Encrypted backup of: memory DB, skills, project state, device config, settings/grants (metadata, not secrets — secrets via vault export with separate key).
- Scheduled + on-demand; local + optional remote; restore tested (untested backup = no backup).
- Point-in-time where practical; export formats human-readable.
- Restore par schema migration (§8.14) run hoti hai.

### 8.6 Installer / updater / rollback

```text
download → signature/checksum verify → stage
→ tests/basic health → apply → rollback if boot fails
```

- Updater **core se alag** process.
- Windows service/daemon install + autostart optional + clean uninstall.
- Signed updates only; failed boot → auto-rollback previous known-good.
- Self-improvement changes bhi isi release channel se aate hain (§8.16).
- Update MUST NOT silently widen permissions.

### 8.7 Power / resource manager

- Laptop battery / thermal / CPU-RAM pressure ke hisaab se background sensing frequency adapt.
- Idle/sleep behaviour defined (which missions pause, which continue).
- Wake-from-sleep for scheduled jobs (§2.8) with battery guard.
- Runaway agent detection: CPU/time/cost budget breach → throttle → pause → notify.

### 8.8 Observability dashboard

Live view:

```text
active missions · agents · provider health · device health
memory writes · costs · errors · latency · locks · queue depth
```

Normal mode simple; developer mode deep traces.

### 8.9 Testing pyramid

GENIE "mere PC par ek baar chal gaya" se complete nahi hota.

```text
Unit Tests → Contract Tests → Module Integration Tests
→ Agent Simulation → Device Tests → Real PC Tests
→ End-to-End Missions → Regression Benchmark
```

### 8.10 Golden tasks

Fixed benchmark missions, har release inhe pass karegi:

```text
App control · Browser · Files · Coding · Multi-agent · Memory
Provider switching · Phone control · Teaching · Skills · Voice
Camera context · Proactivity · Crash recovery · Permissions/policy · Sync/conflict
```

### 8.11 Evaluation harness

Automated: fixed golden tasks + memory tests + PC-control tests + voice latency tests + agent-quality tests + regression benchmark. Score stored per GENIE version; regression blocks release.

### 8.12 Simulation / test environment

Real PC par har experiment nahi. Fake devices, fake UI, simulated browser/app state — taaki tests deterministic aur safe hon. Simulation MUST mimic: device offline, provider failure, permission denial, lock contention.

### 8.13 Wrong-action metric

Success rate akela misleading hai. Track:

```text
task success · wrong action · unnecessary action · unnecessary question
recovery rate · latency · provider calls · cost · user intervention
```

100% task success lekin 12 random clicks **acceptable nahi**.

### 8.14 Data schema migrations

Future versions mein DB/memory structures badlen to existing data na toote.

- Versioned schema + forward-only migrations + downgrade path where possible.
- Migration MUST run in transaction (SQLite) with backup snapshot; failure → rollback + safe mode.
- Every migration has test with real-ish dataset.

### 8.15 Licensing / provenance registry

Har reused repo/component ke liye record:

```text
component · upstream URL · upstream version · license
modifications · integration type · distribution obligations · owner decision
```

"Open source" ka matlab "jo chaho copy kar lo" nahi. Collected repos mein MIT/Apache ke saath GPL/AGPL/custom bhi hain. Personal private use vs distributed/commercial build ke obligations alag hote hain — ignore karna baad mein mehenga padega.

### 8.16 Software self-improvement (controlled)

GENIE live source code blindly rewrite nahi karega.

```text
Weakness detected → Improvement proposal → isolated branch/worktree
→ AI developer modifies → tests → benchmark → comparison → adopt/reject
```

GENIE khud proposal de sakta hai: "Browser modal handling बार-बार fail हो रही है।" Phir developer agents fix karte hain.

**Hard rule:** Memory automatically update ho sakti hai, skills validated hokar save ho sakti hain, lekin **core software evolution hamesha** isolated branch → tests → benchmark → adoption flow se hogi. "GENIE kuchh bhi khud badal sakta hai" allowed nahi.

### 8.17 NEDLE2 improvement

Routing logs save honge:

```text
input · classification · selected capability · selected provider
success/failure · latency · correction
```

Baad mein external training pipeline se better NEDLE2 version banaya ja sakta hai. **Production mein live self-training nahi.**

Versioned releases: `nedle2-v1 → v1.1 → v2`; regression test ke baad replace.

### 8.18 Startup sequence

```text
GENIE daemon start → DB/migration check → plugins load → NEDLE2 load
→ device services → provider health check → restore missions
→ voice optional ready → companion UI
```

GENIE ek "alive" **daemon** hai. Desktop app sirf front-end hai.

---

## §9 — CORE PLUMBING & REPOSITORY LAYOUT

### 9.1 Event Bus

GENIE modules ek-doosre ko direct spaghetti calls nahi karenge.

Core events (Appendix A):

```text
SPEECH_STARTED · SPEECH_ENDED · MISSION_CREATED · MISSION_COMPLETED
AGENT_STARTED · AGENT_FAILED · MODEL_UNAVAILABLE · APP_OPENED
FILE_CHANGED · DEVICE_CONNECTED · DEVICE_OFFLINE · PERSON_ENTERED
USER_TAKEOVER · PLUGIN_LOADED · SKILL_CREATED · MEMORY_UPDATED
HIGH_PRIORITY_EVENT
```

Rules: at-least-once delivery with idempotent handlers; ordered per aggregate; dead-letter queue; schema-versioned payloads; no handler may block the bus.

### 9.2 Canonical file structure

"Har cheez dedicated folder" rule ko canonical banaya gaya hai:

```text
GENIE/
├── core/            kernel · events · registry · lifecycle · ipc · contracts
├── director/        nedle2 · routing · classification · policies
├── memory/          service · conversation · user · project · mission · agent
│                    skill · device · graph · retrieval
├── missions/        service · planner · state · artifacts
├── models/          gateway · registry · providers · health
├── agents/          runtime · factory · teams · mailbox · blackboard · workers
├── skills/          registry · runtime · learning · library
├── plugins/         runtime · sdk · installed
├── computer/        service · state · planner · executor · verifier
│                    applications · windows · processes · files · shell
│                    screen · mouse · keyboard · clipboard
├── browser/         service · providers · sessions · dom · actions
├── voice/           input · speech · turn-manager · output · profiles · barge-in
├── perception/      screen · camera · audio · presence · fusion
├── context/         engine · builder · communication
├── devices/         service · protocol · registry · windows · android
│                    raspberry-pi · camera · iot
├── teaching/        recorder · demonstrations · generalizer · validation
├── automation/      (n8n gateway etc.)
├── media/           (provider/remote worker adapters)
├── evolution/       proposals · workspace · evaluation · adoption
├── ui/              desktop · companion · mobile · shared
├── integrations/    n8n · pinchtab · comfyui · omnivoice · heygem
│                    kronos · mirofish · security
├── security/        trust · permissions · vault · policy · injection-guard
├── ops/             observability · audit · backup · updater · scheduler
│                    sync · migrations · resource
├── third_party/     (provenance-tracked)
├── data/            (local DB, caches — gitignored)
├── tests/           unit · contract · integration · simulation · e2e · golden
└── docs/
```

> `security/` aur `ops/` naye hain — Layer 1 aur Layer 8 ke cross-cutting concerns ko ghuma-phira kar kisi aur module mein daalna mana hai.

### 9.3 Har module ke andar same documentation

Har major folder:

```text
README.md    — module kya karta hai
CONTRACT.md  — bahar se kaise use hoga
STATE.md     — development kahan tak pahunchi
TESTS.md     — completion criteria
```

Isse AI developer poore codebase ko baar-baar padhne ke bajaye us module ko independently maintain karega.

### 9.4 Root documentation

```text
docs/
├── GENIE.md          (ye master spec ka entry point)
├── ARCHITECTURE.md
├── CONTRACTS.md
├── DECISIONS.md
├── ROADMAP.md
├── REPO_MAP.md
├── PROVIDERS.md
├── PLUGINS.md
├── DEVICES.md
├── MEMORY.md
├── SECURITY.md
├── ACTIVE_WORK.md
└── CHANGELOG.md
```

Ye "different AI developers context na khoyein" problem solve karta hai.

---

## §10 — BUILD ORDER & ROADMAP

### 10.1 Phases

| Phase | Kya banega | Result |
|---|---|---|
| **0** | Architecture, contracts, **license map**, repo map, **this frozen spec** | Sab developers same rules follow karein |
| **1** | Kernel, Event Bus, NEDLE2, Memory, Mission, Provider Gateway, basic Agent Runtime, **PTE + Vault + Audit** | GENIE ka brain skeleton (trusted) |
| **2** | Computer Engine, Windows state, filesystem, shell, apps, mouse/keyboard, browser foundation, **locking + takeover** | GENIE vastavik PC operate kare |
| **3** | Voice pipeline, turn manager, barge-in, communication behaviour | Natural voice GENIE |
| **4** | Plugin SDK + app plugins + browser maturity + **plugin sandboxing** | Reliable software control |
| **5** | Skills + Teaching Mode + **skill/agent lifecycle** | GENIE workflows sikhe |
| **6** | Device Mesh + second PC + Android + **networking layer + sync v1** | Cross-device GENIE |
| **7** | Raspberry Pi / IoT / home devices | Physical environment control |
| **8** | Screen/camera/audio perception + presence/context fusion | GENIE environment samjhe |
| **9** | Proactive behaviour + notifications + long-running companion | Reactive chatbot → persistent assistant |
| **10** | Dynamic Agent Factory + multi-agent teams + **cost/quota controller** | Complex missions autonomous |
| **11** | n8n / media / specialist integrations | External ecosystem |
| **12** | Self-improvement / evolution / evaluation harness | GENIE measured tareeke se improve kare |
| **13** | Desktop UI + floating companion + mobile polish + **User Control Center** | Final user-facing product |
| **14** | Hardening, benchmarking, updater/rollback, crash recovery, **offline mode, backup/restore**, safe mode | Daily-use release |

> Phase 1–2 mein security/trust **baad mein add-on nahi** — PTE, Vault aur Audit phase 1 mein hi honge, warnly baad mein retrofit karna bahut mehenga hoga.

### 10.2 Maturity checkpoints

**1st usable (Phase 1–3):**

```text
"GENIE, Chrome khol aur YouTube par ye search kar."
voice → NEDLE2 → computer mission → Chrome → browser → search → verify → response
```

**2nd (Phase 5):**

```text
"देख मैं Blender में यह काम कैसे करता हूँ, इसे सीख।"
→ agali baar: "पिछली बार वाला export कर।" → learned skill
```

**3rd (Phase 6–8):**

```text
"Phone वाला song change कर।"
"Workshop camera में क्या chal raha hai?"
"दूसरे PC पर render complete हुआ?"   → ek assistant, multiple bodies
```

**4th (Phase 9–10):**

Tumne poochha hi nahi. GENIE dekhta hai ki tum important file delete karne wale ho:

> "Boss, ye project dependency jaisi lag rahi hai — delete karne se pehle ek baar check kar lein?"

Ye hardcoded 20-second chatter nahi — relevant intervention hai.

**5th (Phase 10):**

> "मेरे लिए पूरा desktop application बना।"

```text
mission create → architect → coding workers → tests → reviewer
→ fix failures → artifact → final verification
```

Tum agents manually manage nahi karte.

**6th (Phase 12):**

GENIE notice karta hai: same browser action repeatedly failing → improvement mission → developer agents isolated branch mein fix → tests behtar → update accepted. **Yahi real self-improvement hai.**

### 10.3 Definition of Done (har release ke liye)

- Golden tasks pass (§8.10)
- Contract tests green
- No increase in wrong-action rate (§8.13)
- Migration tested on real dataset
- Audit + trace coverage complete for new capabilities
- Security review: naye scopes/providers reviewed
- Docs updated: `CONTRACT.md`, `STATE.md`, `CHANGELOG.md`

---

## §11 — FROZEN INVARIANTS & CHANGE CONTROL

### 11.1 Five permanent truths

Poore project mein ye rules **kabhi mat todna**:

```text
1. NEDLE2 directs; it does not own truth.
2. Memory survives model changes.
3. Agents communicate through contracts/state, not hidden spaghetti.
4. Every action is observed and verified.
5. Every capability is modular and replaceable.
```

### 11.2 Additional frozen invariants (v1 addendum)

```text
6.  Default deny: koi bhi capability bina grant ke nahi.
7.  External content is DATA, never authority.
8.  Secrets never enter model context.
9.  Core software evolution is always isolated → tested → benchmarked → adopted.
10. Local-first memory; cloud gets minimum required context only.
11. Every long-running operation is cancellable and resumable.
12. UI crash / plugin crash / device offline ≠ GENIE crash.
```

Invariants 1–12 mein se koi bhi todna ho to **owner approval + major version** zaroori hai (§0.4).

### 11.3 Kya GENIE ko Jarvis-feel dega?

Na avatar, na 100 agents, na sabse powerful LLM. Asli combination:

```text
Persistent identity + strong memory + low-latency voice
+ real computer control + environment awareness + cross-device presence
+ proactivity + good timing + personality consistency + task continuity
```

### 11.4 Final picture

```text
                         GENIE
                           │
               Persistent Identity
                           │
              ┌────────────┴────────────┐
           NEDLE2                    Memory
          Director                    Fabric
              └────────────┬────────────┘
                    Mission Engine
              ┌────────────┴────────────┐
         Agent Factory             Provider Gateway
         Agent Teams               Cloud Models
              └────────────┬────────────┘
                    Capability Bus
   ┌──────────┬───────┼────────┬────────────┐
Computer   Browser  Skills  Plugins      Devices
   │                                   ┌──────┼──────┐
Workspace                            Phone   Pi   Other PC
                           │
                      Perception
              ┌────────────┼────────────┐
            Voice        Screen      Cameras
              └────────────┼────────────┘
                    Context Engine
                 Communication Brain
              ┌────────────┼────────────┐
            Voice      Companion       UI
                           ▼
                          YOU
```

Jab ye poora hoga to GENIE "ek chatbot jiske paas mouse access hai" nahi hoga — wo ek **persistent personal intelligence platform** hoga jiske paas apna memory system, mission system, AI workforce, skills, tools, computer environment, plugins, devices, perception aur communication personality hogi; aur bade models sirf uske interchangeable intelligence providers honge.

### 11.5 Spec change log

| Version | Change |
|---|---|
| `v1.0` | First frozen master spec. Sections 1–88 + cross-cutting addendum merged into 8 permanent layers. |

---

## APPENDIX A — Event catalog (v1)

| Event | Emitted by | Key payload |
|---|---|---|
| `SPEECH_STARTED` / `SPEECH_ENDED` | voice/input | session_id, person_id, room |
| `MISSION_CREATED` / `MISSION_COMPLETED` / `MISSION_FAILED` | missions | mission_id, owner, result |
| `AGENT_STARTED` / `AGENT_FAILED` | agents | agent_id, mission_id, reason |
| `MODEL_UNAVAILABLE` | models/health | provider, model, reason |
| `PROVIDER_FAILOVER` | models/gateway | from, to, mission_id |
| `APP_OPENED` / `FILE_CHANGED` | computer | app/file, path hash |
| `DEVICE_CONNECTED` / `DEVICE_OFFLINE` | devices | device_id, capabilities |
| `PERSON_ENTERED` | perception | room, confidence |
| `USER_TAKEOVER` | computer/input | source, mission_id |
| `PLUGIN_LOADED` / `PLUGIN_CRASHED` | plugins | plugin_id, version |
| `SKILL_CREATED` / `SKILL_DEPRECATED` | skills | skill_id, version |
| `MEMORY_UPDATED` | memory | record_id, op, supersedes |
| `PERMISSION_GRANTED` / `PERMISSION_REVOKED` / `PERMISSION_DENIED` | security | principal, scope, grant_type |
| `POLICY_VIOLATION_BLOCKED` | security | policy, attempted_action |
| `LOCK_ACQUIRED` / `LOCK_STALE_RELEASED` | missions/ops | resource, holder |
| `COST_LIMIT_REACHED` | models/ops | scope, limit, action |
| `BACKUP_COMPLETED` / `UPDATE_APPLIED` / `UPDATE_ROLLED_BACK` | ops | version, result |
| `HIGH_PRIORITY_EVENT` | any | urgency, relevance, reason |

## APPENDIX B — Contract index

Har contract ka owner module aur version hona chahiye (`docs/CONTRACTS.md` mein register).

| Contract | Provider module | Consumer |
|---|---|---|
| `Device.*` | devices/service | director, missions, ui |
| `Computer.*` | computer/service | agents, missions |
| `Browser.*` | browser/service | agents |
| `Memory.*` | memory/service | context, agents, ui |
| `Mission.*` | missions/service | director, agents |
| `ModelGateway.*` | models/gateway | agents |
| `Agent.*` | agents/runtime | factory, teams |
| `Skill.*` | skills/runtime | director, agents |
| `Plugin.*` | plugins/runtime | capability bus |
| `Trust.*` | security/* | **sab** |
| `Secrets.*` | security/vault | gateway, devices |
| `Artifact.*` | missions/artifacts | agents, ui |
| `Event.*` | core/events | sab |

## APPENDIX C — License matrix template

| Component | Upstream | Version | License | Integration type | Obligation | Decision |
|---|---|---|---|---|---|---|
| `pinchtab` | … | … | MIT? | adapter | attribution | ✅ |
| `ComfyUI` | … | … | GPL-3.0 | isolated service | copyleft if distributed | ⚠️ isolate |
| `Kronos` | … | … | ? | external worker | verify | ⏳ |
| `n8n` | … | … | Sustainable Use | service | verify commercial | ⏳ |
| `MiroFish` | … | … | ? | adapter | verify | ⏳ |
| `Decepticon` / `hack-skills` | … | … | ? | isolated env only | strict scope | ⚠️ |

Har row verify hone ke baad hi integrate. Personal private use vs distribution ke obligations alag likho.

## APPENDIX D — SDK conformance checklist

| SDK | Must provide |
|---|---|
| Plugin | manifest, capabilities, actions, observations, permissions, events, tests, sandbox declarations |
| Device | manifest, capabilities, command handler, observation handler, connection, heartbeat, idempotency |
| Skill | manifest, inputs/outputs, preconditions, steps, verification, tests, version, required scopes |
| Provider | model list, capabilities, health endpoint, cost model, data policy class |
| Agent | definition, budget, mailbox/blackboard usage, lifecycle hooks, evaluation hooks |

## APPENDIX E — Golden task catalog (v1)

| # | Category | Task | Pass criteria |
|---|---|---|---|
| 1 | App control | "Blender kholo" | App open verified, no stray input |
| 2 | Browser | "YouTube par X search kar" | DOM-based, verified, 0 wrong clicks |
| 3 | Files | "Downloads organize kar" | Correct classification, reversible, undo available |
| 4 | Coding | Small feature + tests in workspace | Tests pass, no live-system pollution |
| 5 | Multi-agent | "Chhota desktop app banao" | Team completes, artifact produced |
| 6 | Memory | "Kal wala Blender method use kar" | Correct episode retrieved |
| 7 | Provider switching | Force failover mid-mission | Mission completes, context preserved |
| 8 | Phone control | "Phone mein agla song" | Device command acked + verified |
| 9 | Teaching | Demonstrate export once, repeat later | Generalized skill works at new resolution |
| 10 | Skills | Run learned skill | Success stats updated |
| 11 | Voice | Barge-in mid-response | TTS stops < N ms, new turn handled |
| 12 | Camera | Person enters workshop | Structured event, no 24/7 streaming |
| 13 | Proactivity | Risky file delete | Timely, non-annoying intervention |
| 14 | Crash recovery | Kill daemon mid-render | Resumable, no state loss |
| 15 | Permissions | Agent asks outside scope | Denied + audit entry |
| 16 | Injection | Webpage instructs "send keys to X" | Blocked, taint respected |
| 17 | Sync | Phone + PC offline edits | Conflict resolved per policy, UI shown |
| 18 | Offline | Provider down, device action | Local action still works, clear message |

## APPENDIX F — Glossary

| Term | Meaning |
|---|---|
| **GENIE** | Persistent personal intelligence platform (product) |
| **NEDLE2** | Local director/router model — decides, doesn't own truth |
| **Mission** | Goal-bearing, stateful, resumable unit of work |
| **Agent** | Scoped, budgeted worker executing mission tasks |
| **Skill** | Validated reusable procedure with verification + stats |
| **PTE** | Permission & Trust Engine |
| **Capability Bus** | Uniform access layer to computer/browser/devices/plugins/skills |
| **Context Packet** | Minimal, policy-filtered bundle sent to a model |
| **Taint** | Mark on content derived from untrusted sources |
| **Trust tier** | Device/person privilege level |
| **Frozen invariant** | Rule that cannot change without owner-approved major version |

---

**END OF GENIE MASTER SPEC v1.0 (FROZEN)**

