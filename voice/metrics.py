"""Voice latency metrics (voice/metrics).

Every stage of the voice pipeline is measured separately, because "voice feels slow" is
useless without knowing *which* stage is slow:

    mic -> speech detected          (VAD latency)
    speech end -> transcript        (STT latency)
    transcript -> director decision (routing latency)
    decision -> first audio out     (TTS first-audio latency)
    speech start -> response done   (full perceived latency)
    barge-in detected -> audio stopped
"""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("voice.metrics")

STAGES = ("vad_ms", "stt_ms", "director_ms", "action_ms", "tts_first_audio_ms",
          "tts_ms", "total_ms", "barge_in_stop_ms")


@dataclass
class TurnMetrics:
    turn_id: str
    started_at: float = field(default_factory=time.time)
    stages: Dict[str, int] = field(default_factory=dict)
    transcript: str = ""
    reply: str = ""
    interrupted: bool = False
    provider_stt: str = ""
    provider_tts: str = ""

    def mark(self, stage: str, ms: int) -> None:
        self.stages[stage] = int(ms)

    def finish(self) -> Dict[str, Any]:
        self.stages["total_ms"] = int((time.time() - self.started_at) * 1000)
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {"turn_id": self.turn_id, "stages": dict(self.stages),
                "transcript": self.transcript[:200], "reply": self.reply[:200],
                "interrupted": self.interrupted,
                "stt_provider": self.provider_stt, "tts_provider": self.provider_tts,
                "ts": int(self.started_at)}


class VoiceMetrics:
    def __init__(self, db=None, ring_size: int = 200):
        self.db = db
        self._ring: List[Dict[str, Any]] = []
        self._ring_size = ring_size
        self._lock = threading.Lock()
        self._current: Optional[TurnMetrics] = None

    # ------------------------------------------------------------------- turns
    def begin_turn(self, turn_id: str) -> TurnMetrics:
        with self._lock:
            self._current = TurnMetrics(turn_id=turn_id)
            return self._current

    def current(self) -> Optional[TurnMetrics]:
        return self._current

    def finish_turn(self) -> Dict[str, Any]:
        with self._lock:
            if self._current is None:
                return {}
            record = self._current.finish()
            self._current = None
            self._ring.append(record)
            if len(self._ring) > self._ring_size:
                self._ring.pop(0)
        self._persist(record)
        log.info("voice turn %s: %s", record["turn_id"], record["stages"])
        return record

    # ------------------------------------------------------------------ stages
    def mark(self, stage: str, ms: int) -> None:
        if self._current is not None:
            self._current.mark(stage, ms)

    # ----------------------------------------------------------------- persist
    def _persist(self, record: Dict[str, Any]) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "INSERT INTO voice_metrics(turn_id, ts, vad_ms, stt_ms, director_ms,"
                " action_ms, tts_first_audio_ms, tts_ms, total_ms, barge_in_stop_ms,"
                " interrupted, stt_provider, tts_provider, transcript, reply)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record["turn_id"], record["ts"],
                 record["stages"].get("vad_ms", 0), record["stages"].get("stt_ms", 0),
                 record["stages"].get("director_ms", 0), record["stages"].get("action_ms", 0),
                 record["stages"].get("tts_first_audio_ms", 0),
                 record["stages"].get("tts_ms", 0), record["stages"].get("total_ms", 0),
                 record["stages"].get("barge_in_stop_ms", 0),
                 1 if record["interrupted"] else 0, record["stt_provider"],
                 record["tts_provider"], record["transcript"], record["reply"]))
        except Exception as exc:
            log.debug("voice metrics persist failed: %s", exc)

    # ------------------------------------------------------------------- stats
    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._ring[-limit:])

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            records = list(self._ring)
        if not records:
            return {"turns": 0}
        out: Dict[str, Any] = {"turns": len(records)}
        for stage in STAGES:
            values = [r["stages"].get(stage, 0) for r in records if r["stages"].get(stage)]
            if values:
                out[stage] = {"avg": round(statistics.mean(values), 1),
                              "p50": round(statistics.median(values), 1),
                              "max": max(values)}
        out["interruptions"] = sum(1 for r in records if r["interrupted"])
        return out

    def db_summary(self, limit: int = 100) -> Dict[str, Any]:
        if self.db is None:
            return {}
        try:
            rows = self.db.query(
                "SELECT * FROM voice_metrics ORDER BY id DESC LIMIT ?", (limit,))
            return {"rows": len(rows),
                    "avg_total_ms": round(statistics.mean([r["total_ms"] for r in rows]), 1)
                    if rows else 0,
                    "avg_stt_ms": round(statistics.mean([r["stt_ms"] for r in rows]), 1)
                    if rows else 0}
        except Exception:
            return {}
