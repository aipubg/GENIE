"""Self-contained OpenTelemetry-style tracing (`observability/tracing.py`).

No third-party dependency is required for the core. GENIE can emit traces today — span tree,
context propagation, in-memory export for tests/debug — and ship them to any OTLP/HTTP collector
via `configure_otlp()` using only the standard library (OTLP/HTTP **JSON**).

Why self-contained (not a thin wrapper over the OTel SDK): the SDK is a heavy, optional dependency.
The re-audit deferred "OTel tracing" as infrastructure; this gives the project a real, testable
tracer now, with an OTel-compatible wire format (`to_otel_json`) so a collector can ingest it
later without rewriting instrumentation.

Wire-format note: `configure_otlp()` POSTs OTLP/HTTP **JSON** to `:4318/v1/traces`. The OpenTelemetry
Collector's `otlphttp` receiver accepts both protobuf and JSON; if you need strict protobuf, install
`opentelemetry-sdk` and swap in its exporter. The span *model* here is OTel-shaped either way.
"""
from __future__ import annotations

import contextvars
import json
import time
import uuid
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:  # keep usable without the app logging stack
    from core.logging_setup import get_logger
    log = get_logger("observability.tracing")
except Exception:  # pragma: no cover - only when run outside the app
    import logging
    log = logging.getLogger("observability.tracing")

# --- OTel span kinds ------------------------------------------------------- #
SPAN_KIND_INTERNAL = 0
SPAN_KIND_SERVER = 1
SPAN_KIND_CLIENT = 2
SPAN_KIND_PRODUCER = 3
SPAN_KIND_CONSUMER = 4

# --- OTel status codes ----------------------------------------------------- #
STATUS_UNSET = "UNSET"
STATUS_OK = "OK"
STATUS_ERROR = "ERROR"
_OTEL_STATUS_CODE = {STATUS_UNSET: 0, STATUS_OK: 1, STATUS_ERROR: 2}

_ACTIVE_SPAN: contextvars.ContextVar["Span"] = contextvars.ContextVar(
    "genie_active_span", default=None)


def _new_id(n_bytes: int) -> str:
    return uuid.uuid4().hex[: n_bytes * 2]


def _stringify(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


# --------------------------------------------------------------------------- #
# Span
# --------------------------------------------------------------------------- #
@dataclass
class SpanEvent:
    name: str
    timestamp_ns: int
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class Span:
    """One operation's span. OTel-shaped: trace/span/parent ids, timing, attrs, events."""

    name: str
    span_id: str
    trace_id: str
    parent_id: Optional[str]
    kind: int = SPAN_KIND_INTERNAL
    start_ns: int = field(default_factory=lambda: time.perf_counter_ns())
    end_ns: int = 0
    attributes: Dict[str, str] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)
    status: str = STATUS_UNSET
    status_message: str = ""

    # --- mutators (chainable) ---
    def set_attribute(self, key: str, value) -> "Span":
        self.attributes[key] = _stringify(value)
        return self

    def add_event(self, name: str, attributes: Optional[Dict] = None) -> "Span":
        self.events.append(SpanEvent(
            name=name, timestamp_ns=time.perf_counter_ns(),
            attributes={k: _stringify(v) for k, v in (attributes or {}).items()}))
        return self

    def set_status(self, status: str, message: str = "") -> "Span":
        self.status = status
        self.status_message = message
        return self

    def record_exception(self, exc: BaseException) -> "Span":
        self.set_status(STATUS_ERROR, f"{type(exc).__name__}: {exc}")
        self.add_event("exception", {
            "exception.type": type(exc).__name__,
            "exception.message": str(exc),
        })
        return self

    def end(self) -> "Span":
        if self.end_ns == 0:
            self.end_ns = time.perf_counter_ns()
        return self

    # --- views ---
    @property
    def finished(self) -> bool:
        return self.end_ns != 0

    @property
    def duration_ms(self) -> float:
        if self.end_ns == 0:
            return 0.0
        return (self.end_ns - self.start_ns) / 1_000_000.0

    def as_dict(self) -> Dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ms": round(self.duration_ms, 4),
            "attributes": dict(self.attributes),
            "events": [{"name": e.name, "timestamp_ns": e.timestamp_ns,
                        "attributes": dict(e.attributes)} for e in self.events],
            "status": self.status,
            "status_message": self.status_message,
        }


# --------------------------------------------------------------------------- #
# Exporters
# --------------------------------------------------------------------------- #
class SpanExporter:
    def export(self, spans: List[Span]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class InMemoryExporter(SpanExporter):
    """Collects spans in-process. For tests and debug; never used in production paths."""

    def __init__(self) -> None:
        self._spans: List[Span] = []

    def export(self, spans: List[Span]) -> None:
        self._spans.extend(spans)

    @property
    def spans(self) -> List[Span]:
        return list(self._spans)

    def finished_spans(self) -> List[Span]:
        return [s for s in self._spans if s.finished]

    def clear(self) -> None:
        self._spans.clear()


class _OTLPExporter(SpanExporter):
    """POSTs spans as OTLP/HTTP JSON to a collector. Standard library only."""

    def __init__(self, endpoint: Optional[str] = None) -> None:
        # OTLP/HTTP JSON default endpoint per the spec.
        self._endpoint = endpoint or "http://localhost:4318/v1/traces"

    def export(self, spans: List[Span]) -> None:
        if not spans:
            return
        payload = {"resourceSpans": [
            {"scopeSpans": [{"spans": [to_otel_json(s) for s in spans]}]}
        ]}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint, data=data,
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=2)
        except Exception as exc:  # network/collector down -> log, never raise into tracing
            log.warning("otlp export failed (%s): %s", self._endpoint, exc)


# --------------------------------------------------------------------------- #
# Tracer
# --------------------------------------------------------------------------- #
class _SpanCtx:
    """Context manager returned by :meth:`Tracer.span`."""

    def __init__(self, tracer: "Tracer", name: str, kind: int,
                 attributes: Optional[Dict], parent: Optional[Span]) -> None:
        self._tracer = tracer
        self._name = name
        self._kind = kind
        self._attributes = attributes
        self._parent = parent
        self._span: Optional[Span] = None
        self._token = None

    def __enter__(self) -> Span:
        self._span = self._tracer.start_span(
            self._name, kind=self._kind,
            attributes=self._attributes, parent=self._parent)
        self._token = _ACTIVE_SPAN.set(self._span)
        return self._span

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._span is not None
        if exc is not None:
            self._span.record_exception(exc)
        self._span.end()
        _ACTIVE_SPAN.reset(self._token)
        self._tracer._on_end(self._span)


class Tracer:
    def __init__(self, name: str, exporters: Optional[List[SpanExporter]] = None) -> None:
        self.name = name
        self._exporters: List[SpanExporter] = list(exporters or [])

    def add_exporter(self, exporter: SpanExporter) -> "Tracer":
        self._exporters.append(exporter)
        return self

    def start_span(self, name: str, kind: int = SPAN_KIND_INTERNAL,
                   attributes: Optional[Dict] = None,
                   parent: Optional[Span] = None) -> Span:
        """Begin a span. Parent defaults to the currently-active span (context propagation)."""
        parent_span = parent if parent is not None else _ACTIVE_SPAN.get()
        trace_id = parent_span.trace_id if parent_span else _new_id(16)
        return Span(
            name=name,
            span_id=_new_id(8),
            trace_id=trace_id,
            parent_id=parent_span.span_id if parent_span else None,
            kind=kind,
            attributes={k: _stringify(v) for k, v in (attributes or {}).items()},
        )

    def span(self, name: str, kind: int = SPAN_KIND_INTERNAL,
             attributes: Optional[Dict] = None,
             parent: Optional[Span] = None) -> _SpanCtx:
        return _SpanCtx(self, name, kind, attributes, parent)

    def get_current_span(self) -> Optional[Span]:
        return _ACTIVE_SPAN.get()

    def _on_end(self, span: Span) -> None:
        for exporter in self._exporters:
            try:
                exporter.export([span])
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("exporter %r failed: %s", exporter, exc)


# --------------------------------------------------------------------------- #
# Provider (app-wide singleton)
# --------------------------------------------------------------------------- #
class TracerProvider:
    def __init__(self) -> None:
        self._exporters: List[SpanExporter] = []
        self._tracers: Dict[str, Tracer] = {}

    def add_exporter(self, exporter: SpanExporter) -> "TracerProvider":
        self._exporters.append(exporter)
        for tracer in self._tracers.values():
            tracer.add_exporter(exporter)
        return self

    def get_tracer(self, name: str = "genie") -> Tracer:
        if name not in self._tracers:
            tracer = Tracer(name, exporters=list(self._exporters))
            self._tracers[name] = tracer
        return self._tracers[name]

    def configure_otlp(self, endpoint: Optional[str] = None) -> bool:
        """Attach an OTLP/HTTP-JSON exporter. Always succeeds (stdlib only); returns True."""
        self.add_exporter(_OTLPExporter(endpoint))
        log.info("OTLP exporter attached -> %s", endpoint or "http://localhost:4318/v1/traces")
        return True


_DEFAULT_PROVIDER = TracerProvider()


def get_provider() -> TracerProvider:
    return _DEFAULT_PROVIDER


def get_tracer(name: str = "genie") -> Tracer:
    return _DEFAULT_PROVIDER.get_tracer(name)


# --------------------------------------------------------------------------- #
# OTel wire format (OTLP/HTTP JSON)
# --------------------------------------------------------------------------- #
def _kv(key: str, value: str) -> Dict:
    return {"key": key, "value": {"stringValue": value}}


def to_otel_json(span: Span) -> Dict:
    """Convert a :class:`Span` to an OTLP/HTTP JSON span object."""
    return {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "parentSpanId": span.parent_id or "",
        "name": span.name,
        "kind": span.kind,
        "startTimeUnixNano": str(span.start_ns),
        "endTimeUnixNano": str(span.end_ns) if span.finished else "",
        "attributes": [_kv(k, v) for k, v in span.attributes.items()],
        "events": [
            {"name": e.name, "timeUnixNano": str(e.timestamp_ns),
             "attributes": [_kv(k, v) for k, v in e.attributes.items()]}
            for e in span.events
        ],
        "status": {
            "code": _OTEL_STATUS_CODE.get(span.status, 0),
            "message": span.status_message,
        },
    }
