"""Mission control through chat / voice (Pass 3).

The owner should be able to say "pause the YouTube mission", "resume it",
"change the report to weekly", "stop that mission" or "show me what the
website-monitoring mission is doing" and have GENIE resolve the mission and
perform the mutation — no manual database-like editing.

This module only PARSES the intent and applies a supported mutation through the
existing MissionService / AgentService. Mission truth stays in MissionService.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.contracts import MissionState, now_ms
from core.logging_setup import get_logger

log = get_logger("missions.control")

_PAUSE = re.compile(r"\b(pause|suspend|hold)\b", re.I)
_RESUME = re.compile(r"\b(resume|unpause|continue|restart)\b", re.I)
_CANCEL = re.compile(r"\b(stop|cancel|abort|kill|terminate|end)\b", re.I)
_SHOW = re.compile(r"\b(show|status|progress|report\s+on|what.*(doing|happening)|"
                   r"how.*going)\b", re.I)
_CHANGE = re.compile(r"\b(change|make|set|switch|reschedule|update)\b", re.I)
_MISSION = re.compile(r"\bmission\b", re.I)
_REFER = re.compile(r"\b(it|that|this|them)\b", re.I)

_TARGET_NOISE = re.compile(
    r"\b(the|a|an|my|that|this|it|them|mission|missions|please|genie|pause|"
    r"resume|unpause|continue|restart|stop|cancel|abort|kill|terminate|end|"
    r"show|me|status|progress|report|on|what|is|doing|happening|how|going|"
    r"change|make|set|switch|reschedule|update|to|do\s+not|don't|dont|"
    r"every|weekly|daily|hourly|monthly|morning|evening|night|am|pm)\b", re.I)


def _target(text: str) -> str:
    t = _TARGET_NOISE.sub(" ", text or "")
    return re.sub(r"\s+", " ", t).strip()


def parse_control(text: str) -> Optional[Dict[str, Any]]:
    """Return {action, target, spec?, raw} when the text is a mission command."""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    has_ref = bool(_MISSION.search(low) or _REFER.search(low))
    short = len(low.split()) <= 4

    if _CHANGE.search(low) and _MISSION.search(low):
        from missions.scheduler import normalize
        spec = normalize(t, now_ms())
        if spec:
            return {"action": "reschedule", "target": _target(t), "spec": spec, "raw": t}

    if _SHOW.search(low) and _MISSION.search(low):
        return {"action": "show", "target": _target(t), "raw": t}
    if _PAUSE.search(low) and (has_ref or short):
        return {"action": "pause", "target": _target(t), "raw": t}
    if _RESUME.search(low) and (has_ref or short):
        return {"action": "resume", "target": _target(t), "raw": t}
    if _CANCEL.search(low) and (has_ref or short):
        return {"action": "cancel", "target": _target(t), "raw": t}
    return None


def _resolve(missions, target: str):
    """Resolve the referenced mission; fall back to the most recent live one."""
    if target:
        m = missions.find_by_text(target)
        if m is not None:
            return m
    live = [m for m in missions.list(50)
            if m.state not in (MissionState.COMPLETED, MissionState.FAILED,
                               MissionState.CANCELLED)]
    return live[0] if live else None


def apply_control(services: Dict[str, Any], text: str) -> Optional[Dict[str, Any]]:
    """Apply a mission-control command. None when the text is not a command."""
    parsed = parse_control(text)
    if not parsed:
        return None

    missions = services.get("missions")
    if missions is None:
        return None
    agents = services.get("agents_service")
    runner = services.get("mission_runner")

    mission = _resolve(missions, parsed.get("target", ""))
    if mission is None:
        return {"handled": True, "ok": False,
                "reply": "I couldn't find a mission to do that with. "
                         "Tell me the mission's name and I'll try again."}

    action = parsed["action"]
    mid = mission.mission_id
    title = (mission.goal or mid)[:80]

    try:
        if action == "pause":
            missions.set_fields(mid, state=MissionState.PAUSED.value)
            if agents is not None:
                try:
                    agents.pause(mid, reason="owner via chat")
                except Exception:
                    pass
            return {"handled": True, "ok": True, "mission_id": mid,
                    "reply": f"Paused “{title}”."}

        if action == "resume":
            missions.set_fields(mid, state=MissionState.RUNNING.value)
            if agents is not None:
                try:
                    agents.resume(mid)
                except Exception:
                    pass
            if runner is not None:
                runner.execute(mid)
            return {"handled": True, "ok": True, "mission_id": mid,
                    "reply": f"Resumed “{title}”."}

        if action == "cancel":
            try:
                missions.cancel(__import__("core.contracts", fromlist=["CallContext"])
                                .CallContext(), mid, "owner via chat")
            except Exception:
                missions.set_fields(mid, state=MissionState.CANCELLED.value)
            missions.clear_schedule(mid)
            if agents is not None:
                try:
                    agents.cancel(mid, reason="owner via chat")
                except Exception:
                    pass
            return {"handled": True, "ok": True, "mission_id": mid,
                    "reply": f"Stopped “{title}”."}

        if action == "reschedule":
            from missions.scheduler import next_run
            spec = parsed["spec"]
            nxt = next_run(spec, now_ms())
            missions.set_schedule(mid, spec, nxt)
            missions.set_fields(mid, schedule=str(spec.get("raw", ""))[:64],
                                state=MissionState.WAITING.value)
            return {"handled": True, "ok": True, "mission_id": mid,
                    "reply": f"Updated “{title}” — it will now run "
                             f"{spec.get('raw', spec.get('kind'))}."}

        if action == "show":
            prog = missions.progress(mid)
            steps = missions.steps(mid)
            current = prog.get("current") or "waiting"
            done = prog.get("done", 0)
            total = prog.get("total", 0)
            sched = missions.schedule_of(mid)
            nxt = ""
            if sched and sched.get("next_run_ms"):
                import datetime as _d
                nxt = " Next run: " + _d.datetime.fromtimestamp(
                    sched["next_run_ms"] / 1000).strftime("%Y-%m-%d %H:%M") + "."
            return {"handled": True, "ok": True, "mission_id": mid,
                    "reply": f"“{title}” is {mission.state.value} — "
                             f"{done}/{total} steps done. Now: {current}.{nxt}"}

    except Exception as exc:  # noqa: BLE001
        log.warning("mission control %s failed: %s", action, exc)
        return {"handled": True, "ok": False,
                "reply": f"I couldn't {action} “{title}” ({exc})."}

    return None


__all__ = ["parse_control", "apply_control"]
