"""Windows event sources (computer/events).

Polling-based watchers that turn machine state changes into bus events:

    APP_OPENED / APP_CLOSED / WINDOW_FOCUSED / DEVICE_* / USER_TAKEOVER / FILE_CHANGED

Assumption A-030: v1 uses polling rather than SetWinEventHook / ReadDirectoryChangesW.
Polling is simpler, survives sleep/resume and cannot deadlock the daemon; the interval
adapts to the machine profile (low-end machines poll less). Hook-based sources can replace
this later behind the same event contract.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.contracts import EventType
from core.events import get_bus
from core.logging_setup import get_logger

from . import state, windows_api as win
from .input import DETECTOR

log = get_logger("computer.events")


@dataclass
class WatcherConfig:
    interval_s: float = 2.0
    watch_foreground: bool = True
    watch_processes: bool = True
    watch_workspace: bool = True
    workspace_root: Optional[str] = None
    max_events_per_tick: int = 20


class WindowsEventWatcher:
    def __init__(self, db=None, config: Optional[WatcherConfig] = None):
        self.db = db
        self.cfg = config or WatcherConfig()
        self._bus = get_bus()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._known_processes: Set[str] = set()
        self._foreground: str = ""
        self._file_snapshot: Dict[str, int] = {}
        self._takeover_seen: int = 0
        self.stats = {"ticks": 0, "events": 0, "errors": 0, "last_tick": 0.0}

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._known_processes = {p["name"].lower() for p in state.list_processes()}
        self._foreground = state.foreground_title()
        self._thread = threading.Thread(target=self._run, name="win-events", daemon=True)
        self._thread.start()
        log.info("windows event watcher started (interval=%.1fs)", self.cfg.interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # --------------------------------------------------------------------- loop
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.stats["errors"] += 1
                log.debug("watcher tick failed: %s", exc)
            self._stop.wait(self.cfg.interval_s)

    def tick(self) -> List[Dict[str, Any]]:
        emitted: List[Dict[str, Any]] = []
        self.stats["ticks"] += 1
        self.stats["last_tick"] = time.time()

        # 1. user takeover
        takeover = DETECTOR.check()
        if takeover:
            self._emit(EventType.USER_TAKEOVER, takeover.to_dict(), emitted)

        # 2. foreground window changes
        if self.cfg.watch_foreground:
            title = state.foreground_title()
            if title and title != self._foreground:
                self._foreground = title
                self._emit("WINDOW_FOCUSED", {"title": title}, emitted)

        # 3. process start/stop
        if self.cfg.watch_processes:
            current = {p["name"].lower() for p in state.list_processes()}
            started = current - self._known_processes
            stopped = self._known_processes - current
            for name in list(started)[: self.cfg.max_events_per_tick]:
                self._emit(EventType.APP_OPENED, {"process": name}, emitted)
            for name in list(stopped)[: self.cfg.max_events_per_tick]:
                self._emit("APP_CLOSED", {"process": name}, emitted)
            self._known_processes = current

        # 4. workspace file changes
        if self.cfg.watch_workspace and self.cfg.workspace_root:
            self._scan_workspace(emitted)

        return emitted

    def _scan_workspace(self, emitted: List[Dict[str, Any]]) -> None:
        root = Path(self.cfg.workspace_root)
        if not root.exists():
            return
        snapshot: Dict[str, int] = {}
        count = 0
        for p in root.rglob("*"):
            if count > 2000:
                break
            count += 1
            try:
                if p.is_file():
                    snapshot[str(p)] = p.stat().st_mtime_ns
            except OSError:
                continue
        for path, mtime in snapshot.items():
            if path not in self._file_snapshot:
                self._emit(EventType.FILE_CHANGED, {"path": path, "change": "created"}, emitted)
            elif self._file_snapshot[path] != mtime:
                self._emit(EventType.FILE_CHANGED, {"path": path, "change": "modified"}, emitted)
        for path in self._file_snapshot:
            if path not in snapshot:
                self._emit(EventType.FILE_CHANGED, {"path": path, "change": "deleted"}, emitted)
        self._file_snapshot = snapshot

    def _emit(self, event_type: str, payload: Dict[str, Any],
              sink: List[Dict[str, Any]]) -> None:
        self.stats["events"] += 1
        sink.append({"type": event_type, "payload": payload})
        try:
            self._bus.publish(event_type, payload)
        except Exception:
            pass

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        return {"running": self.running(), "interval_s": self.cfg.interval_s,
                "stats": dict(self.stats),
                "tracked_processes": len(self._known_processes),
                "tracked_files": len(self._file_snapshot)}
