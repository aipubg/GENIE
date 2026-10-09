"""Tests for observability/tracing.py — self-contained OTel-style tracer.

No collector / network required: exercises span-tree linkage, context propagation, the
in-memory exporter, status mapping, and OTLP-JSON serialization.
"""
from __future__ import annotations

import time

from observability.tracing import (
    SPAN_KIND_INTERNAL, SPAN_KIND_CLIENT, STATUS_OK, STATUS_ERROR, STATUS_UNSET,
    InMemoryExporter, Span, Tracer, TracerProvider, get_tracer, get_provider,
    to_otel_json,
)


def _timed(provider, name, ms=1):
    """Start+end a span sleeping ~ms so duration is measurable."""
    tracer = provider.get_tracer("t")
    with tracer.span(name) as s:
        time.sleep(ms / 1000.0)
    return s


# --------------------------------------------------------------------------- #
# Identity / ids
# --------------------------------------------------------------------------- #
def test_span_ids_are_unique_and_well_formed():
    p = TracerProvider()
    t = p.get_tracer()
    a = t.start_span("a")
    b = t.start_span("b")
    assert a.span_id != b.span_id
    assert len(a.span_id) == 16       # 8 bytes hex
    assert len(a.trace_id) == 32      # 16 bytes hex


def test_root_span_has_no_parent_and_new_trace():
    p = TracerProvider()
    t = p.get_tracer()
    s = t.start_span("root")
    assert s.parent_id is None
    assert s.trace_id


# --------------------------------------------------------------------------- #
# Nesting / context propagation
# --------------------------------------------------------------------------- #
def test_nested_spans_share_trace_and_link_parent():
    p = TracerProvider()
    t = p.get_tracer()
    with t.span("parent") as parent:
        assert t.get_current_span() is parent
        with t.span("child") as child:
            assert t.get_current_span() is child
            assert child.parent_id == parent.span_id
            assert child.trace_id == parent.trace_id
        # after child exits, parent is active again
        assert t.get_current_span() is parent
    assert t.get_current_span() is None


def test_function_called_inside_span_sees_active_span():
    p = TracerProvider()
    t = p.get_tracer()

    def inner():
        return t.get_current_span()

    with t.span("outer") as outer:
        seen = inner()
        assert seen is outer


def test_explicit_parent_overrides_context():
    p = TracerProvider()
    t = p.get_tracer()
    a = t.start_span("a")
    b = t.start_span("b", parent=a)
    assert b.parent_id == a.span_id
    assert b.trace_id == a.trace_id


def test_independent_traces_have_different_trace_ids():
    p = TracerProvider()
    t = p.get_tracer()
    a = t.start_span("a")
    b = t.start_span("b")
    assert a.trace_id != b.trace_id


# --------------------------------------------------------------------------- #
# Attributes / events / status
# --------------------------------------------------------------------------- #
def test_attributes_and_events_recorded():
    p = TracerProvider()
    t = p.get_tracer()
    with t.span("op", attributes={"k": "v", "n": 3}) as s:
        s.add_event("checkpoint", {"step": 1})
    assert s.attributes["k"] == "v"
    assert s.attributes["n"] == "3"          # non-str coerced to string
    assert s.events[0].name == "checkpoint"
    assert s.events[0].attributes["step"] == "1"


def test_exception_sets_error_status():
    p = TracerProvider()
    t = p.get_tracer()
    with t.span("risky") as s:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            s.record_exception(exc)
    assert s.status == STATUS_ERROR
    assert "ValueError" in s.status_message
    assert s.events[-1].name == "exception"


def test_explicit_ok_status():
    p = TracerProvider()
    t = p.get_tracer()
    s = t.start_span("ok").set_status(STATUS_OK, "done")
    assert s.status == STATUS_OK


def test_duration_is_measured():
    p = TracerProvider()
    s = _timed(p, "sleepy", ms=5)
    assert s.finished
    assert s.duration_ms >= 1.0     # slept ~5ms; allow slack


# --------------------------------------------------------------------------- #
# Exporter
# --------------------------------------------------------------------------- #
def test_in_memory_exporter_receives_only_finished_spans():
    p = TracerProvider()
    exp = InMemoryExporter()
    p.add_exporter(exp)
    t = p.get_tracer()
    finished = t.start_span("done")
    finished.end()
    unfinished = t.start_span("open")          # never ended
    # force an end so export path runs for the finished one
    t._on_end(finished)
    assert len(exp.spans) == 1
    assert exp.spans[0].name == "done"
    assert exp.finished_spans()[0].name == "done"


def test_exporter_collects_on_context_exit():
    p = TracerProvider()
    exp = InMemoryExporter()
    p.add_exporter(exp)
    t = p.get_tracer()
    with t.span("a"):
        with t.span("b"):
            pass
    assert {s.name for s in exp.spans} == {"a", "b"}


def test_provider_add_exporter_reaches_existing_tracers():
    p = TracerProvider()
    t1 = p.get_tracer("one")
    exp = InMemoryExporter()
    p.add_exporter(exp)
    t2 = p.get_tracer("two")
    with t1.span("x"):
        pass
    with t2.span("y"):
        pass
    assert {s.name for s in exp.spans} == {"x", "y"}


def test_exporter_clear():
    p = TracerProvider()
    exp = InMemoryExporter()
    p.add_exporter(exp)
    with p.get_tracer().span("z"):
        pass
    assert exp.spans
    exp.clear()
    assert exp.spans == []


# --------------------------------------------------------------------------- #
# OTLP-JSON serialization
# --------------------------------------------------------------------------- #
def test_to_otel_json_shape_and_status_codes():
    s = Span(name="op", span_id="11", trace_id="22", parent_id="33",
             kind=SPAN_KIND_CLIENT, status=STATUS_ERROR, status_message="bad")
    s.set_attribute("env", "prod").add_event("e")
    s.end()
    j = to_otel_json(s)
    assert j["traceId"] == "22"
    assert j["spanId"] == "11"
    assert j["parentSpanId"] == "33"
    assert j["kind"] == SPAN_KIND_CLIENT
    assert j["status"]["code"] == 2          # ERROR -> 2
    assert j["status"]["message"] == "bad"
    assert j["attributes"][0]["value"]["stringValue"] == "prod"
    assert j["endTimeUnixNano"]             # finished -> has end time


def test_to_otel_json_unset_status_code():
    s = Span(name="op", span_id="1", trace_id="2", parent_id=None)
    s.end()
    assert to_otel_json(s)["status"]["code"] == 0   # UNSET -> 0


def test_to_otel_json_unfinished_has_empty_end():
    s = Span(name="op", span_id="1", trace_id="2", parent_id=None)
    assert to_otel_json(s)["endTimeUnixNano"] == ""


# --------------------------------------------------------------------------- #
# Global provider / OTLP
# --------------------------------------------------------------------------- #
def test_global_get_tracer_returns_provider_tracer():
    assert get_tracer("global-test") is get_provider().get_tracer("global-test")


def test_configure_otlp_attaches_exporter_and_returns_true():
    p = TracerProvider()
    ok = p.configure_otlp("http://127.0.0.1:4318/v1/traces")
    assert ok is True
    # exporter registered; ending a span must not raise (collector absent -> logged, not raised)
    with p.get_tracer().span("emit"):
        pass
