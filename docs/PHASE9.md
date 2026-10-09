# GENIE — PHASE 9: PROACTIVITY & COMPANION

**Status: 🟩 COMPLETE**

> **Exit gate:** *golden #13 — risky file delete → timely, non-annoying intervention;
> duplicate suppression + quiet hours verified.*
> All verified: **50 tests**, all green.

---

## 1. The rule

The spec removes "say something every 20 seconds" as robotic. GENIE speaks when an event is
**useful**, and it has a vocabulary for the gradations in between:

```
ignore · save · show silently · mention later · speak now · interrupt
```

## 2. The decision function

```
score = urgency × relevance × confidence × current_task × interruption_cost × user_preference
```

**Multiplication is the point.** If any factor is near zero the whole score collapses — a genuinely
urgent message that is irrelevant to the owner's goal, or that would land in the middle of deep
work, must not be shouted at them. An additive score would let three mediocre signals outvote one
fatal one.

| Factor | Meaning | Direction |
|---|---|---|
| `urgency` | how much it matters that this is acted on | higher → louder |
| `relevance` | how much it concerns the current goal | higher → louder |
| `confidence` | how sure GENIE is | higher → louder |
| `current_task` | the owner is mid-task | **inverted** (`1 - value`) |
| `interruption_cost` | how disruptive speaking now would be | **inverted** |
| `user_preference` | the owner's feeling about this class | 0.5 = neutral = ×1.0 |

Score → outcome thresholds: `≥0.80 interrupt · ≥0.62 speak now · ≥0.44 mention later ·
≥0.26 show silently · ≥0.12 save · else ignore`.

A candidate marked `urgent_override` has a **visibility floor** of `show_silently`: a safety signal
may be reduced to a silent toast, never to nothing.

## 3. Notifications — the MUST rules

| Rule | How it is enforced |
|---|---|
| **Duplicate suppression** (same class within a window) | a repeat inside `dedupe_window_s` (default 300 s) is withheld — **unless it is materially worse** (`ESCALATION_MARGIN = 0.15`), because suppressing an escalating problem is how a warning becomes useless |
| **Quiet hours per room/person** | quiet hours **clamp** the outcome rather than replace it (never *less* intrusive than the score earned). Scoped by person and/or zone |
| **Urgent override whitelist** | only `interrupt` / `speak_now` pierce quiet hours, and only when the candidate carries an explicit `urgent_override` |
| **Delivery respects presence** | the target is the device in the owner's zone, else the local machine — and the reason is recorded |
| **Traceable to source event + mission** | every notification stores its score, every factor, the event and the mission. `explain()` answers *"why am I seeing this?"* in one line |

## 4. Golden #13 — risky file delete

**Timely.** The orchestrator **pre-scans the plan** and consults the proactive service *before any
step runs*, so a destructive step is flagged while it can still be stopped. The warning travels
with the turn result (`payload["risks"]`), so the UI can show it.

**Non-annoying.** It goes through the ordinary rules: a second identical delete inside the window
is suppressed, quiet hours clamp the channel. But `files.delete` and `process.kill` are marked
`urgent_override`, so at 2am the owner still sees a toast rather than nothing.

```
files.delete D:/thesis/final.docx
  → risky=True severity=high needs_confirmation=True
  → outcome=mention_later channel=toast
  → why: score 0.581 = urgency=0.90 × relevance=0.85 × confidence=0.95 × current_task=1.00
                       × interruption_cost=0.80 × user_preference=1.00
```

## 5. Defect found while building this phase

| ID | Defect | Fix |
|---|---|---|
| A-072 | A **neutral** `user_preference` (0.5) was used as a literal multiplier, so *every* notification was silently halved — a neutral owner would have received half the warnings they should. Neutral now maps to ×1.0; the owner can **suppress** a class but cannot inflate one above its merits | `proactive/scoring.py` |

## 6. Verification highlights

* a multiplicative score **collapses** when any factor is zero, and a busy owner scores lower
* a neutral preference does not halve the score; a disliked class is suppressible; a liked class
  cannot be inflated
* quiet hours: midnight wrap, daytime window, disabled window, equal start/end, clamping only ever
  reduces, and **scoping by zone**
* an urgent override **pierces** quiet hours; a non-urgent message is clamped to a toast
* duplicate suppression: same class within the window is withheld; a **materially worse** repeat
  escapes; different classes do not dedupe against each other; the window expires
* every notification explains itself; an unknown id is an honest failure
* golden #13: flagged **before** execution, needs confirmation, a second identical delete does not
  nag, and a read-only step is never flagged
* the turn loop surfaces `risks` for a destructive plan

## 7. Surfaces

| Surface | Routes / commands |
|---|---|
| HTTP | `GET /api/proactive`, `/api/proactive/history`, `/api/proactive/explain/<id>` · `POST /api/proactive/quiet-hours`, `/api/proactive/preference`, `/api/proactive/risky` |
| CLI | `proactive`, `proactive-quiet` |
| Storage | migration `009_proactive` (`proactive_notifications`, `proactive_quiet_hours`, `proactive_preferences`) |

## 8. Deliberately deferred (interfaces preserved)

| Deferred | Why | Hook that exists |
|---|---|---|
| The floating desktop companion UI (§7.8) | it is a UI-phase deliverable; the notification stream it renders is complete | `outbox` / `history` / the event bus |
| `mobile_push` delivery | Android is owner-deferred | `Channel.MOBILE_PUSH` exists and the target already resolves to a device id; see `docs/ANDROID_DEFERRED.md` → `ANDROID-NOTIFICATIONS` |
| Voice delivery of `speak_now` | the voice pipeline exists (Phase 3) | `Channel.VOICE_ALERT` maps to it; a `deliver` callable can be injected |
| Calendar-driven relevance | no calendar provider connected | `Candidate.relevance` is supplied by the caller |
| Learned per-class preferences | needs usage history | `set_preference` / `proactive_preferences` |

## 9. Verification

```
tests/e2e/test_phase9_proactivity.py   44   scoring, quiet hours, duplicate suppression,
                                            traceability, golden #13, other candidate sources
tests/contract/test_ipc_api.py         +6   /api/proactive*
genie.py proactive                          delivered / withheld / quiet hours / recent decisions
genie.py proactive-quiet --start 22 --end 7 Set quiet hours
```

**Phase 9 exit gate: PASS** — a risky file delete produces a timely warning before the action runs,
the owner is not told the same thing twice, quiet hours are respected, and an urgent safety signal
still gets through at night.
