# NEDLE2 — local director (Cactus Needle 2)

**Model/engine:** `Cactus-Compute/needle2` · **Python package:** `cactus-needle` (module `needle`) · **native engine:** `libneedle.dll`
**Status:** 🟩 REAL RUNTIME LOADED AND VALIDATED (routing smoke 12/12)

> NEDLE2 is GENIE's local **director**, not its reasoning brain. It decides; services own truth.
> A tool call from NEDLE2 is a **routing decision** — never evidence that an action happened.

---

## 1. Provisioning (no manual model paths)

```bash
python genie.py needle-setup        # provision + verify + smoke test
python genie.py needle-smoke        # re-run routing smoke test
python genie.py needle-smoke --large 60   # also probe with 60 filler tools
```

The installer/bootstrapper calls the same code path. Order (owner-specified):

```text
1. official Python package   cactus-needle        (pip)
2. official native runtime   libneedle.dll        (HF Cactus-Compute/needle2)
   a. official fetch_library()  ->  b. direct HTTPS + sha256 verify (fallback)
3. localhost server          only if direct embedding proves unreliable
```

**GENIE-managed location** (never a user-chosen path):

```text
Windows : %LOCALAPPDATA%\GENIE\runtime\needle2\
macOS   : ~/Library/Application Support/GENIE/runtime/needle2/
Linux   : ~/.local/share/GENIE/runtime/needle2/
```

`runtime.json` records `version`, `sha256`, `source`, timestamps. `verify()` re-hashes the
engine and fails on corruption or replacement. Boot provisions in a **background thread**
(boot never waits on a download) and then runs the routing smoke test.

Telemetry from the official package is disabled (`NEEDLE_TELEMETRY=0`).

## 2. Integration contract

Mirrors the official Cactus harness exactly (`director/nedle2.py`):

| Rule | Implementation |
|---|---|
| One engine instance | lazy singleton, guarded by a lock (native engine has global state) |
| Independent requests | `reset()` before every request |
| Read | `function_calls`, `validation`, `confidence` |
| Ignore bad calls | drop when `validation.ungrounded` or `validation.negation` |
| Confidence gate | act only at/above `director.needle.confidence_threshold` (default **0.4**) |
| Refusal | below threshold / no call → **escalate classification to a remote model** |
| Never owns truth | no execution, no completion claims, no memory writes (proposes only) |

Everything Needle-specific lives in **`director/nedle2.py` + `director/tools.py`**. No other
module imports Needle (owner requirement). Replacement is possible behind `DirectorProvider`.

## 3. Tool catalogue (single source, deliberately small)

`director/tools.py` declares GENIE's **routable** surface as official `@needle.tool` functions:

`open_application · close_application · set_system_volume · system_volume_up ·
system_volume_down · mute_audio · media_next · media_previous · media_pause · media_resume ·
memory_lookup · start_mission · read_processes · run_shell`  → **14 tools**

**Catalogue size is a quality lever (D-043).** Measured on the real engine: 13 tools → smoke 12/12;
20 tools → 9–11/12. The other Phase 2 capabilities (window focus/minimize, clipboard copy, file
search/move, website open) remain fully available as capabilities and are reached through a mission /
remote model instead of the local router.

### Deterministic fast path (D-044)

A web address is not a judgement call. When the request contains one, the director resolves it
directly (`fast-path:web-address` → `browser.navigate`) instead of asking the model to separate
"open youtube.com/x" from "open notepad".

`TOOL_TO_TASK` is the single mapping table from Needle tool name → GENIE capability.
`memory_lookup` → memory query · `start_mission` → mission + remote model.

## 4. Hinglish layer (D-038)

Needle 2 is a 14MB English-first router. It routes English reliably but **correctly refuses**
Hinglish command words (they are outside its training distribution). GENIE therefore
normalises command words in front of the director (`director/normalize.py`):

```text
"Chrome kholo"          -> "open chrome"        -> application.open
"awaz 30"               -> "volume 30"          -> system.volume.set
"phone ka next song"    -> "phone next song"    -> media.next (device inferred)
```

The lexicon only maps command words, particles and possessives; **meaning-carrying values
(song names, file names, app names) keep their original casing**. The original user text is
always preserved for trace/audit/memory. NEDLE2 still makes the routing decision.

## 5. Device inference

Device arguments are requested **as the user said them** ("phone", "pc") and canonicalised by
GENIE (`tool_catalog.canonical_device`). If the model omits the device, GENIE infers it from
the user's own words (`infer_device`) — this fixed an `ungrounded` rejection in the smoke test.

## 6. Validated behaviour (real engine, engine 2.0.4, package 2.0.15)

| Case | Input | Result |
|---|---|---|
| volume (en) | `volume 30` | `system.volume.set` · conf 0.998 |
| volume (hinglish) | `awaz 30` | `system.volume.set` · conf 1.000 |
| open app (en) | `open chrome` | `application.open` · conf 0.764 |
| open app (hinglish) | `Chrome kholo` | `application.open` · conf 1.000 |
| device media (en) | `next song on my phone` | `media.next` · conf 0.670 |
| device media (hinglish) | `phone ka next song` | `media.next` · device `phone_main` · conf 0.712 |
| project (en/hinglish) | `continue my latest project` | no device action · **escalated** |
| malformed | `asdkjhasd qwerty ???` | no action · escalated |
| ambiguous | `kuch karo` | no action · escalated |
| unsupported | `delete all my files right now` | no action · escalated |
| negated | `don't open chrome` | no action (negation flagged) |
| large catalogue | 73 tools registered | still routes both probes correctly |

Latency: ~0.6–1.4 s per decision, ~62–66 MB peak RAM, engine init ~0.26 s.
Smoke suite runtime ~9–11 s for 12 cases. **Reproducible across runs.**

### Honest caveats

- `continue my latest project` is handled by **escalation**, not by a direct `memory_lookup`
  call. Escalation is the designed path for below-threshold input (owner-approved), but it is
  not the same as NEDLE2 choosing the memory tool itself.
- Prompt sensitivity is real: a 6-clause system prompt dropped the smoke rate from 12/12 to
  9/12. Keep `SYSTEM` short; put disambiguation in tool descriptions (recorded in code comment).
- `director.needle.timeout_ms` cannot interrupt the synchronous native call; it applies to the
  cli/http runtime kinds only.

## 7. Configuration

```json
"director": {
  "engine": "auto",
  "needle": {
    "enabled": true,
    "runtime": "python",
    "runtime_dir": "",
    "confidence_threshold": 0.4,
    "auto_provision": true,
    "timeout_ms": 800
  }
}
```

`engine: "heuristic"` forces the deterministic fallback; `"needle"` makes a missing runtime a
hard error instead of a silent fallback. Fallback state is always visible in
`/api/status` → `needle.director.fallback_active`.
