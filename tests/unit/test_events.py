"""Event bus: delivery, wildcards, DLQ isolation, ring buffer."""
from __future__ import annotations

import threading
import time

from core.events import EventBus


def test_publish_delivers_to_subscriber():
    bus = EventBus(workers=1)
    got = []
    bus.subscribe("MISSION_*", lambda e: got.append(e.type))
    bus.publish("MISSION_CREATED", {"id": 1})
    bus.drain(1.0)
    assert got == ["MISSION_CREATED"]
    bus.shutdown()


def test_wildcard_and_pattern_isolation():
    bus = EventBus(workers=1)
    seen = []
    bus.subscribe("*", lambda e: seen.append(e.type))
    bus.publish("MEMORY_UPDATED")
    bus.drain(1.0)
    assert "MEMORY_UPDATED" in seen
    bus.shutdown()


def test_bad_handler_goes_to_dlq_without_killing_bus():
    bus = EventBus(workers=1)
    ok = []

    def boom(event):
        raise RuntimeError("handler exploded")

    bus.subscribe("BAD_*", boom)
    bus.subscribe("BAD_*", lambda e: ok.append(e.type))
    bus.publish("BAD_THING")
    bus.drain(1.0)
    assert ok == ["BAD_THING"]           # other handlers still ran
    assert len(bus.dlq) == 1             # failure recorded
    bus.shutdown()


def test_recent_ring_buffer_is_bounded():
    bus = EventBus(workers=0, ring_size=3)
    for i in range(10):
        bus.publish("E", {"i": i}, sync=True)
    assert len(bus.recent(50)) == 3
    bus.shutdown()
