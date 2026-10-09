"""Structured logging + trace plumbing (core/logging_setup).

Every log line carries trace_id when a context is bound, so one mission can be
replayed end-to-end from the log file alone.
"""
from __future__ import annotations

import json
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

_CTX = threading.local()
_CONFIGURED = False

_LEVELS = {
    "DEBUG": logging.DEBUG, "INFO": logging.INFO,
    "WARN": logging.WARNING, "ERROR": logging.ERROR,
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        trace = getattr(_CTX, "trace_id", None)
        if trace:
            payload["trace_id"] = trace
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("genie")
    root.setLevel(_LEVELS.get(level.upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    fmt = JsonFormatter()
    # logs go to stderr so CLI commands can emit clean JSON on stdout
    import sys as _sys
    console = logging.StreamHandler(_sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"genie.{name}")


def bind_trace(trace_id: str | None) -> None:
    _CTX.trace_id = trace_id


def current_trace() -> Optional[str]:
    return getattr(_CTX, "trace_id", None)
