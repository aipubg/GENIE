"""Mission scheduler (Pass 3).

A real scheduler, not a natural-language hint. A schedule is NORMALISED into a
durable spec and a concrete `next_run_ms`, both persisted, so it survives app
exit, machine restart and relaunch.

Design:
  * `normalize(text, now_ms)`  -> spec | None
  * `next_run(spec, after_ms)` -> epoch ms of the next occurrence
  * `missed_count(spec, stored_next, now)` -> how many occurrences were skipped
  * `MissionScheduler`         -> a lightweight thread that finds due missions and
    hands them to a runner. Catch-up is explicit: a long outage runs the work
    ONCE, never once-per-missed-occurrence.

The scheduler is NOT the mission authority — it only decides WHEN to ask the
mission runner to make progress. Mission truth stays in MissionService.
"""
from __future__ import annotations

import calendar
import queue
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from core.logging_setup import get_logger

log = get_logger("missions.scheduler")

DEFAULT_TIME = (9, 0)          # 09:00 when a recurrence names no time
_MORNING = (8, 0)
_EVENING = (19, 0)
_NIGHT = (22, 0)

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _tod(hour: int, minute: int) -> tuple:
    return (max(0, min(23, hour)), max(0, min(59, minute)))


def _parse_time(text: str) -> Optional[tuple]:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", text, re.I)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return _tod(hour, minute)


def normalize(text: str, now_ms: int) -> Optional[Dict[str, Any]]:
    """Turn a recurrence phrase into a durable spec. None when not a schedule."""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()

    # every N minutes / hours
    m = re.search(r"every\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|h)\b", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        scale = (3_600_000 if unit.startswith("h") else
                 60_000 if unit.startswith(("minute", "min")) else 1_000)
        interval = n * scale
        return {"kind": "interval", "interval_ms": max(60_000, interval), "raw": t}

    # explicit clock time, e.g. "at 9 am" / "at 17:30"
    tod = None
    if re.search(r"\bat\b", low) or re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", low):
        tod = _parse_time(low)

    # one-off: "once ...", "tomorrow ..."
    if re.search(r"\b(once|one[- ]?time|tomorrow)\b", low):
        if "tomorrow" in low and tod:
            target = _dt(now_ms).replace(hour=tod[0], minute=tod[1], second=0,
                                         microsecond=0) + timedelta(days=1)
            return {"kind": "once", "at_ms": _ms(target), "raw": t}
        return {"kind": "once", "at_ms": now_ms + 3_600_000, "raw": t}

    weekly_day = next((d for name, d in _WEEKDAYS.items()
                       if re.search(rf"\b{name}\b", low)), None)
    if weekly_day is not None and re.search(r"\b(every|each|weekly)\b", low):
        return {"kind": "weekly", "weekday": weekly_day,
                "time_of_day": list(tod or DEFAULT_TIME), "raw": t}

    if re.search(r"\b(every\s+month|monthly)\b", low):
        dom = 1
        return {"kind": "monthly", "day_of_month": dom,
                "time_of_day": list(tod or DEFAULT_TIME), "raw": t}

    if re.search(r"\b(every\s+(day|morning|evening|night)|daily|nightly|roz|"
                 r"har\s+(din|roz|subah))\b", low):
        if tod is None:
            if "morning" in low or "subah" in low:
                tod = _MORNING
            elif "evening" in low:
                tod = _EVENING
            elif "night" in low:
                tod = _NIGHT
            else:
                tod = DEFAULT_TIME
        return {"kind": "daily", "time_of_day": list(tod), "raw": t}

    if re.search(r"\b(every\s+(week|monday|tuesday|wednesday|thursday|friday|"
                 r"saturday|sunday)|weekly)\b", low):
        return {"kind": "weekly", "weekday": weekly_day if weekly_day is not None else 0,
                "time_of_day": list(tod or DEFAULT_TIME), "raw": t}

    # a bare time with no recurrence is treated as a daily time
    if tod is not None:
        return {"kind": "daily", "time_of_day": list(tod), "raw": t}
    return None


def _next_daily(tod, after_ms: int) -> int:
    hh, mm = tod
    d = _dt(after_ms)
    cand = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= d:
        cand += timedelta(days=1)
    return _ms(cand)


def _next_weekly(weekday: int, tod, after_ms: int) -> int:
    hh, mm = tod
    d = _dt(after_ms)
    cand = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
    cand += timedelta(days=(int(weekday) - d.weekday()) % 7)
    if cand <= d:
        cand += timedelta(days=7)
    return _ms(cand)


def _next_monthly(dom: int, tod, after_ms: int) -> int:
    hh, mm = tod
    d = _dt(after_ms)

    def mk(year: int, month: int) -> datetime:
        day = min(int(dom), calendar.monthrange(year, month)[1])
        return datetime(year, month, day, hh, mm)

    cand = mk(d.year, d.month)
    if cand <= d:
        y, mo = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        cand = mk(y, mo)
    return _ms(cand)


def next_run(spec: Dict[str, Any], after_ms: int) -> int:
    """Epoch ms of the next occurrence strictly after `after_ms`. 0 = none."""
    kind = spec.get("kind")
    if kind == "once":
        at = int(spec.get("at_ms") or 0)
        return at if at > after_ms else 0
    if kind == "interval":
        return after_ms + max(60_000, int(spec.get("interval_ms") or 3_600_000))
    tod = tuple(spec.get("time_of_day") or DEFAULT_TIME)
    if kind == "daily":
        return _next_daily(tod, after_ms)
    if kind == "weekly":
        return _next_weekly(int(spec.get("weekday") or 0), tod, after_ms)
    if kind == "monthly":
        return _next_monthly(int(spec.get("day_of_month") or 1), tod, after_ms)
    return 0


def missed_count(spec: Dict[str, Any], stored_next_ms: int, now_ms: int,
                 cap: int = 500) -> int:
    """How many occurrences were skipped while GENIE was down.

    Bounded so a very old schedule cannot spin. Catch-up runs the work once and
    records this number — it never fires once per missed occurrence.
    """
    if spec.get("kind") == "once" or stored_next_ms <= 0:
        return 0
    n = 0
    cur = stored_next_ms
    while cur and cur <= now_ms and n < cap:
        n += 1
        nxt = next_run(spec, cur)
        if nxt <= cur:
            break
        cur = nxt
    return max(0, n - 1)          # the current occurrence is the one we will run


class MissionScheduler:
    """A lightweight scheduler thread over MissionService.

    It only decides WHEN to ask the runner to progress a mission. The runner owns
    execution; MissionService owns mission truth.
    """

    def __init__(self, missions, on_due: Callable[[str, Dict[str, Any]], None],
                 interval_s: float = 20.0):
        self.missions = missions
        self.on_due = on_due
        self.interval_s = max(1.0, float(interval_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="genie-scheduler",
                                        daemon=True)
        self._worker = threading.Thread(target=self._drain, name="genie-mission-worker",
                                        daemon=True)
        self._thread.start()
        self._worker.start()
        log.info("mission scheduler started (interval %.1fs)", self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        for th in (self._thread, self._worker):
            if th is not None:
                th.join(timeout=5)
        self._thread = self._worker = None

    # ---------------------------------------------------------------- tick
    def tick(self, now: Optional[int] = None) -> int:
        """Find due missions and enqueue them. Returns how many were due."""
        from core.contracts import now_ms as _now
        now = now if now is not None else _now()
        due = self.missions.due_missions(now)
        for row in due:
            try:
                spec = row.get("spec")
                if isinstance(spec, str):
                    import json
                    spec = json.loads(spec or "{}")
                missed = missed_count(spec, int(row.get("next_run_ms") or 0), now)
                nxt = next_run(spec, now)
                if nxt <= 0:
                    self.missions.clear_schedule(row["mission_id"])
                else:
                    self.missions.mark_run(row["mission_id"], nxt, missed)
                if missed:
                    log.info("mission %s: %d missed occurrence(s) collapsed to one run",
                             row["mission_id"], missed)
                self._queue.put((row["mission_id"], spec, missed))
            except Exception as exc:  # noqa: BLE001
                log.warning("schedule dispatch failed for %s: %s", row.get("mission_id"), exc)
        return len(due)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler tick failed: %s", exc)
            self._stop.wait(self.interval_s)

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                mission_id, spec, missed = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.on_due(mission_id, {"spec": spec, "missed": missed})
            except Exception as exc:  # noqa: BLE001
                log.warning("mission run failed for %s: %s", mission_id, exc)
            finally:
                self._queue.task_done()


__all__ = ["normalize", "next_run", "missed_count", "MissionScheduler"]
