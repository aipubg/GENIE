"""GENIE orchestrator (core/orchestrator) — the end-to-end turn loop.

Flow (master spec §81 / user's Phase-1 E2E goal):

    UI/text -> identity+context -> NEDLE2 director -> mission/capability decision
            -> permission (PTE) -> execute -> verify -> audit -> mission result -> UI

A large remote model is only called when the director says `reasoning_required`.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional

from core.contracts import (
    CallContext, DataClass, EventType, Mission, MissionState, MissionStep,
    ModelRequirement, TaskType,
)
from core.events import get_bus
from core.logging_setup import bind_trace, get_logger
from director.semantic_guard import GuardVerdict, guard_decision

log = get_logger("core.orchestrator")


# Questions about the conversation itself: these are answered from history,
# never dispatched to a tool/device/plugin.
_CONVERSATION_CTX_RE = re.compile(
    r"\b(what\s+(did|was|were)\s+(i|we|you)|"
    r"what\s+did\s+i\s+(just\s+)?(ask|say)|"
    r"what\s+were\s+we\s+talking|"
    r"repeat\s+(your|the)\s+(last|previous)|"
    r"why\s+did\s+you\s+(choose|pick|say)|"
    r"remind\s+me\s+what\s+i)", re.I)

_WINDOW_INVENTORY_RE = re.compile(
    r"\b(?:list|show|check|what|which|see)\b.{0,60}"
    r"\b(?:currently\s+)?(?:open|visible|active|running)\b.{0,40}"
    r"\b(?:desktop\s+)?(?:windows?|window\s+titles|apps?|applications?)\b|"
    r"\b(?:what|which)\b.{0,35}\b(?:windows?|apps?|applications?)\b.{0,30}"
    r"\b(?:are\s+)?(?:currently\s+)?(?:open|visible|active|running)\b|"
    r"\b(?:kaun\s+si|kaun\s+se)\b.{0,40}\b(?:windows?|apps?)\b.{0,25}"
    r"\b(?:khuli|khule|chal\s+rahi)\b|"
    r"\b(?:screen|desktop)\s+(?:par|pe)\s+(?:kya|kaun)\b.{0,30}\b(?:khula|khuli)\b",
    re.I)


def _apply_conversation_override(text: str, decision):
    """Shared conversation authority, applied AFTER any director.

    A greeting or short remark ("hi", "hey", "HAY", "thanks") must never become
    a durable Mission, and a context question must never become a tool call.
    Enforced here so every director - heuristic, model, or future providers -
    obeys the same rule instead of each re-implementing it (a model director
    previously promoted "HAY" to a Mission).
    """
    try:
        from director.heuristics import is_conversational, is_instruction_question
    except Exception:
        return decision
    if not (is_conversational(text) or is_instruction_question(text) or _CONVERSATION_CTX_RE.search(text or "")):
        return decision

    decision.mission_required = False      # never create/keep a Mission row
    decision.schedule = ""
    decision.tasks = []                    # never dispatch a tool
    decision.intent = "conversation"
    decision.reply_hint = ""
    decision.reasoning_required = True     # still answered by the model
    return decision


def _apply_mission_gate(text: str, decision):
    """Positive gate on durable Mission creation.

    A director (including a model provider) may *recommend* a Mission, but the
    request itself must carry evidence of recurrence / scheduling / continuous
    monitoring / long-running responsibility. Without that evidence the turn
    stays a conversation or a simple action — no Mission row is created.
    """
    if not getattr(decision, "mission_required", False):
        return decision
    try:
        from director.heuristics import has_durable_intent
    except Exception:
        return decision
    if has_durable_intent(text):
        return decision
    log.info("mission suppressed: no durable intent in %r", text)
    decision.mission_required = False
    decision.schedule = ""
    return decision


def _fast_deterministic_decision(text: str, ctx: CallContext,
                                 context_hint: Optional[Dict[str, Any]] = None):
    """Resolve bounded local actions before Needle or a remote model.

    Opening a website, playing a named YouTube query, changing volume, or
    opening an application does not need a model round-trip. Keeping this fast
    path here also means voice and typed chat share the same low-latency route.
    Complex conversation and durable work still go through the normal director.
    """
    try:
        from director.heuristics import is_instruction_question
        if is_instruction_question(text):
            return None
        if _WINDOW_INVENTORY_RE.search(text or ""):
            from director.base import DirectorDecision, DirectorTask
            decision = DirectorDecision(source="heuristic-fast", confidence=1.0,
                                        intent="simple_action")
            decision.tasks = [DirectorTask(type=TaskType.COMPUTER_ACTION,
                                           device="pc_main", capability="window.list",
                                           params={})]
            decision.raw["fast_path"] = True
            return decision
        from director.heuristics import HeuristicDirector
        decision = HeuristicDirector().classify(text, ctx, context_hint)
        decision = _apply_web_action_plan(text, decision)
        decision = _apply_desktop_action(text, decision)
        if decision.raw.get("reason") == "observed-control-tool-loop":
            return decision
        if decision.tasks and not decision.mission_required:
            decision.source = "heuristic-fast"
            decision.raw["fast_path"] = True
            return decision
    except Exception as exc:
        log.debug("deterministic fast path unavailable: %s", exc)
    return None


def _apply_web_action_plan(text: str, decision):
    """Route website / named-web-service requests to the BROWSER authority.

    Applied AFTER every director (like the conversation/mission overrides), so a
    remote Director cannot bypass it. A website is not an installed application:
    `application.open` cannot resolve it — that is exactly why
    "YouTube open karo" silently did nothing. This converts the decision into
    browser.navigate / browser.media.play / browser.fullscreen and respects an
    explicitly requested browser (e.g. Brave).
    """
    from director.heuristics import is_instruction_question, is_control_interaction
    if is_instruction_question(text):
        return _apply_conversation_override(text, decision)
    if is_control_interaction(text):
        decision.tasks = []
        decision.reasoning_required = True
        decision.mission_required = False
        decision.schedule = ""
        decision.intent = "simple_action"
        decision.reply_hint = ""
        decision.raw = {**(decision.raw or {}), "reason": "observed-control-tool-loop"}
        return decision
    try:
        from director.heuristics import plan_web_action
    except Exception:
        return decision
    plan = plan_web_action(text)
    if not plan:
        return decision
    unsupported = [step for step in plan
                   if str(step.get("capability", "")) == "plan.unsupported"]
    if unsupported:
        # The deterministic website planner only handles a narrow set of
        # navigation/media actions. Do not execute a partial plan and strand
        # ordinary observe/click/fill steps; hand the whole request to the
        # bounded conversational tool loop instead.
        decision.tasks = []
        decision.mission_required = False
        decision.schedule = ""
        decision.reasoning_required = True
        decision.provider_category = "reasoning"
        decision.intent = "web_interaction"
        decision.reply_hint = ""
        decision.raw = {**(decision.raw or {}),
                        "reason": "web-interaction-tool-loop",
                        "unsupported_clauses": [
                            str((step.get("params") or {}).get("clause", ""))[:240]
                            for step in unsupported]}
        return decision
    # never override a decision that is already a browser action
    if any(str(getattr(t, "capability", "")).startswith("browser.")
           for t in (decision.tasks or [])):
        return decision
    try:
        from director.base import DirectorTask
    except Exception:
        return decision
    decision.tasks = [
        DirectorTask(type=TaskType.BROWSER_ACTION, device="pc_main",
                     capability=str(step["capability"]),
                     params=dict(step.get("params") or {}))
        for step in plan
    ]
    decision.mission_required = False
    decision.schedule = ""
    decision.reply_hint = ""
    decision.reasoning_required = False
    decision.intent = "simple_action" if len(decision.tasks) == 1 else "multi_step_action"
    try:
        # keep the annotated plan (ids, dependencies, expected observations and
        # verification conditions) so the runner can execute it with verification
        decision.raw = {**(decision.raw or {}), "reason": "web-action-plan",
                        "web_plan": plan}
    except Exception:
        pass
    return decision


def _apply_desktop_action(text: str, decision):
    import re
    low = (text or "").strip().lower()
    if not re.search(r"\b(desktop awareness|screen shar(?:e|ing)|desktop preview)\b", low):
        return decision
    if re.search(r"\b(how|why|kaise|don't|do not|mat)\b", low):
        return decision
    params = {}
    if re.search(r"\b(stop|pause|band|off|disable)\b", low):
        cap = "desktop.pause"
    elif re.search(r"\b(resume|continue)\b", low):
        cap = "desktop.resume"
    elif re.search(r"\b(start|enable|chalu|on)\b", low):
        cap = "desktop.settings.set"
        params = {"settings": {"enabled": True, "local_preview": True}}
        if re.search(r"\b(auto|automatically|startup|background)\b", low):
            params["settings"]["auto_start"] = True
    else:
        return decision
    from director.base import DirectorTask
    decision.tasks = [DirectorTask(type=TaskType.COMPUTER_ACTION, device="pc_main",
                                  capability=cap, params=params)]
    decision.mission_required = False
    decision.reasoning_required = False
    decision.intent = "simple_action"
    decision.reply_hint = ""
    decision.raw = {}
    return decision


def _use_conversational_tools(text: str, decision) -> bool:
    """Reasoning may discover tools even when phrasing misses action keywords."""
    from director.heuristics import is_conversational, is_instruction_question
    return (not decision.mission_required
            and not is_conversational(text)
            and not is_instruction_question(text)
            and not _CONVERSATION_CTX_RE.search(text or ""))


class Orchestrator:
    def __init__(self, director, missions, memory, gateway, computer,
                 agent_runtime, context_builder, trust=None, audit=None, config=None,
                 perception=None, proactive=None,
                 mission_runner=None, mission_control=None):
        self.director = director
        self.missions = missions
        self.memory = memory
        self.gateway = gateway
        self.computer = computer
        self.agents = agent_runtime
        self.context = context_builder
        self.trust = trust
        self.audit = audit
        self.config = config
        from director.laya_runtime import LayaRuntime
        self.laya_shadow = LayaRuntime(config)
        # Shadow mode remains inert at startup: loading the isolated CPU model
        # costs ~2.1 GB RAM and must not delay Preview or compete with Live audio.
        self.laya_shadow.start()
        # Phase 8: the fused environment picture, consumed by the Context Engine (§6.11)
        self.perception = perception
        # Phase 9: proactivity. Destructive steps are flagged *before* they run, so the warning
        # is timely rather than a post-mortem (golden #13).
        self.proactive = proactive
        # Pass 3: the mission execution engine. `mission_runner` executes a
        # mission's durable step DAG; `mission_control` resolves and mutates a
        # mission from the owner's words. Both are optional so a bare
        # Orchestrator (tests) still works.
        self.mission_runner = mission_runner
        self.mission_control = mission_control
        # Owner cancellation for a bounded multi-step action. Set by the IPC
        # cancel endpoint; checked BETWEEN steps so a Stop aborts the rest of the
        # plan instead of running it to completion.
        self.cancel_event = threading.Event()
        self._request_local = threading.local()
        self._request_lock = threading.Lock()
        self._active_requests = {}
        self._pending_stops = {}

    def begin_request(self, request_id):
        import time
        with self._request_lock:
            now = time.monotonic()
            self._pending_stops = {k: v for k, v in self._pending_stops.items() if v > now}
            if request_id in self._active_requests:
                raise ValueError("Request identity is already active")
            event = threading.Event()
            if self._pending_stops.pop(request_id, None) is not None:
                event.set()
            self._active_requests[request_id] = event
            self._request_local.event = event

    def end_request(self, request_id):
        with self._request_lock:
            self._active_requests.pop(request_id, None)
        self._request_local.event = None

    def cancel_request(self, request_id):
        import time
        if not request_id:
            with self._request_lock:
                if len(self._active_requests) != 1:
                    return False
                next(iter(self._active_requests.values())).set()
            return True
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            return False
        with self._request_lock:
            event = self._active_requests.get(request_id)
            if event is not None:
                event.set()
            else:
                # Stop can arrive before the stream reaches begin_request.
                if len(self._pending_stops) >= 128:
                    self._pending_stops.pop(next(iter(self._pending_stops)))
                self._pending_stops[request_id] = time.monotonic() + 60
        return True

    def _turn_cancel(self):
        return getattr(self._request_local, "event", None) or self.cancel_event

    # ------------------------------------------------------------------- turn
    def _audit_guard(self, ctx: CallContext, text: str, verdict: GuardVerdict) -> None:
        """A refused route must be recorded, not just logged.

        The router was confident and wrong; the audit trail is how that becomes visible and
        how the guard itself can be reviewed later.
        """
        detail = (f"blocked={','.join(verdict.blocked_capabilities)} "
                  f"confidence={verdict.original_confidence:.2f}")
        if self.audit:
            try:
                self.audit.record(who=ctx.person_id, device=ctx.device_id,
                                  action="route.semantic_guard",
                                  why=f"{text[:160]} :: {verdict.reason[:160]}",
                                  mission_id=ctx.mission_id, result="blocked",
                                  trace_id=ctx.trace_id)
            except Exception as exc:            # auditing must never break a turn
                log.warning("guard audit failed: %s", exc)
        try:
            get_bus().publish(EventType.POLICY_VIOLATION_BLOCKED, {
                "kind": "semantic_route_guard", "detail": detail,
                "text": text[:200], "verdict": verdict.to_dict(),
                "trace_id": ctx.trace_id})
        except Exception as exc:
            log.warning("guard event failed: %s", exc)

    def handle_direct_action(self, text: str, ctx: CallContext, cancel_event=None) -> Dict[str, Any]:
        """Live voice already has a reasoning model; do not call another one."""
        text = (text or "").strip()[:2000]
        hint = self._context_hint(ctx)
        decision = _fast_deterministic_decision(text, ctx, hint)
        if decision is None:
            # B07 repair: Live perform_task used to be fast-only and refused any
            # unmatched request. Escalate into the canonical orchestrated action
            # path (Director + guard + tasks) so Live is no longer a dead end.
            return self._escalate_live_action(text, ctx, cancel_event)
        decision = _apply_conversation_override(text, decision)
        verdict = guard_decision(text, decision, context=hint)
        if not verdict.allowed or not decision.tasks:
            return {"ok": False, "error": "Request did not pass the action routing guard; no action was performed."}
        return self._run_tasks(text, ctx, decision, [],
                               risks=self._prescan_risks(decision, ctx), cancel_event=cancel_event)

    def _escalate_live_action(self, text: str, ctx: CallContext,
                              cancel_event=None) -> Dict[str, Any]:
        """B07: run an unmatched actionable Live request through the SAME Director
        and guard path as typed Chat, instead of refusing because the fast
        classifier did not match. One bounded attempt; honest failure otherwise."""
        try:
            hint = self._context_hint(ctx)
            decision = (_fast_deterministic_decision(text, ctx, hint)
                        or self.laya_shadow.route(text)
                        or self.director.classify(text, ctx, hint))
            self.laya_shadow.observe(text, decision.intent)
            decision = _apply_conversation_override(text, decision)
            decision = _apply_web_action_plan(text, decision)
            decision = _apply_desktop_action(text, decision)
            verdict = guard_decision(text, decision, context=hint)
            if not verdict.allowed or not decision.tasks:
                return {"ok": False,
                        "error": "No available tool can perform this request; no action was performed."}
            return self._run_tasks(text, ctx, decision, [],
                                   risks=self._prescan_risks(decision, ctx),
                                   cancel_event=cancel_event)
        except Exception as exc:  # noqa: BLE001
            log.error("live action escalation failed: %s", exc)
            return {"ok": False, "error": f"No action was performed: {exc}"}

    def handle_text(self, text: str, ctx: CallContext) -> Dict[str, Any]:
        bind_trace(ctx.trace_id)
        text = (text or "").strip()
        if not text:
            return {"reply": "", "error": "empty input"}

        # 0) mission control (Pass 3) — the owner is talking ABOUT a mission
        #    ("pause the YouTube mission"), not starting new work.
        ctrl = self._mission_control(text)
        if ctrl is not None:
            return {"reply": ctrl.get("reply", ""), "mission_id": ctrl.get("mission_id"),
                    "trace_id": ctx.trace_id,
                    "decision": {"intent": "mission_control", "action": ctrl.get("action")},
                    "memory_written": [], "steps": [], "state": "mission_control"}

        # 1) director decision ------------------------------------------------
        hint = self._context_hint(ctx)
        decision = (_fast_deterministic_decision(text, ctx, hint)
                    or self.laya_shadow.route(text)
                    or self.director.classify(text, ctx, hint))
        self.laya_shadow.observe(text, decision.intent)
        decision = _apply_conversation_override(text, decision)
        decision = _apply_mission_gate(text, decision)
        decision = _apply_web_action_plan(text, decision)
        decision = _apply_desktop_action(text, decision)
        # 1b) semantic route guard (Phase 5 correction) ------------------------
        # The router's confidence is not evidence. A continuation request must never execute
        # an unrelated media/device action just because a 14 MB model was confident.
        verdict = guard_decision(text, decision, context=hint)
        if not verdict.allowed:
            self._audit_guard(ctx, text, verdict)
        log.info("director source=%s tasks=%s reasoning=%s",
                 decision.source, [t.capability for t in decision.tasks],
                 decision.reasoning_required)

        # 2) memory writes the director proposed (service validates) ----------
        written = []
        for w in decision.memory_writes:
            try:
                rid = self.memory.write(
                    ctx, type=str(w.get("type", "semantic"))[:32],
                    entity=str(w.get("entity", ""))[:128],
                    value=str(w.get("value", ""))[:2000],
                    confidence=float(w.get("confidence", 0.6) or 0.6),
                    source=f"director:{decision.source}")
                written.append(rid)
            except Exception as exc:
                log.warning("memory write rejected: %s", exc)

        # 3) action path ------------------------------------------------------
        if decision.tasks:
            risks = self._prescan_risks(decision, ctx)
            return self._run_tasks(text, ctx, decision, written, risks=risks)

        # 4) mission path — DURABLE autonomous work (Point 6) -----------------
        # Only durable/autonomous work becomes a Mission. A normal exchange must
        # never create one.
        if decision.mission_required:
            return self._start_mission(text, ctx, decision, written)

        # 5) conversation path — NO mission -----------------------------------
        # A normal exchange is not durable work. The turn still runs through the
        # same reasoning authority; only the persisted Mission row is omitted.
        #
        # For a pure conversation (greeting / small talk / context question) even
        # the ephemeral row is skipped: its "CREATED, steps=0" state was leaking
        # into the reply, so "HAY" came back as "Mission HAY ... CREATED".
        try:
            from director.heuristics import is_conversational as _is_conv
            pure_conversation = _is_conv(text) or bool(
                _CONVERSATION_CTX_RE.search(text or ""))
        except Exception:
            pure_conversation = False
        ephemeral = None if pure_conversation else self._ephemeral_mission(ctx, text)
        reply = self._reason(text, ctx, decision, ephemeral)
        return {
            "reply": reply, "mission_id": None, "trace_id": ctx.trace_id,
            "decision": decision.to_dict(), "memory_written": written,
            "steps": [], "state": "conversation",
        }

    def _ephemeral_mission(self, ctx: CallContext, text: str) -> Mission:
        """A mission-shaped context object for ONE conversational turn.

        It is deliberately NOT persisted: a normal exchange is not durable work,
        so it must never appear in the owner's Missions. It exists only so the
        context builder keeps the same shape it always had.
        """
        return Mission(goal=text[:240], owner=ctx.person_id,
                       targets=["pc_main"], criteria=[], trace_id=ctx.trace_id)

    def _start_mission(self, text: str, ctx: CallContext, decision,
                       written: List[Any]) -> Dict[str, Any]:
        """Create a DURABLE Mission for autonomous / long-running work.

        The mission is persisted — it belongs in the owner's dashboard — and a
        first plan/reply is produced by the reasoning authority. Autonomous
        continuation is the mission runner's job, not this turn's.
        """
        from core.contracts import now_ms as _now
        schedule_hint = str(getattr(decision, "schedule", "") or "")
        continuous = bool(getattr(decision, "continuous", False))
        mission = self.missions.create(ctx, text[:240], targets=["pc_main"],
                                       criteria=["deliverable produced"],
                                       schedule=schedule_hint, continuous=continuous)
        self.missions.transition(ctx, mission.mission_id, MissionState.PLANNED)

        # Recurrence: normalise the owner's own words into a durable schedule so
        # it survives exit/relaunch (Pass 3).
        try:
            from missions.scheduler import normalize as _norm, next_run as _next
            spec = _norm(text, _now())
            if spec:
                self.missions.set_schedule(mission.mission_id, spec, _next(spec, _now()))
                self.missions.set_fields(mission.mission_id,
                                         schedule=str(spec.get("raw", ""))[:64])
        except Exception as exc:  # noqa: BLE001
            log.debug("schedule normalisation failed: %s", exc)

        runner = self.mission_runner
        if runner is None:
            # No engine wired (bare Orchestrator in tests): keep the honest
            # plan/reply path rather than pretending the mission ran.
            mctx = ctx.with_(mission_id=mission.mission_id)
            reply = self._reason(text, mctx, decision, mission)
            return {"reply": reply, "mission_id": mission.mission_id,
                    "trace_id": ctx.trace_id, "decision": decision.to_dict(),
                    "memory_written": written, "steps": [],
                    "state": MissionState.PLANNED.value}

        # Derive the durable step DAG and execute the first iteration.
        try:
            runner.ensure_plan(mission)
            result = runner.execute(mission.mission_id)
        except Exception as exc:  # noqa: BLE001
            log.error("mission execution failed: %s", exc)
            self.missions.record_error(mission.mission_id, str(exc))
            result = {}
        progress = self.missions.progress(mission.mission_id)
        reply = (f"Mission created — {progress['total']} steps planned, "
                 f"{progress['done']} done. Now: {progress.get('current') or 'starting'}.")
        return {"reply": reply, "mission_id": mission.mission_id,
                "trace_id": ctx.trace_id, "decision": decision.to_dict(),
                "memory_written": written, "steps": [],
                "progress": progress,
                "state": result.get("state", MissionState.PLANNED.value)}

    def _mission_control(self, text: str):
        """Run the owner's words through mission control, if it is a command."""
        if self.mission_control is None:
            return None
        try:
            return self.mission_control(text)
        except Exception as exc:  # noqa: BLE001
            log.debug("mission control failed: %s", exc)
            return None

    # ---------------------------------------------------------------- streaming
    def stream_text(self, text: str, ctx: CallContext):
        """Stream one text turn. Same authority path as `handle_text`.

        Yields (text, kind) where kind is:
          "token"    - a real piece of streamed model output
          "progress" - a status line for work actually being done
          "final"    - a complete reply delivered at once (no token stream)

        Two honest cases:
          * reasoning turn  -> token deltas arrive from the provider as produced;
          * action turn     -> the work is tool execution, not prose, so a short
                               progress line is emitted and the verified result
                               arrives as a final delta. GENIE never fabricates
                               intermediate tokens for work it is not doing.
        """
        bind_trace(ctx.trace_id)
        text = (text or "").strip()
        if not text:
            yield "", "final"
            return

        # mission control (Pass 3) — handled before any new work is classified.
        ctrl = self._mission_control(text)
        if ctrl is not None:
            yield ctrl.get("reply", ""), "final"
            return

        hint = self._context_hint(ctx)
        decision = (_fast_deterministic_decision(text, ctx, hint)
                    or self.laya_shadow.route(text)
                    or self.director.classify(text, ctx, hint))
        self.laya_shadow.observe(text, decision.intent)

        # Same shared override as handle_text(): greetings, small talk and
        # context questions can never become a Mission or a tool call.
        decision = _apply_conversation_override(text, decision)
        decision = _apply_mission_gate(text, decision)
        decision = _apply_web_action_plan(text, decision)
        decision = _apply_desktop_action(text, decision)

        verdict = guard_decision(text, decision, context=hint)
        if not verdict.allowed:
            self._audit_guard(ctx, text, verdict)

        for w in decision.memory_writes:
            try:
                self.memory.write(
                    ctx, type=str(w.get("type", "semantic"))[:32],
                    entity=str(w.get("entity", ""))[:128],
                    value=str(w.get("value", ""))[:2000],
                    confidence=float(w.get("confidence", 0.6) or 0.6),
                    source=f"director:{decision.source}")
            except Exception as exc:  # noqa: BLE001
                log.warning("memory write rejected: %s", exc)

        if decision.tasks:
            # action path: emit real progress, then the verified result
            caps = [t.capability for t in decision.tasks]
            yield f"(running {', '.join(str(c) for c in caps)}) ", "progress"
            try:
                risks = self._prescan_risks(decision, ctx)
                result = self._run_tasks(text, ctx, decision, [], risks=risks)
                yield result.get("reply", "") or "(no reply)", "final"
            except Exception as exc:  # noqa: BLE001
                log.error("streamed action turn failed: %s", exc)
                yield f"(action failed: {exc})", "final"
            return

        # Point 6 — durable work becomes a Mission; a normal exchange does not.
        if decision.mission_required:
            schedule = str(getattr(decision, "schedule", "") or "")
            continuous = bool(getattr(decision, "continuous", False))
            mission = self.missions.create(ctx, text[:240], targets=["pc_main"],
                                           criteria=["deliverable produced"],
                                           schedule=schedule, continuous=continuous)
            self.missions.transition(ctx, mission.mission_id, MissionState.PLANNED)
            # B01/E53 repair: this GUI streaming branch used to persist PLANNED and
            # then only stream model prose, so a mission created from WPF never
            # reached MissionRunner or its schedule setup. It now performs the SAME
            # schedule normalisation + runner handoff as the nonstream path.
            try:
                from missions.scheduler import normalize as _norm, next_run as _next
                from core.contracts import now_ms as _now
                spec = _norm(text, _now())
                if spec:
                    self.missions.set_schedule(mission.mission_id, spec, _next(spec, _now()))
                    self.missions.set_fields(mission.mission_id,
                                             schedule=str(spec.get("raw", ""))[:64])
            except Exception as exc:  # noqa: BLE001
                log.debug("schedule normalisation failed: %s", exc)

            mctx = ctx.with_(mission_id=mission.mission_id)
            yield "(mission created — planning) ", "progress"
            runner = self.mission_runner
            if runner is None:
                # Bare orchestrator (tests): keep the honest plan/reply path.
                try:
                    for delta in self.stream_reason(text, mctx, decision, mission):
                        yield delta
                except Exception as exc:  # noqa: BLE001
                    log.error("mission planning failed: %s", exc)
                    yield f"(planning failed: {exc})", "final"
                return
            try:
                runner.ensure_plan(mission)
                runner.execute(mission.mission_id)
                progress = self.missions.progress(mission.mission_id)
                yield (f"Mission created — {progress['total']} steps planned, "
                       f"{progress['done']} done. Now: "
                       f"{progress.get('current') or 'starting'}."), "final"
            except Exception as exc:  # noqa: BLE001
                log.error("mission execution failed: %s", exc)
                self.missions.record_error(mission.mission_id, str(exc))
                yield f"(mission execution failed: {exc})", "final"
            return

        # Conversation: no mission row is created. For a pure conversation even
        # the ephemeral row is skipped so no "CREATED, steps=0" state can leak
        # into the reply.
        try:
            from director.heuristics import is_conversational as _is_conv
            _pure = _is_conv(text) or bool(_CONVERSATION_CTX_RE.search(text or ""))
        except Exception:
            _pure = False
        ephemeral = None if _pure else self._ephemeral_mission(ctx, text)
        try:
            for delta in self.stream_reason(text, ctx, decision, ephemeral):
                yield delta
        except Exception as exc:  # noqa: BLE001
            log.error("streaming reasoning unavailable: %s", exc)
            yield ("Remote model abhi available nahi hai (offline/degraded mode). "
                   "Settings mein provider connection aur API key check karein. Is jawab ne koi action nahi chalaya."), "final"

    # ------------------------------------------------------------------ tasks
    def _prescan_risks(self, decision, ctx: CallContext) -> List[Dict[str, Any]]:
        """Warn about destructive steps while they can still be stopped."""
        if self.proactive is None:
            return []
        try:
            return self.proactive.prescan(
                [t.to_dict() for t in decision.tasks], mission_id=ctx.mission_id or "",
                person_id=ctx.person_id)
        except Exception as exc:
            log.debug("risk prescan failed: %s", exc)
            return []

    def _run_tasks(self, text: str, ctx: CallContext, decision, written,
                   risks: Optional[List[Dict[str, Any]]] = None, cancel_event=None) -> Dict[str, Any]:
        """Execute a BOUNDED one-time action plan (no permanent Mission record).

        A request with several sequential steps is an ordinary multi-step action,
        NOT durable work — so it must not create a Mission row. The plan lives
        only for this turn. Every step carries a real state and an execution
        receipt (the executor's own evidence, never model narration); the first
        failure stops the run and the remaining dependent steps are reported as
        NOT ATTEMPTED rather than described as if they ran.
        """
        tasks = list(decision.tasks or [])
        # A cancel belongs to THIS plan. Request-local events ensure that a Stop
        # from a previous action can never cancel the next one.
        plan_cancel = cancel_event if cancel_event is not None else self._turn_cancel()
        results: List[Dict[str, Any]] = []
        not_attempted: List[Dict[str, Any]] = []
        failed = False
        cancelled = False
        cancel_boundary = "none"

        # A website plan is executed by the website-task engine:
        # PLAN -> ACT -> OBSERVE -> VERIFY -> NEXT STEP, with dependencies,
        # expected observations, verification conditions and bounded recovery.
        web_plan: List[Dict[str, Any]] = []
        try:
            raw = decision.raw if isinstance(decision.raw, dict) else {}
            web_plan = list(raw.get("web_plan") or [])
        except Exception:
            web_plan = []
        if web_plan and len(web_plan) == len(tasks) and tasks:
            try:
                from browser.website_task import WebsiteTaskEngine
            except Exception as exc:
                log.warning("website task engine unavailable: %s", exc)
                web_plan = []
        if web_plan and len(web_plan) == len(tasks) and tasks:
            engine = WebsiteTaskEngine(
                lambda cap, prm: self._execute_task(
                    ctx, {"type": TaskType.BROWSER_ACTION.value, "device": "pc_main",
                          "capability": cap, "target": "", "params": prm},
                          cancel_event=plan_cancel),
                cancel_event=plan_cancel)
            browser = ""
            for st in web_plan:
                browser = str((st.get("params") or {}).get("browser", "") or browser)
            run = engine.run(web_plan, browser=browser)
            for s in run.get("steps") or []:
                results.append({
                    "index": s.get("index"), "capability": s.get("capability"),
                    "status": s.get("status"), "ok": bool(s.get("ok")),
                    "verified": bool(s.get("verified")),
                    "receipt": s.get("receipt", ""),
                    "observation": s.get("observation", {}),
                    "checks": s.get("checks", []), "attempts": s.get("attempts", 1),
                    "blocked": bool(s.get("blocked")),
                })
            not_attempted = list(run.get("not_attempted") or [])
            failed = run.get("state") == "FAILED"
            cancelled = run.get("state") == "CANCELLED"
            cancel_boundary = run.get("cancel_boundary", "none")
            state = run.get("state", "FAILED")
            if cancelled:
                done = [str(r.get("capability")) for r in results
                        if r.get("status") == "succeeded"]
                reply = "Aapne yeh action rok diya."
                if done:
                    reply += f" (ho gaya: {', '.join(done)})"
            elif failed:
                err = next((r.get("receipt") for r in results
                            if r.get("status") in ("failed", "blocked", "timeout")), "")
                reply = ("Main yeh action complete nahi kar paya. "
                         f"Reason: {err or 'step failed'}").strip()
            else:
                reply = decision.reply_hint or self._summarize(results)
            return {
                "reply": reply, "trace_id": ctx.trace_id, "decision": decision.to_dict(),
                "steps": results, "not_attempted": not_attempted, "state": state,
                "memory_written": written, "bounded_plan": True,
                "plan": run.get("plan", []),
                "cancel_requested": run.get("cancel_requested", False),
                "cancel_boundary": cancel_boundary,
                **({"risks": risks} if risks else {}),
            }

        for idx, task in enumerate(tasks):
            if failed or cancelled:
                not_attempted.append({"index": idx + 1, "capability": task.capability,
                                      "status": "not_attempted"})
                continue
            # Owner Stop: checked BETWEEN steps so a cancel aborts the rest of the
            # plan instead of running it to completion.
            if plan_cancel.is_set():
                cancelled = True
                cancel_boundary = f"before_step_{idx + 1}"
                results.append({"index": idx + 1, "capability": task.capability,
                                "status": "cancelled", "ok": False, "verified": False,
                                "receipt": "cancellation requested; this step was NOT started"})
                continue
            # a clause the planner could not map: report it as BLOCKED, never drop
            if task.capability == "plan.unsupported":
                clause = str((task.params or {}).get("clause", "")).strip()
                results.append({
                    "index": idx + 1, "capability": "plan.unsupported",
                    "status": "blocked", "ok": False, "verified": False,
                    "receipt": f"no executor support for this step: {clause!r}"})
                failed = True
                continue
            payload = {"type": task.type.value, "device": task.device,
                       "capability": task.capability, "target": task.target,
                       "params": task.params}
            out = self._execute_task(ctx, payload, cancel_event=plan_cancel)
            ok = bool(out.get("ok"))
            verification = (out.get("data") or {}).get("verification") or out.get("verification") or out.get("verify") or {}
            verified = out.get("verified", verification.get("verified"))
            delivery_only = bool(verification.get("delivery_only") or
                                 (verification.get("evidence") or {}).get("delivery_only"))
            if out.get("blocked"):
                status = "blocked"
            elif ok and verified is True and not delivery_only:
                status = "succeeded"
            elif ok:
                status = "succeeded_unverified"
            else:
                status = "failed"
            receipt = ((out.get("verify") or {}).get("detail")
                       or out.get("detail") or out.get("error") or "")
            record: Dict[str, Any] = {
                "index": idx + 1, "capability": task.capability, "status": status,
                "ok": ok, "verified": verified, "receipt": str(receipt)[:300],
            }
            # Keep the bounded, public storage result so a read-only disk query
            # can answer with measured values instead of a generic success receipt.
            if task.capability in ("files.disk_usage", "system.audio.devices",
                                   "system.network.state") and isinstance(out.get("data"), dict):
                record["data"] = out["data"]
            for k in ("error", "blocked", "detail", "verify"):
                if k in out:
                    record[k] = out[k]
            results.append(record)
            if status in ("failed", "blocked"):
                failed = True

        unverified = any(r.get("status") == "succeeded_unverified" for r in results)
        state = "CANCELLED" if cancelled else ("FAILED" if failed else "UNVERIFIED" if unverified else "COMPLETED")
        if cancelled:
            done = [r["capability"] for r in results if r["status"] == "succeeded"]
            reply = "Aapne yeh action rok diya."
            if done:
                reply += f" (ho gaya: {', '.join(done)})"
            if not_attempted:
                reply += (" (nahi chala: "
                          + ", ".join(str(a["capability"]) for a in not_attempted) + ")")
        elif failed:
            err = next((r.get("error") or r.get("receipt") for r in results
                        if r["status"] in ("failed", "blocked")), "")
            done = [r["capability"] for r in results if r["status"] == "succeeded"]
            reply = ("Main yeh action complete nahi kar paya. "
                     f"Reason: {err or 'step failed'}").strip()
            if done:
                reply += f" (ho gaya: {', '.join(done)})"
            if not_attempted:
                reply += (" (nahi chala: "
                          + ", ".join(str(a["capability"]) for a in not_attempted) + ")")
        elif unverified:
            reply = "Action dispatch returned, but completion was not verified. " + self._summarize(results)
        else:
            # reply_hint is a CLASSIFICATION-TIME suggestion, so it is only used
            # when every step actually succeeded; otherwise the executor receipts
            # speak.
            reply = decision.reply_hint or self._summarize(results)

        # Distinguish "cancel requested" from "the running operation was actually
        # cancelled": a cancel arriving during the LAST step leaves it completed.
        cancel_requested = plan_cancel.is_set()
        if cancel_requested and not cancelled:
            cancel_boundary = "after_all_steps"

        payload_out: Dict[str, Any] = {
            "reply": reply, "trace_id": ctx.trace_id, "decision": decision.to_dict(),
            "steps": results, "not_attempted": not_attempted, "state": state,
            "memory_written": written, "bounded_plan": True,
            "cancel_requested": cancel_requested, "cancel_boundary": cancel_boundary,
        }
        if risks:
            payload_out["risks"] = risks
        return payload_out

    def _execute_task(self, ctx: CallContext, task: Dict[str, Any], cancel_event=None) -> Dict[str, Any]:
        if cancel_event is not None:
            if cancel_event.is_set():
                return {"ok": False, "error": "action cancelled"}
            params = dict(task.get("params") or {})
            params.pop("confirmed", None)
            task = {**task, "params": params}
        worker = self.agents.step_runner if hasattr(self.agents, "step_runner") else None
        if worker is None:
            return {"ok": False, "error": "no worker available"}
        try:
            return (worker(ctx, task, cancel_event=cancel_event)
                    if cancel_event is not None else worker(ctx, task))
        except Exception as exc:
            log.error("task execution error: %s", exc)
            return {"ok": False, "error": str(exc)}

    # --------------------------------------------------------------- reasoning
    def _reason_messages(self, text: str, ctx: CallContext, decision, mission):
        """Build the (requirement, messages) pair used for remote reasoning.

        Shared by the one-shot and the streaming path so both send exactly the
        same context packet — streaming must not change what the model sees.

        Uses ContextCompiler so memory is retrieved ONCE per turn (not once by
        the orchestrator and again by ContextBuilder).
        """
        from core.contracts import ModelRequirement
        from context.compiler import ContextCompiler

        # ONE authoritative memory query + recent turns compilation
        compiler = ContextCompiler(memory=self.memory)
        recent = self._recent_turns(ctx.session_id)
        packet = compiler.compile(
            ctx, text,
            mission=(mission.to_dict() if mission is not None else None),
            environment=self._context_hint(ctx),
            memory_query=decision.memory_query or "",
            recent_turns=recent,
        )

        # Point 1.2 — NEDLE2 classifies the task; the routing requirement is
        # built FROM that classification. NEDLE2 stays classification-only; the
        # authoritative registry/gateway remain the source of truth for which
        # provider/model actually serves the request.
        cap = (decision.provider_category or "reasoning")
        if cap == "none":
            cap = "reasoning"
        tool_loop_required = _use_conversational_tools(text, decision)
        req = ModelRequirement(
            capability=cap,
            min_quality="medium",
            # The adapter uses native function calls when supported and a strict
            # compatibility protocol otherwise; both reach the same dispatcher.
            needs_tools=bool(tool_loop_required or decision.tasks or decision.memory_writes),
            needs_vision=(cap == "vision"),
            data_class=DataClass.INTERNAL, budget_usd=0.25)
        messages = [
            {"role": "system", "content": "You are GENIE. Reply in Hinglish by default "
                                          "(Hindi+English code switching). Be concise and useful. "
                                          "This response does not execute tools. Only claim actions "
                                          "or playback succeeded when current execution receipts prove it. "
                                          "Never invent a website URL, active mission, or running task. "
                                          "Ask for the missing URL or target when an action is ambiguous."},
            {"role": "user", "content": _packet_to_prompt(packet.to_dict(), text)},
        ]
        return req, messages

    def _reason(self, text: str, ctx: CallContext, decision, mission) -> str:
        req, messages = self._reason_messages(text, ctx, decision, mission)
        try:
            from core.tool_dialogue import run
            if _use_conversational_tools(text, decision):
                return run(self.gateway, self.computer, ctx, req, messages, self._turn_cancel(),
                       user_text=text)
            completion = self.gateway.complete(ctx, req, messages, max_tokens=600)
            if self._turn_cancel().is_set():
                return "Stopped."
            return completion.text
        except Exception as exc:
            log.error("remote reasoning unavailable: %s", exc)
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:240]
            return (f"Configured model request failed ({type(exc).__name__}: {detail}). "
                    "Completion is not verified. Check any preceding action receipts before retrying.")

    def stream_reason(self, text: str, ctx: CallContext, decision, mission):
        """Yield reasoning deltas as the provider produces them.

        Same packet as `_reason`. If the provider cannot stream, the adapter
        emits ONE delta containing the whole reply — never a fake split.
        """
        req, messages = self._reason_messages(text, ctx, decision, mission)
        try:
            from core.tool_dialogue import run
            if _use_conversational_tools(text, decision):
                yield "Checking the requested action...\n", "progress"
                yield run(self.gateway, self.computer, ctx, req, messages, self._turn_cancel(),
                          user_text=text), "final"
                return
            for delta, _meta in self.gateway.stream(ctx, req, messages, max_tokens=600):
                if self._turn_cancel().is_set():
                    yield "Stopped.", "final"
                    return
                yield delta, "token"
        except Exception as exc:
            log.error("streaming reasoning unavailable: %s", exc)
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:240]
            yield (f"Configured model request failed ({type(exc).__name__}: {detail}). "
                   "Completion is not verified. Check any preceding action receipts before retrying."), "final"

    # ---------------------------------------------------------------- helpers
    def _recent_turns(self, session_id: str):
        """Return recent (user, reply) tuples for the session."""
        from core.lifecycle import _history
        return _history(session_id)

    def _context_hint(self, ctx: CallContext) -> Dict[str, Any]:
        hint = {
            "person": ctx.person_id,
            "device": ctx.device_id,
            "session": ctx.session_id,
            "dry_run": ctx.dry_run,
        }
        # The environment picture is context, never authority: it can inform a decision but it
        # cannot authorise an action (the semantic guard and the PTE still decide).
        if self.perception is not None:
            try:
                hint["environment"] = self.perception.environment()
            except Exception as exc:
                log.debug("perception unavailable for context: %s", exc)
        # Keep the single desktop-awareness authority in the context packet.
        # This is lightweight window/display state, not a second screenshot
        # pipeline. Pixels remain local unless the owner grants remote visual
        # consent and explicitly asks for visual reasoning.
        try:
            awareness = getattr(self.computer, "desktop_awareness", None)
            if awareness is not None and awareness.is_running and not awareness.is_paused:
                obs = awareness.observe()
                hint["desktop"] = {
                    "locked": obs.locked,
                    "display_count": obs.display_count,
                    "window_count": obs.window_count,
                    "cursor": list(obs.cursor),
                    "foreground": obs.foreground,
                    "displays": [
                        {"index": d.index, "primary": d.primary,
                         "foreground_on_display": d.foreground_on_display,
                         "windows": [
                             {"title": w.get("title", ""),
                              "process": w.get("process", ""),
                              "monitor": w.get("monitor", d.index)}
                             for w in d.windows[:20]]}
                        for d in obs.displays]
                }
        except Exception as exc:
            log.debug("desktop awareness unavailable for context: %s", exc)
        return hint

    @staticmethod
    def _summarize(results: List[Dict[str, Any]]) -> str:
        """Owner-facing summary built from the executor receipts, not narration."""
        parts = []
        for r in results:
            cap = r.get("capability", "action")
            receipt = str(r.get("receipt") or "").strip()
            status = str(r.get("status") or ("ok" if r.get("ok") else "failed"))
            if cap == "files.disk_usage" and isinstance(r.get("data"), dict):
                data = r["data"]
                if isinstance(data.get("volumes"), list):
                    values = [f"{str(v.get('drive') or 'Drive').rstrip('\\:')}: total {v.get('total_gb')} GB, "
                              f"used {v.get('used_gb')} GB, free {v.get('free_gb')} GB"
                              for v in data["volumes"] if v.get("ok")]
                    inaccessible = data.get("inaccessible") or []
                    if inaccessible:
                        values.append("unavailable: " + ", ".join(map(str, inaccessible)))
                    parts.append("; ".join(values) if values else
                                 str(data.get("error") or "No mounted volume was readable."))
                    continue
                if data.get("total_gb") is not None and data.get("free_gb") is not None:
                    drive = str(data.get("drive") or "Drive").rstrip("\\:")
                    parts.append(f"{drive}: total {data.get('total_gb')} GB, "
                                 f"used {data.get('used_gb')} GB, free {data.get('free_gb')} GB")
                    continue
                parts.append(str(data.get("error") or "Drive usage could not be read."))
                continue
            if cap == "system.audio.devices" and isinstance(r.get("data"), dict):
                devices = r["data"].get("devices") or []
                def endpoint_names(channel_key):
                    names = []
                    seen = set()
                    for device in devices:
                        if int(device.get(channel_key, 0) or 0) <= 0:
                            continue
                        name = " ".join(str(device.get("name", "")).split())
                        folded = name.casefold()
                        # PortAudio exposes generic aliases once per host API;
                        # don't repeat them alongside the actual endpoints.
                        if (not name or "sound mapper" in folded
                                or folded in {"primary sound driver", "primary sound capture driver"}
                                or folded in seen):
                            continue
                        seen.add(folded)
                        names.append(name)
                    return names
                inputs = endpoint_names("input_channels")
                outputs = endpoint_names("output_channels")
                if not devices:
                    parts.append(str(r["data"].get("error") or "No audio devices were reported."))
                else:
                    parts.append("Microphones: " + (", ".join(inputs) if inputs else "none detected")
                                 + "; speakers/output: "
                                 + (", ".join(outputs) if outputs else "none detected"))
                continue
            if cap == "system.network.state" and isinstance(r.get("data"), dict):
                interfaces = r["data"].get("interfaces") or []
                if not interfaces:
                    parts.append(str(r["data"].get("error") or "No network interfaces were reported."))
                else:
                    parts.append("Network interfaces: " + "; ".join(
                        f"{item.get('Name') or item.get('name') or 'Adapter'}: "
                        f"{item.get('Status') or item.get('status') or 'unknown'}"
                        for item in interfaces))
                continue
            parts.append(f"{cap}: {status}" + (f" — {receipt}" if receipt else ""))
        return "; ".join(parts)


def _packet_to_prompt(packet: Dict[str, Any], user_text: str) -> str:
    sections = packet.get("sections", {})
    chunks = []
    for name in ("safety", "mission", "environment", "memory", "recent"):
        if name in sections:
            chunks.append(f"[{name}]\n{sections[name]}")
    chunks.append(f"[user]\n{user_text}")
    return "\n\n".join(chunks)
