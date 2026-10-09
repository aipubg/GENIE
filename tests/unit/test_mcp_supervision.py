"""MCP connection supervision (re-audit 14.5, donor: Strix).

The behaviour that matters: a failing server is quarantined instead of hammered, and a
quarantined/dead session **refuses** calls rather than pretending they ran.
"""
from __future__ import annotations

import time

from integrations.mcp_supervision import (DEAD, DEGRADED, LIVE, QUARANTINED,
                                          SupervisedMcpSession)


def _ok_transport(*_a, **_k):
    return {"ok": True, "result": "done"}


# --------------------------------------------------------------------- basics
def test_a_successful_call_marks_the_session_live():
    session = SupervisedMcpSession("srv", transport=_ok_transport)
    out = session.call("tools/list")
    assert out["ok"] is True
    assert session.state == LIVE
    assert session.status()["usable"] is True


def test_without_a_transport_the_call_is_refused_not_faked():
    session = SupervisedMcpSession("srv")          # no transport
    out = session.call("tools/call", {"name": "x"})
    assert out["ok"] is False
    assert "no MCP transport" in out["error"]
    assert out.get("result") is None, "must not invent a result"


# ------------------------------------------------------------ failure ladder
def test_failures_escalate_degraded_then_quarantined():
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "boom"},
                                   failure_threshold=2, dead_after=99)
    session.call("a")
    assert session.state == DEGRADED, "one failure is tolerated"
    session.call("b")
    assert session.state == QUARANTINED, "repeated failure opens a quarantine"


def test_a_quarantined_session_refuses_without_calling_the_transport():
    """This is what prevents retry storms."""
    attempts = {"n": 0}

    def counting(_m, _p):
        attempts["n"] += 1
        return {"ok": False, "error": "down"}

    session = SupervisedMcpSession("srv", transport=counting, failure_threshold=1,
                                   dead_after=99, retry_delay_s=60)
    session.call("a")                     # -> quarantined
    assert attempts["n"] == 1
    out = session.call("b")
    assert attempts["n"] == 1, "the transport must not be touched while quarantined"
    assert out["ok"] is False
    assert out["status"] == QUARANTINED


def test_repeated_failed_reconnects_kill_the_session():
    """DEAD is reached through exhausted recovery, not through calls (quarantine blocks those)."""
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "down"},
                                   failure_threshold=1, dead_after=3,
                                   retry_delay_s=0, connect=lambda: False)
    session.call("x")                       # -> quarantined
    assert session.state == QUARANTINED
    for _ in range(3):
        session.reconnect()                 # every recovery attempt fails
    assert session.state == DEAD
    assert session.status()["usable"] is False


def test_pending_calls_are_dropped_when_the_session_dies():
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "down"},
                                   failure_threshold=1, dead_after=1,
                                   retry_delay_s=0, connect=lambda: False)
    session.call("x")
    session._pending["stuck"] = {"method": "x", "started_ms": 0}
    assert session.status()["pending"] == 1
    session.reconnect()                     # kills it
    assert session.state == DEAD
    assert session.status()["pending"] == 0, "in-flight calls must not be left hanging"


# ----------------------------------------------------------------- reconnect
def test_reconnect_respects_the_cooldown():
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "down"},
                                   failure_threshold=1, retry_delay_s=30)
    session.call("a")
    out = session.reconnect()
    assert out["ok"] is False
    assert "cooldown" in out["error"]
    assert out["retry_after_s"] > 0


def test_reconnect_recovers_after_the_cooldown():
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "down"},
                                   failure_threshold=1, retry_delay_s=0.05,
                                   connect=lambda: True)
    session.call("a")
    assert session.state == QUARANTINED
    time.sleep(0.08)
    out = session.reconnect()
    assert out["ok"] is True
    assert session.state == LIVE, "a recovered session is usable again"


def test_a_dead_session_refuses_further_recovery_attempts():
    session = SupervisedMcpSession("srv",
                                   transport=lambda m, p: {"ok": False, "error": "down"},
                                   failure_threshold=1, dead_after=1,
                                   retry_delay_s=0, connect=lambda: False)
    session.call("a")
    session.reconnect()                   # -> dead
    assert session.state == DEAD
    out = session.reconnect()
    assert out["ok"] is False
    assert "beyond recovery" in out["error"]


# -------------------------------------------------------------------- status
def test_status_reports_the_full_picture():
    session = SupervisedMcpSession("srv", transport=_ok_transport)
    session.call("a")
    status = session.status()
    assert status["name"] == "srv"
    assert status["state"] == LIVE
    assert status["calls"] == 1
    assert status["last_success_ms"] > 0
    assert status["usable"] is True


def test_history_records_both_successes_and_failures():
    session = SupervisedMcpSession("srv", transport=_ok_transport, failure_threshold=9)
    session.call("ok")
    session.transport = lambda m, p: {"ok": False, "error": "later failure"}
    session.call("bad")
    assert session.history[0]["ok"] is True
    assert session.history[-1]["ok"] is False
