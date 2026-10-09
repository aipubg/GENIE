"""GENIE daemon lifecycle (core/lifecycle) — spec §8.18 startup sequence.

    config -> logging -> DB/migrations -> audit -> vault -> trust
    -> memory -> missions -> registry/gateway -> director -> computer -> agents
    -> restore interrupted missions -> IPC server -> ready
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

if __package__ in (None, ""):  # allow `python core/lifecycle.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Config, get_config
from core.contracts import CallContext
from core.db import get_db
from core.session_store import SessionStore
from core.events import get_bus
from core.logging_setup import bind_trace, get_logger, setup_logging
from core.orchestrator import Orchestrator

from agents.runtime import AgentRuntime, CapabilityWorker
from computer.service import ComputerService
from context.builder import ContextBuilder
from director.nedle2 import NeedleDirector, get_director, reload_director
from director.needle_runtime import get_runtime
from memory.service import MemoryService
from missions.service import LockService, MissionService
from models.gateway import Gateway
from models.health import HealthMonitor
from models.registry import ModelRegistry
from security.audit import AuditLog, get_audit
from security.policy import get_policy
from security.trust import TrustService
from security.vault import Vault, get_vault
from models.eligibility import EligibilityTracker

log = get_logger("core.lifecycle")


# ---------------------------------------------------------------------------
# Conversation transcript (per session). Shared by typed Chat AND voice, so a
# question about "what I just asked" resolves identically in both.
#
# Phase 0 closure: recent turns are persisted via SessionStore (SQLite) so a
# backend restart preserves owner-session continuity. The in-memory _HISTORY is
# kept only as a fallback when no daemon SessionStore is active (unit tests,
# bare imports). The authoritative path is the persisted store.
# ---------------------------------------------------------------------------
_HISTORY_LOCK = threading.Lock()
_HISTORY: Dict[str, list] = {}          # session_id -> [(user_text, genie_reply)] (fallback)
_MAX_TURNS = 20
_SESSION_STORE = None                   # core.session_store.SessionStore set by Daemon.start()

# "what did I just ask/say"  -> previous OWNER message
_ASKED_RE = re.compile(
    r"(what\s+did\s+i\s+(just\s+)?(ask|say)|"
    r"maine\s+(abhi\s+)?kya\s+(pucha|poocha|kaha)|"
    # "maine abhi kya <phrase/number/...> bola tha" -> recall the previous
    # OWNER message. Covers the owner-acceptance phrasings ("Maine abhi kya
    # phrase bola tha?") so the answer is deterministic, not model-dependent.
    r"maine\s+(abhi\s+)?kya\s+\w*\s*bola|"
    r"mera\s+(aakhri|last)\s+sawaal)", re.I)

# "what did you just say" / "repeat your last answer" -> previous GENIE reply
_SAID_RE = re.compile(
    r"(what\s+did\s+you\s+(just\s+)?(say|answer|reply)|"
    r"repeat\s+(your|the)\s+(last|previous)\s+(answer|reply)|"
    r"tumne\s+(abhi\s+)?kya\s+kaha|"
    r"apna\s+(aakhri|last)\s+jawab\s+dohrao)", re.I)

# "what were we talking about" -> short recap of recent turns
_TOPIC_RE = re.compile(
    r"(what\s+(were|are)\s+we\s+talking|"
    r"hum\s+(kya|kis)\s+(baat|bare)\s+kar\s+rahe)", re.I)


def _history(session_id: str) -> list:
    if _SESSION_STORE is not None and session_id == _SESSION_STORE.session_id:
        return _SESSION_STORE.recent()
    with _HISTORY_LOCK:
        return list(_HISTORY.get(session_id or "owner", []))


def _remember_turn(session_id: str, user_text: str, reply: str) -> None:
    if not reply:
        return
    if _SESSION_STORE is not None and session_id == _SESSION_STORE.session_id:
        _SESSION_STORE.append(user_text, reply)
        return
    with _HISTORY_LOCK:
        turns = _HISTORY.setdefault(session_id or "owner", [])
        turns.append(((user_text or "").strip(), (reply or "").strip()))
        del turns[:-_MAX_TURNS]


def _context_answer(session_id: str, text: str) -> Optional[str]:
    """Deterministic answer for context questions, or None to run normally.

    The transcript stored for a session never contains the message currently
    being processed, so 'what did I just ask' can only ever return the PREVIOUS
    owner turn.
    """
    t = (text or "").strip()
    if not t:
        return None
    turns = _history(session_id)
    if not turns:
        return None          # nothing recorded yet - fall through to the model

    prev_user, prev_reply = turns[-1]

    if _ASKED_RE.search(t):
        return (f'Aapne abhi pucha tha: "{prev_user}"')
    if _SAID_RE.search(t):
        return (f'Maine abhi kaha tha: "{prev_reply}"')
    if _TOPIC_RE.search(t):
        recent = turns[-3:]
        lines = "\n".join(f"- Aap: {u}\n  GENIE: {r[:160]}" for u, r in recent)
        return "Hum abhi is baare mein baat kar rahe the:\n" + lines
    return None


def _degraded_reasons(voice, voice_status) -> list:
    """Voice degraded reasons, tolerating the nested status shape.

    VoiceService.status() nests them under pipeline.status()["degraded"].
    """
    if not isinstance(voice_status, dict):
        return []
    pipeline = voice_status.get("pipeline") or {}
    if isinstance(pipeline, dict) and pipeline.get("degraded"):
        return list(pipeline["degraded"])
    if voice_status.get("degraded"):
        return list(voice_status["degraded"])
    try:
        return list(voice.pipeline.degraded_reasons())
    except Exception:
        return []


class Daemon:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.started_at = 0
        self.ready = False
        self._lock = threading.RLock()
        self.services: Dict[str, Any] = {}
        self._loaded_build = self._read_build_identity()

    # ------------------------------------------------------------------ boot
    def start(self) -> "Daemon":
        self.started_at = time.time()
        setup_logging(self.config.get("log_level", "INFO"),
                      self.config.data_dir / "logs" / "genie.log")
        log.info("GENIE daemon starting (instance=%s)", self.config.get("instance_id"))

        # 1. database + migrations
        db = get_db(self.config.db_path, wal=self.config.get("database.wal", True))
        db.migrate()
        self.services["db"] = db

        # 2. audit + vault + trust  (security first — spec §1, D-007)
        audit = AuditLog(db)
        self.services["audit"] = audit
        vault = Vault(self.config.vault_path, audit=audit)
        self.services["vault"] = vault
        trust = TrustService(db, audit=audit, default_deny=self.config.get("security.default_deny", True))
        self.services["trust"] = trust

        # 3. memory + missions
        memory = MemoryService(db, audit=audit, trust=trust)
        self.services["memory"] = memory
        # Phase 0 closure: persisted owner-session store (restart-safe continuity).
        # Separate from long-term MemoryService — ephemeral session state only.
        from core.contracts import OWNER_SESSION_ID
        self.services["session_store"] = SessionStore(db, session_id=OWNER_SESSION_ID)
        global _SESSION_STORE
        _SESSION_STORE = self.services["session_store"]
        missions = MissionService(db, audit=audit)
        self.services["missions"] = missions
        self.services["locks"] = LockService(db)

        # 4. models
        # The registry needs the vault so provider credential *presence* is
        # truthful: a secret_ref in config only means a reference is configured,
        # not that a key was ever stored.
        registry = ModelRegistry(vault=vault)
        self.services["registry"] = registry
        health = HealthMonitor(db)
        self.services["health"] = health
        eligibility = EligibilityTracker(db)
        self.services["eligibility"] = eligibility
        gateway = Gateway(registry, vault, get_policy(), db, health=health,
                          eligibility=eligibility,
                          max_attempts=int(self.config.get("models.max_failover_attempts", 3)),
                          allow_mock_fallback=bool(self.config.get("models.mock_mode", False)))
        self.services["gateway"] = gateway

        # 5. director (NEDLE2)
        director = get_director(self.config)
        self.services["director"] = director

        # 6b2. plugins (Phase 4) — separate processes, permissions, crash isolation
        plugins = None
        try:
            from plugins.service import PluginService
            plugins = PluginService(db, audit=audit, trust=trust,
                                    root=Path(__file__).resolve().parents[1] / "plugins")
            self.services["plugins"] = plugins
            log.info("plugins: %s discovered", len(plugins.registry.all()))
        except Exception as exc:
            log.warning("plugin service unavailable: %s", exc)

        # 6. computer engine + agents  (Phase 2: PLAN -> ACT -> OBSERVE -> VERIFY -> RECOVER)
        computer = ComputerService(trust=trust, audit=audit, db=db,
                                   locks_service=self.services["locks"],
                                   data_dir=str(self.config.data_dir))
        self.services["computer"] = computer
        computer.executor.visual.gateway = gateway
        from security.injection_guard import get_guard
        guard = get_guard(audit=audit)
        self.services["guard"] = guard

        # 6a. skills (Phase 5) — learned + taught procedures. The registry owns selection;
        # every step still runs through the capability worker (PTE, plugins, verification).
        skills = None
        try:
            from skills.service import SkillService
            skills = SkillService(
                db, audit=audit, missions=missions, trust=trust, computer=computer,
                guard=guard, locks=self.services["locks"], gateway=gateway,
                capability_provider=computer.capabilities,
                plugin_provider=lambda: sorted((plugins.capabilities() or {}).keys())
                if plugins else [],
            )
            self.services["skills"] = skills
            log.info("skills: %s registered", skills.registry.stats().get("skills", 0))
        except Exception as exc:
            log.warning("skill service unavailable: %s", exc)

        # 6b. device mesh (Phase 6) — the registry is authoritative; every command is
        # permission-checked, idempotent, replay-protected and verified by the node.
        devices = None
        try:
            from devices.service import DeviceService
            devices = DeviceService(
                db, audit=audit, trust=trust, vault=vault,
                port=int(self.config.get("devices.port", 8765)),
                auto_start=self.config.get("devices.enabled", True))
            self.services["devices"] = devices
            # let the router's vocabulary grow with the devices that actually paired
            from director import tools as tool_catalog
            tool_catalog.register_device_ids(devices.registry.known_ids())
            log.info("devices: %s registered (%s online)", len(devices.registry.all()),
                     len(devices.registry.online()))
        except Exception as exc:
            log.warning("device service unavailable: %s", exc)

        # 6c. perception (Phase 8) — screen/camera/audio/presence/fusion. Privacy-first: private
        # zones have camera OFF by default, and a camera fails closed if its indicator is missing.
        perception = None
        try:
            from perception.service import PerceptionService
            perception = PerceptionService(db, audit=audit, computer=computer, devices=devices,
                                           gateway=gateway, config=self.config.all() if
                                           hasattr(self.config, "all") else {})
            self.services["perception"] = perception
            log.info("perception: %d zone(s), camera %s", len(perception.zones()),
                     "available" if perception.camera_status()["available"] else "unavailable")
        except Exception as exc:
            log.warning("perception service unavailable: %s", exc)

        # 6d. proactivity (Phase 9) — scoring, notifications, quiet hours, risky-action warnings
        proactive = None
        try:
            from proactive.service import ProactiveService
            proactive = ProactiveService(
                db, audit=audit, presence=perception.presence if perception else None,
                devices=devices, config=self.config.all() if hasattr(self.config, "all") else {})
            self.services["proactive"] = proactive
            log.info("proactive: dedupe=%ss quiet_windows=%s",
                     proactive.notifier.policy.dedupe_window_s,
                     len(proactive.notifier.policy.quiet_hours))
        except Exception as exc:
            log.warning("proactive service unavailable: %s", exc)

        # 6d-ii. forecasting — authority/router. The default engines are
        # deterministic (base rate + historical frequency); none of them can
        # invent a probability, and the service reports insufficient evidence
        # rather than guessing. MiroFish stays a specialist engine, not the
        # authority.
        try:
            from forecast.service import ForecastService
            self.services["forecast"] = ForecastService()
            log.info("forecast: %d deterministic engine(s)",
                     len(self.services["forecast"].engines))
        except Exception as exc:
            log.warning("forecast service unavailable: %s", exc)

        # 6d-iii. security findings — GENIE's canonical finding store. Strix (or
        # any specialist) may REPORT into it, but GENIE/PTE remains the
        # authority and the store never fabricates findings.
        try:
            from security.findings import SecurityFindingService
            self.services["security_findings"] = SecurityFindingService()
            log.info("security findings store: registered")
        except Exception as exc:
            log.warning("security findings service unavailable: %s", exc)

        # 6e. agent teams (Phase 10) — factory, DAG, mailbox, blackboard, artifacts, budgets.
        # The runner is bound to the gateway so a team task really reaches a model.
        # (This block was accidentally duplicated: AgentService was constructed
        # twice at boot, the first instance thrown away. Removed - see W4 dedupe.)
        agents = None
        try:
            from agents.service import AgentService
            agents = AgentService(
                db, audit=audit, locks=self.services["locks"], gateway=gateway,
                runner=self._agent_runner(gateway, memory, skills))
            self.services["agents_service"] = agents
            log.info("agent teams: %d profile(s) registered",
                     agents.factory.status()["agents"])
        except Exception as exc:
            log.warning("agent service unavailable: %s", exc)

        # The artifact registry is created inside AgentService but the Media
        # surface needs it as a first-class service, so publish it here.
        if agents is not None:
            self.services["artifacts"] = agents.artifacts

        # 6e-i. knowledge — imported sources the owner pointed GENIE at. Distinct
        # from memory: memory is what GENIE learned about the owner, knowledge is
        # documents the owner imported. Different store, different lifetime.
        try:
            from knowledge.service import KnowledgeService
            self.services["knowledge"] = KnowledgeService(db, audit=audit)
            log.info("knowledge service: registered")
        except Exception as exc:
            log.warning("knowledge service unavailable: %s", exc)

        # 6e-ii. experience bank — trajectory lessons recorded by the competition
        # engine and the agent runtime. Reports insufficient evidence rather than
        # inventing a lesson, so an empty bank is a truthful empty bank.
        try:
            from experience.bank import ExperienceBank
            self.services["experience"] = ExperienceBank(db)
            log.info("experience bank: registered")
        except Exception as exc:
            log.warning("experience bank unavailable: %s", exc)

        worker = CapabilityWorker(computer=computer, device=devices, plugins=plugins,
                                  guard=guard, skills=skills)
        if skills is not None:
            skills.worker = worker        # bind the executor after construction
        runtime = AgentRuntime(step_runner=worker, audit=audit)
        self.services["agents"] = runtime
        self.services["worker"] = worker

        # 6b. windows event sources (polling watchers -> event bus)
        watcher = None
        try:
            from computer.events import WindowsEventWatcher
            watcher = WindowsEventWatcher(db=db)
            watcher.start()
            self.services["watcher"] = watcher
        except Exception as exc:
            log.warning("windows event watcher unavailable: %s", exc)

        # 7. orchestrator
        self.orchestrator = Orchestrator(
            director=director, missions=missions, memory=memory, gateway=gateway,
            computer=computer, agent_runtime=runtime,
            context_builder=ContextBuilder(memory=memory), trust=trust, audit=audit,
            config=self.config, perception=perception, proactive=proactive,
            mission_control=self._mission_control)
        self.services["orchestrator"] = self.orchestrator

        # 7a. Mission execution engine (Pass 3). The runner bridges mission steps
        #     to the existing agent engine; the scheduler only decides WHEN to ask
        #     it to progress a mission. Neither owns mission truth (MissionService
        #     does). Disabled in tests via GENIE_SCHEDULER=0.
        try:
            from missions.runner import MissionRunner
            from missions.scheduler import MissionScheduler
            self.services["mission_runner"] = MissionRunner(self.services)
            # the orchestrator was built first; hand it the engine it executes with
            self.orchestrator.mission_runner = self.services["mission_runner"]
            scheduler = MissionScheduler(
                missions, self._on_mission_due,
                interval_s=float(self.config.get("missions.scheduler_interval_s", 20)))
            self.services["scheduler"] = scheduler
            import os as _os
            if _os.environ.get("GENIE_SCHEDULER", "1") != "0":
                scheduler.start()
        except Exception as exc:
            log.warning("mission scheduler unavailable: %s", exc)

        # 6c. voice (Phase 3) — provider chain with honest degradation
        voice = None
        if self.config.get("voice.enabled", True):
            try:
                from voice.service import VoiceService
                voice = VoiceService(
                    self.config, db=db, vault=vault,
                    # Phase 0 closure: the voice turn must enter the SAME canonical
                    # owner session as typed chat, so session_id is explicit.
                    handler=lambda text, ctx: self.chat(
                        text, session_id=ctx.session_id, dry_run=ctx.dry_run,
                        person_id=ctx.person_id),
                    live_tool_handler=self._live_tool,
                    context_provider=lambda: _history(OWNER_SESSION_ID),
                    record_turn=lambda user, reply: _remember_turn(OWNER_SESSION_ID, user, reply),
                    screen_provider=self._live_visual_frames,
                    workspace_root=self.config.data_dir)
                self.services["voice"] = voice
                reasons = voice.pipeline.degraded_reasons()
                if reasons:
                    log.warning("voice running in degraded mode: %s", "; ".join(reasons))
            except Exception as exc:
                log.warning("voice service unavailable: %s", exc)

        if perception is not None:
            from perception.local_monitor import LocalMonitor
            self.services["local_monitor"] = LocalMonitor(
                perception, computer.desktop_awareness, memory.observations,
                self.config.data_dir, vault=vault)

        # 7b. NEDLE2 auto-provisioning (non-blocking: boot must never wait on a download)
        self._needle_state: Dict[str, Any] = {"provisioning": False, "smoke": None,
                                              "error": "", "provisioned_at": None}
        if (self.config.get("director.needle.auto_provision", True)
                and self.config.get("director.needle.enabled", True)):
            self._start_needle_provisioning()

        # 8. recover interrupted work (§14.2). CrashRecovery detects whether the *previous* run
        #    shut down cleanly, purges stale locks/leases and reports interrupted missions rather
        #    than silently dropping them. It never auto-resumes without the owner asking.
        from pathlib import Path as _Path
        from core.hardening import CrashRecovery

        recovery = CrashRecovery(_Path(self.config.data_dir) / "running.flag")
        self.services["crash_recovery"] = recovery
        crash_report = recovery.recover(locks=self.services["locks"], missions=missions)
        if crash_report["unclean_shutdown"]:
            log.warning("recovered from an unclean shutdown: %s", crash_report)
        recovery.mark_start()  # mark THIS run, so a crash can be detected next boot

        interrupted = missions.interrupted()
        if interrupted:
            log.warning("%s interrupted mission(s) detected — offered for resume", len(interrupted))

        audit.record(who="system", action="daemon.start", why="boot",
                     result=f"{len(interrupted)} interrupted")
        self.ready = True
        log.info("GENIE daemon ready in %.2fs", time.time() - self.started_at)
        return self

    # ----------------------------------------------------- NEDLE2 provisioning
    @staticmethod
    def _agent_runner(gateway, memory, skills):
        """The function a team agent calls to do its work.

        Kept deliberately small: the agent gets its task, the blackboard facts and artifact
        *references* — never another agent's transcript. If no model is reachable the runner says
        so honestly, and the orchestrator treats it as a provider failure (which is what makes
        golden #7 meaningful).
        """
        def run(agent, task, ctx):
            from core.contracts import CallContext, DataClass, ModelRequirement
            requirement = ModelRequirement(
                capability=str(agent.model_requirement.get("capability", "reasoning")),
                data_class=DataClass.INTERNAL, max_input_tokens=6000)
            prompt_parts = [
                f"ROLE: {agent.role}", f"PURPOSE: {agent.purpose}",
                f"TASK: {task.objective}",
                f"COMPLETION: {task.completion_criteria or 'produce the requested result'}",
            ]
            blackboard = ctx.get("blackboard") or {}
            if blackboard.get("decisions"):
                prompt_parts.append("DECISIONS: " + "; ".join(
                    f"{d.get('key')}={d.get('value')}" for d in blackboard["decisions"][:6]))
            if ctx.get("artifact_refs"):
                prompt_parts.append("ARTIFACTS: " + "; ".join(
                    f"{a.get('name')} ({a.get('artifact_id')})"
                    for a in ctx["artifact_refs"][:6]))
            if ctx.get("mailbox"):
                prompt_parts.append("MESSAGES: " + "; ".join(
                    f"{m.get('from')}: {m.get('subject')}" for m in ctx["mailbox"][:5]))
            try:
                completion = gateway.complete(
                    CallContext(person_id="agent", agent_id=agent.agent_id),
                    requirement, [{"role": "user", "content": "\n".join(prompt_parts)}],
                    max_tokens=1500)
                return {"ok": True, "provider": completion.provider_id,
                        "detail": (completion.text or "")[:2000],
                        "cost_usd": float(getattr(completion, "cost_usd", 0.0) or 0.0),
                        "verdict": "accept"}
            except Exception as exc:
                return {"ok": False, "error": str(exc),
                        "error_code": "provider_unavailable"}
        return run

    def _start_needle_provisioning(self) -> None:
        """Installer/bootstrapper step: provision the official Needle 2 runtime, verify it,
        then run the routing smoke test and swap the director in if it passes."""
        state = self._needle_state
        if state.get("provisioning"):
            return
        state["provisioning"] = True

        def _job() -> None:
            try:
                runtime = get_runtime(self.config.get("director.needle.runtime_dir") or None)
                if not runtime.verify().get("ok"):
                    result = runtime.provision()
                    if not result.get("ok"):
                        state["error"] = str(result.get("error") or "provision failed")
                        log.error("NEDLE2 provisioning failed: %s", state["error"])
                        return
                    director = reload_director(self.config)
                    if isinstance(director, NeedleDirector):
                        self.services["director"] = director
                        self.orchestrator.director = director
                    else:
                        state["error"] = "provisioned but the director did not activate"
                        return
                else:
                    director = self.services.get("director")
                if not isinstance(director, NeedleDirector):
                    state["error"] = "director is not the real Needle engine"
                    return
                smoke = director.smoke_test()
                state["smoke"] = {"ok": smoke.get("ok"), "passed": smoke.get("passed"),
                                  "total": smoke.get("total")}
                state["provisioned_at"] = int(time.time())
                self.services["director"] = director
                self.orchestrator.director = director
                log.info("NEDLE2 activated after provisioning — smoke %s/%s",
                         smoke.get("passed"), smoke.get("total"))
            except Exception as exc:
                state["error"] = str(exc)
                log.error("NEDLE2 provisioning error: %s", exc)
            finally:
                state["provisioning"] = False

        threading.Thread(target=_job, name="needle-provision", daemon=True).start()

    def needle_status(self) -> Dict[str, Any]:
        runtime = get_runtime(self.config.get("director.needle.runtime_dir") or None)
        director = self.services.get("director")
        return {
            "runtime": runtime.status(),
            "director": director.status() if hasattr(director, "status") else {"engine": "heuristic"},
            "provisioning": bool(self._needle_state.get("provisioning")) if hasattr(self, "_needle_state") else False,
            "smoke": self._needle_state.get("smoke") if hasattr(self, "_needle_state") else None,
            "error": self._needle_state.get("error") if hasattr(self, "_needle_state") else "",
        }

    # ----------------------------------------------------------------- turn
    def chat(self, text: str, session_id: str = "owner", dry_run: bool = False,
             person_id: str = "owner") -> Dict[str, Any]:
        ctx = CallContext(person_id=person_id, session_id=session_id, dry_run=dry_run)
        bind_trace(ctx.trace_id)
        if not self.ready:
            return {"error": "daemon not ready"}
        self.orchestrator.begin_request(ctx.trace_id)
        try:
            result = self.orchestrator.handle_text(text, ctx)
            reply = result.get("reply") or ""
            if reply:
                _remember_turn(session_id, text, reply)
            return result
        except Exception as exc:
            log.exception("turn failed")
            if self.services.get("audit"):
                self.services["audit"].record(who=person_id, action="chat.error",
                                              why=str(exc), result="failed", trace_id=ctx.trace_id)
            return {"error": str(exc), "trace_id": ctx.trace_id}
        finally:
            self.orchestrator.end_request(ctx.trace_id)

    def stream_chat(self, text: str, session_id: str = "owner",
                    person_id: str = "owner", request_id: str | None = None):
        """Yield reasoning deltas for a text turn (real streaming where supported).

        This runs the SAME request path as `chat()` — director decision, memory
        retrieval, context packet — and only the final model call streams. Voice
        and typed input share this path; the companion is never a second brain.
        """
        from core.contracts import CallContext
        ctx = CallContext(person_id=person_id, session_id=session_id)
        bind_trace(ctx.trace_id)
        if not self.ready:
            yield "GENIE daemon is not ready yet.", "final"
            return

        # Context questions are answered from the recorded transcript BEFORE
        # the turn is added, so they can never quote the incoming message as
        # "what you just asked". Deterministic lookup, no tools, no model.
        answer = _context_answer(session_id, text)
        if answer is not None:
            _remember_turn(session_id, text, answer)
            yield answer, "final"
            return

        active_id = request_id or ctx.trace_id
        self.orchestrator.begin_request(active_id)
        try:
            parts: list = []
            for delta, kind in self.orchestrator.stream_text(text, ctx):
                if kind in ("token", "final"):
                    parts.append(delta)
                yield delta, kind
            _remember_turn(session_id, text, "".join(parts).strip())
        except Exception as exc:  # noqa: BLE001
            log.exception("streaming turn failed")
            yield f"(stream error: {exc})", "final"
        finally:
            self.orchestrator.end_request(active_id)

    # --------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        director = self.services.get("director")
        orchestrator = getattr(self, "orchestrator", None)
        laya_shadow = getattr(orchestrator, "laya_shadow", None)
        return {
            "instance_id": self.config.get("instance_id"),
            "ready": self.ready,
            "uptime_s": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "director": director.status() if hasattr(director, "status") else {"engine": "heuristic"},
            "needle": self.needle_status(),
            "laya_shadow": dict(laya_shadow.last) if laya_shadow is not None else {"state": "disabled"},
            "memory": self.services["memory"].stats() if "memory" in self.services else {},
            "missions": {"interrupted": len(self.services["missions"].interrupted())}
            if "missions" in self.services else {},
            "providers": self.services["registry"].summary() if "registry" in self.services else [],
            "provider_health": self.services["health"].snapshot() if "health" in self.services else [],
            "vault": self.services["vault"].status() if "vault" in self.services else {},
            "computer_capabilities": self.services["computer"].capabilities()
            if "computer" in self.services else [],
            "computer": self.services["computer"].health() if "computer" in self.services else {},
            "browser": self._browser_status(),
            "build": self._build_identity(),
            "voice": self.services["voice"].status() if "voice" in self.services else
            {"enabled": False},
            "plugins": self.services["plugins"].status() if "plugins" in self.services else
            {"count": 0, "plugins": []},
            "skills": self.services["skills"].status() if "skills" in self.services else
            {"skills": {"skills": 0}, "teaching": {"active": False}},
            "devices": self.services["devices"].status() if "devices" in self.services else
            {"count": 0, "devices": []},
            "perception": self.services["perception"].status()
            if "perception" in self.services else {"zones": []},
            "proactive": self.services["proactive"].status()
            if "proactive" in self.services else {"delivered": 0},
            "agents": self.services["agents_service"].status()
            if "agents_service" in self.services else {"factory": {"agents": 0}},
            "injection_guard": self.services["guard"].status() if "guard" in self.services else {},
            "events": get_bus().stats,
            "source_of_truth": self._source_of_truth(),
        }

    def _build_identity(self) -> Dict[str, Any]:
        # Snapshot at daemon construction: a later sync must not relabel an
        # already-running interpreter as if it loaded the replacement files.
        return dict(self._loaded_build)

    def _read_build_identity(self) -> Dict[str, Any]:
        """Compact read-only build identity (Pass 3.5).

        Answers "which build am I actually running?" so a stale binary can never
        be mistaken for the latest source again. Reports the backend runtime's
        recorded source commit + when it was last synced.
        """
        import json as _json
        from pathlib import Path as _Path
        # Two layouts: the SOURCE tree (<repo>/backend-dist/backend-runtime/…) and
        # the PACKAGED runtime (<runtime-root>/app/… → manifest at <runtime-root>).
        here = _Path(__file__).resolve()
        candidates = [
            # packaged: app/core/lifecycle.py -> app -> backend-runtime/
            here.parents[2] / "RUNTIME_MANIFEST.json",
            # source: <repo>/core/lifecycle.py -> <repo>/backend-dist/backend-runtime/
            here.parents[1] / "backend-dist" / "backend-runtime"
            / "RUNTIME_MANIFEST.json",
            here.parents[1] / "RUNTIME_MANIFEST.json",
            here.parents[2] / "backend-dist" / "backend-runtime"
            / "RUNTIME_MANIFEST.json",
        ]
        manifest = next((c for c in candidates if c.exists()), candidates[0])
        out: Dict[str, Any] = {"source_commit": "", "file_count": 0,
                               "source_fingerprint": "", "python_executable": __import__("sys").executable,
                               "daemon_pid": os.getpid(),
                               "runtime_manifest": "missing", "runtime_synced_at": "",
                               # Where THIS backend is actually running from. A
                               # frontend paired with a backend from a different
                               # root is a mixed build; saying so is the whole
                               # point of this block.
                               "backend_root": str(here.parents[2]),
                               "backend_app": str(here.parents[1])}
        if not manifest.exists():
            return out
        try:
            data = _json.loads(manifest.read_text(encoding="utf-8"))
            out["source_commit"] = str(data.get("source_commit") or "")
            out["source_fingerprint"] = str(data.get("source_fingerprint") or "")
            out["file_count"] = int(data.get("file_count") or len(data.get("files") or {}))
            out["runtime_manifest"] = "present"
            import datetime as _dt
            out["runtime_synced_at"] = _dt.datetime.fromtimestamp(
                manifest.stat().st_mtime).isoformat(timespec="seconds")
        except Exception as exc:  # noqa: BLE001
            out["runtime_manifest"] = f"unreadable: {exc}"
        return out

    def _source_of_truth(self) -> Dict[str, Any]:
        """Diagnostic truth: what runtime GENIE is actually using (P0.12).

        Normal UI may remain simple; Advanced diagnostics expose these details.
        """
        from core.paths import data_dir, data_dir_source
        voice = self.services.get("voice")
        voice_status = voice.status() if voice else {"enabled": False}
        # VoiceService.status() nests provider status under "providers":
        #   {"providers": {"input":…, "stt":…, "tts":…}, "pipeline": …}
        providers = voice_status.get("providers", {}) if isinstance(voice_status, dict) else {}
        stt = providers.get("stt", {}) if isinstance(providers, dict) else {}
        if not stt and isinstance(voice_status, dict):
            stt = voice_status.get("stt", {}) or {}
        registry = self.services.get("registry")
        health = self.services.get("health")
        # Prefer the Gateway's explicit latest selection. DB row order is not
        # chronological; using snapshot[-1] showed a stale model after a newer
        # provider had actually served the request.
        active = {}
        gateway = self.services.get("gateway")
        selected = gateway.last_selection() if gateway and hasattr(gateway, "last_selection") else {}
        if selected.get("selected_provider") and selected.get("selected_model"):
            active = {
                "provider_id": selected.get("selected_provider", ""),
                "model_id": selected.get("selected_model", ""),
                "capability": selected.get("capability", ""),
            }
        elif health and hasattr(health, "snapshot"):
            snap = health.snapshot()
            if snap:
                last = max(snap, key=lambda row: int(row.get("last_ok", 0) or 0)) \
                    if isinstance(snap, list) else snap
                active = {
                    "provider_id": last.get("provider_id", ""),
                    "model_id": last.get("model_id", ""),
                    "capability": last.get("capability", ""),
                }
        build = self._build_identity()
        return {
            "backend_root": build.get("backend_root", ""),
            "backend_app": build.get("backend_app", ""),
            "source_commit": build.get("source_commit", ""),
            "runtime_synced_at": build.get("runtime_synced_at", ""),
            "data_directory": str(data_dir()),
            "data_directory_source": data_dir_source(),
            "session_id": "owner",
            # Provider status shapes differ: the multilingual faster-whisper
            # provider reports "provider"; the fallback reports "name".
            "stt_provider": (stt.get("provider") or stt.get("name") or "")
            if isinstance(stt, dict) else "",
            "stt_model_id": (stt.get("model_id") or stt.get("model_path") or "")
            if isinstance(stt, dict) else "",
            "stt_ready": bool(stt.get("ready", stt.get("available", False)))
            if isinstance(stt, dict) else False,
            "stt_loaded": stt.get("loaded", False) if isinstance(stt, dict) else False,
            "stt_reason": stt.get("reason", "") if isinstance(stt, dict) else "",
            "active_provider": active.get("provider_id", ""),
            "active_model": active.get("model_id", ""),
            "active_capability": active.get("capability", ""),
            # VoiceService nests degraded reasons under pipeline.status()
            "degraded": _degraded_reasons(voice, voice_status),
        }

    def _mission_control(self, text: str):
        """Resolve + apply a mission command from the owner's words (chat/voice)."""
        try:
            from missions.control import apply_control
            return apply_control(self.services, text)
        except Exception as exc:
            log.debug("mission control failed: %s", exc)
            return None

    def _on_mission_due(self, mission_id: str, info: Dict[str, Any]) -> None:
        """A scheduled / continuous mission is due. Hand it to the runner.

        Catch-up is the scheduler's call: a long outage collapses to ONE run and
        reports how many occurrences were skipped — never one run per missed slot.
        """
        runner = self.services.get("mission_runner")
        if runner is None:
            return
        log.info("mission %s due (missed=%s) — executing", mission_id,
                 (info or {}).get("missed", 0))
        try:
            runner.execute(mission_id)
        except Exception as exc:
            log.warning("scheduled mission %s failed: %s", mission_id, exc)

    def _browser_status(self) -> Dict[str, Any]:
        try:
            from browser.service import get_browser
            return get_browser(workspace_root=self.config.data_dir
                               / self.config.get("computer.workspace", "workspace")).status()
        except Exception as exc:
            return {"error": str(exc)}

    def _live_tool(self, name, args, cancel_event):
        from core.contracts import CallContext, OWNER_SESSION_ID
        ctx = CallContext(session_id=OWNER_SESSION_ID, person_id="owner")
        from computer.tool_bridge import NAMES, execute
        if name in NAMES:
            return execute(self.services["computer"], ctx, name, args, cancel_event)
        if name == "perform_task":
            return self.orchestrator.handle_direct_action(str(args.get("request", "")), ctx, cancel_event)
        return {"ok": False, "error": "Unknown Live tool"}

    def _live_visual_frames(self):
        """One labelled frame per interval; camera and screen have separate consent."""
        import io
        from PIL import Image, ImageDraw
        screens = self._live_screen_frames()
        monitor = self.services.get("local_monitor")
        camera = monitor.preview(for_cloud=True) if monitor else b""
        if not camera:
            return screens
        tiles = []
        for raw, label in [(raw, "Desktop") for raw in screens] + [(camera, "Webcam (not desktop)")]:
            with Image.open(io.BytesIO(raw)) as source:
                source.thumbnail((1280, 900))
                tile = Image.new("RGB", (max(320, source.width), source.height + 30), "black")
                tile.paste(source, (0, 30))
                ImageDraw.Draw(tile).text((8, 8), label, fill="white")
                tiles.append(tile)
        composite = Image.new("RGB", (max(t.width for t in tiles), sum(t.height for t in tiles)))
        y = 0
        for tile in tiles:
            composite.paste(tile, (0, y))
            y += tile.height
        composite.thumbnail((1600, 1440))
        output = io.BytesIO()
        composite.save(output, format="JPEG", quality=65)
        if not monitor.settings.cloud_camera_consent or not monitor.running:
            return []
        awareness = self.services["computer"].desktop_awareness
        if screens and (not awareness.settings.remote_visual_consent or awareness.is_paused):
            return []
        return [output.getvalue()]

    def _live_screen_frames(self):
        """Existing, redacted local previews only; cloud sharing is opt-in."""
        import io
        from PIL import Image, ImageDraw
        awareness = self.services["computer"].desktop_awareness
        if not awareness.settings.remote_visual_consent:
            return []
        tiles = []
        for display in awareness.observe().displays:
            if not awareness.settings.remote_visual_consent:
                return []
            raw = awareness.preview_frame(display.index)
            if not raw:
                continue
            with Image.open(io.BytesIO(raw)) as frame:
                frame.thumbnail((800, 450))
                tile = Image.new("RGB", (800, 480), "black")
                tile.paste(frame, (0, 30))
                ImageDraw.Draw(tile).text((8, 8), f"Display {display.index}; desktop bounds {display.rect}", fill="white")
                tiles.append(tile)
        if not tiles or not awareness.settings.remote_visual_consent or awareness.is_paused:
            return []
        columns = min(2, len(tiles))
        composite = Image.new("RGB", (800 * columns, 480 * ((len(tiles) + columns - 1) // columns)))
        for index, tile in enumerate(tiles):
            composite.paste(tile, ((index % columns) * 800, (index // columns) * 480))
        composite.thumbnail((1600, 1440))
        output = io.BytesIO()
        composite.save(output, format="JPEG", quality=65)
        return [output.getvalue()]

    def stop(self) -> None:
        log.info("GENIE daemon stopping")
        laya = getattr(getattr(self, "orchestrator", None), "laya_shadow", None)
        if laya is not None:
            laya.close()
        voice = self.services.get("voice")
        if voice is not None:
            voice.stop_listening()
        monitor = self.services.get("local_monitor")
        if monitor is not None:
            monitor.stop()
        computer = self.services.get("computer")
        if computer is not None:
            computer.desktop_awareness.stop()
        if self.services.get("audit"):
            self.services["audit"].record(who="system", action="daemon.stop", why="shutdown")
        if self.services.get("devices"):
            try:
                self.services["devices"].stop()
            except Exception as exc:
                log.debug("device transport shutdown failed: %s", exc)
        # Pass 3 — stop the mission scheduler thread before anything else, so no
        # new mission work is dispatched during shutdown.
        scheduler = self.services.get("scheduler")
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception as exc:
                log.debug("scheduler shutdown failed: %s", exc)
        # Point 7 — controlled shutdown of the browser GENIE owns. Only the pid
        # GENIE launched is terminated; the owner's own browsers are untouched.
        try:
            from browser.service import get_browser
            get_browser(workspace_root=self.config.data_dir
                        / self.config.get("computer.workspace", "workspace")).shutdown()
        except Exception as exc:
            log.debug("browser shutdown failed: %s", exc)
        # clear the crash flag so the next boot does not think this run died (§14.2)
        recovery = self.services.get("crash_recovery")
        if recovery is not None:
            try:
                recovery.mark_clean()
            except Exception as exc:
                log.debug("crash flag cleanup failed: %s", exc)
        get_bus().shutdown()
        self.ready = False


_DAEMON: Optional[Daemon] = None


def get_daemon(reload: bool = False) -> Daemon:
    global _DAEMON
    if _DAEMON is None or reload:
        _DAEMON = Daemon().start()
    return _DAEMON
