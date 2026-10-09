"""P5 OpenClaw gap: channel ingress + session continuity.

Deterministic, in-memory SQLite — no network, no provider, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from channels.service import ChannelAdapter, ChannelService, InboundMessage  # noqa: E402
from channels.sessions import SessionStore                                   # noqa: E402
from core.db import Database                                                 # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def svc(db):
    return ChannelService(db=db, sessions=SessionStore(db))


class EchoAdapter(ChannelAdapter):
    """Records what was sent so delivery can be asserted."""

    name = "echo"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.pending: list[InboundMessage] = []

    def send(self, external_key, text):
        self.sent.append((external_key, text))
        return True

    def poll(self):
        out, self.pending = self.pending, []
        return out


# ---------------------------------------------------------------- sessions
def test_session_created_then_resumed(db):
    store = SessionStore(db)
    a = store.get_or_create("echo", "user-1")
    store.append(a["session_id"], "user", "hello")
    b = store.get_or_create("echo", "user-1")
    assert b["session_id"] == a["session_id"], \
        "the same identity in the same channel must resume the same session"
    assert b["message_count"] == 1


def test_different_identities_get_different_sessions(db):
    store = SessionStore(db)
    a = store.get_or_create("echo", "user-1")
    b = store.get_or_create("echo", "user-2")
    assert a["session_id"] != b["session_id"]


def test_history_persists_and_is_ordered(db):
    store = SessionStore(db)
    s = store.get_or_create("echo", "user-1")
    for i in range(5):
        store.append(s["session_id"], "user" if i % 2 == 0 else "genie", f"m{i}")
    hist = store.history(s["session_id"])
    assert [h["text"] for h in hist] == ["m0", "m1", "m2", "m3", "m4"]
    assert [h["role"] for h in hist] == ["user", "genie", "user", "genie", "user"]


def test_continuity_survives_a_new_store_instance(db):
    """A restart must not lose the conversation."""
    s1 = SessionStore(db).get_or_create("echo", "user-9")
    SessionStore(db).append(s1["session_id"], "user", "remember me")
    s2 = SessionStore(db).get_by_key("echo", "user-9")
    assert s2 is not None
    assert "remember me" in [h["text"] for h in SessionStore(db).history(s2["session_id"])]


# ----------------------------------------------------------------- ingress
def test_ingest_persists_user_message(svc):
    out = svc.ingest(InboundMessage(channel="ui", external_key="u1", text="hi"))
    assert out["ok"] is True
    assert out["resumed"] is False
    assert [h["text"] for h in svc.context(out["session_id"])] == ["hi"]


def test_second_message_reports_resumed(svc):
    first = svc.ingest(InboundMessage(channel="ui", external_key="u1", text="hi"))
    second = svc.ingest(InboundMessage(channel="ui", external_key="u1", text="again"))
    assert second["session_id"] == first["session_id"]
    assert second["resumed"] is True


def test_unknown_channel_is_rejected_not_trusted(svc):
    out = svc.ingest(InboundMessage(channel="telegram", external_key="u1", text="hi"))
    assert out["ok"] is False
    assert "unknown channel" in out["error"]


def test_invalid_messages_are_rejected(svc):
    assert svc.ingest(InboundMessage(channel="ui", external_key="", text="hi"))["ok"] is False
    assert svc.ingest(InboundMessage(channel="ui", external_key="u", text="  "))["ok"] is False


def test_registered_channel_is_accepted_and_delivers(svc):
    a = EchoAdapter()
    svc.register(a)
    out = svc.ingest(InboundMessage(channel="echo", external_key="u1", text="hi"))
    assert out["ok"] is True
    rep = svc.reply(out["session_id"], "hello there")
    assert rep["delivered"] is True
    assert ("u1", "hello there") in a.sent


def test_reply_is_recorded_in_history(svc):
    out = svc.ingest(InboundMessage(channel="ui", external_key="u1", text="q"))
    svc.reply(out["session_id"], "a")
    roles = [h["role"] for h in svc.context(out["session_id"])]
    assert roles == ["user", "genie"]


def test_handler_receives_prior_context(svc):
    """Continuity means the handler sees earlier turns."""
    seen = {}

    def handler(text, ctx):
        seen["history"] = ctx.get("history")
        seen["channel"] = ctx.get("channel")
        return {"reply": "ok"}

    svc._handler = handler
    svc.ingest(InboundMessage(channel="ui", external_key="u1", text="first"))
    svc.ingest(InboundMessage(channel="ui", external_key="u1", text="second"))
    texts = [h["text"] for h in seen["history"]]
    assert "first" in texts, "the handler must see earlier turns (continuity)"
    assert seen["channel"] == "ui"


def test_pull_based_adapter_is_polled(svc):
    a = EchoAdapter()
    a.pending = [InboundMessage(channel="echo", external_key="u2", text="ping")]
    svc.register(a)
    results = svc.poll_all()
    assert results and results[0]["ok"] is True
    assert "ping" in [h["text"] for h in svc.sessions.history(results[0]["session_id"])]


def test_adapter_must_have_a_name(svc):
    class Nameless(EchoAdapter):
        name = ""
    with pytest.raises(ValueError):
        svc.register(Nameless())


def test_sessions_can_be_listed(svc):
    svc.ingest(InboundMessage(channel="ui", external_key="u1", text="a"))
    svc.ingest(InboundMessage(channel="voice", external_key="u1", text="b"))
    assert len(svc.sessions.list_sessions()) == 2
    assert len(svc.sessions.list_sessions(channel="ui")) == 1
