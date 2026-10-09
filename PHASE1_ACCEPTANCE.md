# GENIE — Phase 1 Final Owner Acceptance Gate

**Status:** Phase 0 CLOSED. Phase 1 implementation is complete with strong automated
evidence. **No owner-acceptance workflow has been marked PASS** — those require the
owner's microphone, speakers and a visible desktop, which the development
environment cannot supply. No rc27 created. Phase 2 NOT started.

Three categories are used strictly:

| Category | Meaning |
|----------|---------|
| **IMPLEMENTATION PASS** | Source code + targeted automated tests pass. |
| **OWNER ACCEPTANCE PASS** | The actual current Preview GUI + real owner workflow were exercised successfully. |
| **PENDING OWNER ACTION** | Physically requires the owner's microphone / speakers / visible desktop; cannot run here. |

A GUI or microphone workflow is **never** marked owner-accepted on the strength of
an API test, a synthetic audio fixture, compiled XAML, or a process-alive probe.

---

## 1. Implementation PASS (verified in this environment)

| Item | Evidence |
|------|----------|
| WPF build | `scripts/build_native.sh` → **0 warnings / 0 errors** |
| Build match | `scripts/verify_build_match.py` → **31/31** |
| Backend runtime sync | `scripts/sync_backend_runtime.py` → 240 files @ `6f2bb7ec217a`, verified |
| Multilingual STT model | manifest `stt_model_present=True`, `stt_model_loads=True` (faster-whisper small) |
| Preview shortcut target | `scripts/p1_preview_launch_test.py` → **20/20**; shortcut → the exact source-built EXE |
| Voice state machine | `scripts/p1_voice_session_test.py` → **22/22** (13 states, terminal, stop) |
| Full voice loop sequence | `scripts/p1_voice_loop_test.py` → **15/15**: `GREETING → LISTENING → TRANSCRIBING → THINKING → SPEAKING → LISTENING` |
| Greeting exactly once | 22/22 + 15/15 (no repeat on listening cycle / turn; greets again on new session) |
| TTS self-capture prevention | 22/22 + 15/15 (blocked in GREETING and SPEAKING; STT not called) |
| Home/Settings RMS contract | 15/15 — backend dict `{level,source,synthetic}` parsed exactly as Home does; plain-number fallback works |
| Honest Speaking animation | 15/15 — unavailable TTS RMS → 0 (not fabricated); state floor animates |
| Typed↔voice continuity (backend) | `p1_owner_acceptance_test.py` 16/16 + `phase0_lifecycle_continuity_test.py` 15/15 + `phase0_context_audit_test.py` 4/4 + `phase0_session_persistence_test.py` 7/7 (one `SessionStore`, one `ContextCompiler` packet, `owner` session) |
| Hindi voice engine | `p1_hindi_voice_test.py` 15/15 — FasterWhisperSTT `ready=True loaded=True` via Preview runtime; 3-turn pipeline; amplitude contract |
| Preview runtime voice chain | `p1_preview_voice_probe.py` **11/11** run with the **packaged** interpreter: real `VoiceService` builds real providers — sounddevice mic (available), **faster-whisper `ready=True loaded=True`**, Windows SAPI TTS (available), sounddevice output (available); both amplitude contracts present; a real turn executes and returns to IDLE |
| Provider flow (backend/API) | `phase0_wpf_provider_flow_test.py` 9/9 + `phase0_provider_edit_test.py` 16/16 |
| Truthful action failure | `p1_owner_acceptance_test.py` 6/6 — FAILED state, honest reply, no false success |
| Restart continuity | `phase0_sot_restart_test.py` 17/17 |
| Short Chat sanity (headless) | `p1_chat_sanity.py` 7/7 — real daemon, 3 turns, deterministic recall |
| Dict-key lint | `check_duplicate_dict_keys.py` OK |

## 2. OWNER ACCEPTANCE PASS
**None.** No GUI or microphone workflow was exercised in this environment.

## 3. PENDING OWNER ACTION (see `OWNER_ACCEPTANCE.md`)

| # | Workflow | Why pending |
|---|----------|-------------|
| A | Preview identity + GUI render | Needs a visible desktop. |
| B | Real owner voice — three turns | Needs a human speaker at the microphone. |
| C | Greeting audible once + no self-answer | Needs real speakers + a human listening. |
| D | Home microphone ring visibly reacts | Needs a human voice + visible UI. |
| E | Actual typed↔voice GUI continuity (BLUE ORBIT / 731) | Needs the GUI + a real provider. |
| F | Actual provider GUI flow | Needs the GUI (add/detect/save/restart/edit/rediscover/test/delete). |
| G | Slow-provider responsiveness + close-while-active | Needs the GUI; close-while-active is distinct from Stop. |
| H | Chat-to-verified-browser action | Needs the GUI Chat surface (a direct CDP test is not proof of the Chat integration). |

---

## 4. Defects found and fixed in this pass (implementation)

1. **Home amplitude parsing (P1.7).** `HomeViewModel.PollAmplitudeAsync()` expected
   `pipeline["amplitude"]` as a JSON number, but the backend emits a dict
   `{"level":…,"source":"microphone","synthetic":false}`. The Home ring therefore
   never reacted to voice. Fixed to read `"level"` (fallback to a number), matching
   `SettingsViewModel`'s proven parsing.
2. **Missing `tts_amplitude` in `VoicePipeline.status()`.** The pipeline block exposed
   `amplitude` but not `tts_amplitude`, so the **Speaking** ring could not react to the
   real TTS output level. Added `tts_amplitude` to the pipeline status block.
3. **Context resolver gap.** `_ASKED_RE` handled `maine abhi kya pucha/kaha` but not
   the acceptance phrasing `maine abhi kya <word> bola tha`, so the recall depended on
   the model. Added the `bola` alternative so recall is deterministic.
4. **SOT/Advanced STT line lied.** `SettingsViewModel._impl()` read only the `name`
   field, but the multilingual `FasterWhisperSttProvider.status()` reports `provider`,
   so a **ready** engine displayed `STT: Not configured`. Fixed `_impl` to read
   `name` **or** `provider`. Verified in the real GUI: the panel now shows
   `STT: faster-whisper / small — ready=yes, loaded=yes`.

All four changes: WPF rebuilt 0/0, runtime re-synced, `verify_build_match` 31/31,
targeted regression re-run green.

---

## 4b. Automated GUI evidence (real Preview build — NOT owner acceptance)

`scripts/p1_gui_evidence.py` (system Python + pywinauto) launched the **actual
source-built** `Genie.Desktop.exe` and drove the real UI. **13/13**:

- main window rendered (`title='GENIE'`, visible, 1100×720)
- Home screenshot captured (real PrintWindow render)
- navigated to **Settings → Advanced**; the Source-of-truth panel showed **real** values:
  - `Backend root: E:\G3\GENIE\backend-dist\backend-runtime`
  - `Data directory: C:\Users\ghostt\AppData\Local\GENIE\data (source: default)`
  - `Owner session: owner`
  - `STT: faster-whisper / small — ready=yes, loaded=yes`
  - `Active provider/model: xkiro / minimax/minimax-m3:free`
- screenshots: `artifacts/p1_gui_evidence/{home,settings_advanced}.png`

**Typed Chat end-to-end (real GUI):** `scripts/p1_chat_gui_test.py` **7/7** — launched
the real EXE, navigated to **Chat**, typed `Hello GENIE` into the real composer
(`auto_id=Composer`), waited for the turn to settle, and read a **real model reply**:
> "Hey! Welcome back! 😊 Kaise ho? Mission abhi bhi **CREATED** state mein hai (0 steps).
> Koi naya kaam karna hai ya purani cheez continue karein? Bolo, kya…"

Screenshot: `artifacts/p1_gui_evidence/chat_reply.png`. This proves the real Chat GUI
sends to and receives from the configured provider. It is still **not** owner acceptance
and does **not** close items B–H (voice, Home reaction, GUI continuity, provider GUI,
close-while-active, Chat→browser).

**Typed continuity (real GUI):** `scripts/p1_gui_continuity_test.py` **7/7** — sent
`My temporary phrase is BLUE ORBIT.` then `Maine abhi kya phrase bola tha?` in the real
Chat GUI and got the correct recall:
> `Aapne abhi pucha tha: "My temporary phrase is BLUE ORBIT."`

Screenshot: `artifacts/p1_gui_evidence/chat_continuity.png`. This is the **typed half**
of the continuity workflow; the cross-modality half (a voice turn recalling a typed turn)
still needs the owner's microphone.

**Chat→browser action (real GUI) — ⚠️ NOT VERIFIED / LIKELY DEFECT.**
`scripts/p1_gui_browser_action_test.py` sent `YouTube open karo aur Barsaat song play
karo.` through the real Chat composer **three times** (observing up to ~135 s each). Each
time the GUI produced a **reply that only narrates** a mission:

> "Hey! 😊 Mission already **RUNNING** state mein hai (step 3/3) ▶️ Ab **top result play**
> karta hoon "Barsaat song" ka 🎵👇 `{"action": "play_top_result", "app": "youtube",
> "query": "Barsaat song"}` Step complete hone do — song bajna…"

but **no browser process ever appeared** (`all_browsers=[]` — zero Chrome/Edge). So the
Chat request does **not** invoke the GENIE-owned browser, opens no result, and yields no
playback evidence.

**Root cause (confirmed):** the request is routed to capability **`application.open`**
with target **`youtube`**. `computer/executor.py::_open_app()` resolves the target through
the *installed-app* resolver, and:

```
apps.resolve('youtube')  -> None          # not an installed application
apps.resolve('chrome')   -> chrome.exe    # a real app
```

so `_open_app` returns `{"ok": False, "error": "application 'youtube' is not installed"}`
and nothing launches. YouTube is a **website** and must route to the **browser authority**
(`browser/service.py`), not `application.open`. The reply is model narration rather than
the honest failure — the false-action-claim class the gate warns about.

**Do not treat the earlier direct-CDP proof as evidence this works.**

**Required fix (designed, not applied).** A `browser.navigate` capability already exists and
`director/nedle2.py:242` has a **URL fast-path** (`extract_url` → `browser.navigate`) that
fires only for **explicit URLs** — not for a named service like "youtube". Two changes are
needed:

1. **Routing (small, additive):** recognise named web services (youtube, …) with an
   open/navigate verb and route them to `browser.navigate` — either extend the NEDLE2
   fast-path / `director/heuristics.py:92` web regex, or add an orchestrator-level override
   applied after every director (mirroring `_apply_conversation_override` /
   `_apply_mission_gate`). Without this, `application.open` is chosen and fails.
2. **Multi-step action (design):** the expected behaviour is "open YouTube **and play**
   Barsaat song" — i.e. navigate → search → select the top result → **verify playback**.
   That is a multi-step browser action, and the streaming mission path only *plans*; it
   needs the director/planner to emit the full verified step chain.

**Not applied here:** step 1 alone would only open YouTube without playback (still failing
the item), and step 2 is a browser-authority/mission design change the gate explicitly
forbids ("do not redesign the … Mission system or browser authority"; targeted checks only).
Screenshot: `artifacts/p1_gui_evidence/chat_browser_action.png`.

---

## 5. Final report (A–J)

**A. Current Preview identity and GUI render** — **AUTOMATED GUI EVIDENCE (owner
confirmation still pending).**
The real Preview window rendered and the Source-of-truth panel showed real identities
(`p1_gui_evidence.py` 13/13; see §4b). EXE built 0/0, GUI subsystem, all deps present,
shortcut targets the exact source EXE. Still pending: the owner opening it and confirming
the identity matches what they expect.

**B. Real owner voice: three turns** — **PENDING OWNER ACTION.**
Implementation evidence: engine operational — the packaged Preview runtime builds the
real `VoiceService` with sounddevice mic + **faster-whisper `ready=True loaded=True`** +
Windows SAPI TTS + sounddevice output, and executes a real turn (`p1_preview_voice_probe.py`
11/11); full state sequence verified (`p1_voice_loop_test.py` 15/15). No human speech was run.

**C. Greeting and self-capture** — **PENDING OWNER ACTION (audible confirmation).**
Implementation evidence: greeting-once and self-capture prevention verified 22/22 + 15/15
with the real pipeline. Audibility/no-self-answer must be confirmed by the owner.

**D. Home microphone reaction** — **PENDING OWNER ACTION.**
Implementation evidence: the RMS→Home data path was fixed and verified at the contract
level (15/15); the ring reaction itself needs a human voice + visible UI.

**E. Actual typed↔voice GUI continuity** — **PENDING OWNER ACTION (the voice half).**
Implementation evidence: one `SessionStore`, one `ContextCompiler`, `owner` session
(16/16 + 15/15 + 4/4 + 7/7); the **typed** Chat GUI path is verified end-to-end
(`p1_chat_gui_test.py` 7/7, real reply) **and** typed continuity works in the real GUI
(`p1_gui_continuity_test.py` 7/7 — recall returned `My temporary phrase is BLUE ORBIT.`).
The **cross-modality** step (a voice turn recalling a typed turn, and vice-versa, in the
GUI) still needs the owner's microphone.

**F. Actual provider GUI flow** — **PENDING OWNER ACTION.**
Implementation evidence: full provider lifecycle at the daemon HTTP API the WPF calls
(9/9 + 16/16). GUI interaction not performed.

**G. Slow-provider and active-request close** — **PENDING OWNER ACTION (close-while-active).**
Implementation evidence: the dispatcher-blocking root cause was fixed earlier today and
proven with real GUI instrumentation (15s/40s slow provider → 0 hung, window moved;
Stop → "(stopped)"). **Chat responds** in the real GUI (automated: `p1_chat_gui_test.py`
7/7, real model reply). A working Stop is **not** proof of close-while-active, which was
not run.

**H. Chat-to-verified-browser action** — **FAIL (confirmed defect).**
Automated real-GUI evidence (3 runs, ≤135 s) shows the Chat request routes to capability
**`application.open` target `youtube`**, which fails because `apps.resolve('youtube')` is
`None` (*"application 'youtube' is not installed"*). No browser launches, no playback
evidence, and the reply is model narration rather than the honest failure (see §4b). The
browser authority itself works (direct-CDP proof), but the Chat→action integration does
**not** route web targets to it. **Fix not applied** — it needs (1) a small additive
web-service→`browser.navigate` routing fast-path **and** (2) a multi-step verified browser
action (navigate → search → select → verify playback), which is a browser-authority/mission
design change the gate forbids here. See §4b for the full design.

**I. Targeted build and regression results** — **IMPLEMENTATION PASS.**
WPF 0/0; `verify_build_match` 31/31; runtime synced + verified; targeted suites green
(voice session 22/22, voice loop 15/15, owner acceptance 16/16, Hindi engine 15/15,
preview runtime voice probe 11/11, context audit 4/4, session persistence 7/7,
lifecycle continuity 15/15, restart 17/17, provider flow 9/9 + 16/16, preview launch
20/20, chat sanity 7/7, dict-key lint OK). No full release matrix, no installer, no Phase 2.

**J. Remaining owner actions or defects** — **8 workflows pending owner action** (A–H).
No known blocking defect. The only missing inputs are a human speaker, real speakers, and
a visible desktop. Follow `OWNER_ACCEPTANCE.md`; paste the diagnostics output and a
PASS/FAIL line per workflow.

---

## 6. Honest bottom line

Phase 1 is **not** fully closed. Every automated, environment-runnable check passes;
**four** real defects were found and fixed; and real automated GUI evidence confirms the
Preview window renders with truthful identities, that typed Chat works end-to-end against
the real provider, and that typed continuity recalls correctly in the GUI. **However**, the
same GUI automation found a **confirmed defect (item H, FAIL)**: the **Chat→browser action
routes `youtube` to `application.open`, which cannot resolve it, so no browser launches**
(root cause confirmed; fix not applied). **No owner-facing workflow has been marked
accepted** — the voice turns, Home-reaction, cross-modality GUI continuity, provider-GUI,
close-while-active and Chat→browser workflows still need a human at the microphone and a
visible desktop. Phase 1 closes only after the owner runs `OWNER_ACCEPTANCE.md` A–H **and**
item H is fixed.
