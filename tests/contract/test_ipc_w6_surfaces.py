"""W6 canonical surfaces: knowledge, media, competition, experience.

These four were absent from the HTTP API while the product advertised them.
Adding a surface is only worth doing if it is honest, so these tests assert the
honesty contract rather than the shape of a payload:

  * an endpoint with NO authority behind it must say so (available=false + a
    reason) instead of returning an empty list that reads as "nothing here yet";
  * an endpoint with an authority must never synthesise rows;
  * competition must state the winner-only commit policy, because that is the
    safety property the owner is entitled to see.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from core.ipc.server import IPCServer
from experience.bank import ExperienceBank


class _FakeAgents:
    """Stands in for AgentService: the route only needs .competition to exist."""

    def __init__(self):
        self.competition = object()

    def team(self, mission_id):
        return None

    def compete_task(self, mission_id, task_id, *, commit=None, requested=None):
        return {"ok": False, "error": f"no team for mission {mission_id}"}


class W6Daemon:
    def __init__(self, *, with_experience: bool, db, audit):
        self.ready = True
        self.services = {"audit": audit, "db": db}
        if with_experience:
            self.services["experience"] = ExperienceBank(db)
        self.services["agents_service"] = _FakeAgents()

    def status(self):
        return {"ready": True}


@pytest.fixture()
def w6_server(request, app):
    """`app` comes from conftest: a fully wired stack on a throwaway DB."""
    with_exp = getattr(request, "param", True)
    daemon = W6Daemon(with_experience=with_exp, db=app.db, audit=app.audit)
    srv = IPCServer(daemon, host="127.0.0.1", port=8806)
    srv.start(background=True)
    yield srv
    srv.stop()


def _get(path, port=8806):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_knowledge_says_unavailable_when_no_service(w6_server):
    """Knowledge now HAS a real authority (see test_knowledge_media.py).

    What stays pinned here is the fallback: when the service is not registered
    the route must admit it rather than returning an empty list that reads as
    "you have imported nothing".
    """
    code, body = _get("/api/knowledge")
    assert code == 200
    assert body["available"] is False
    assert body["reason"], "a reason is required - 'false' alone is not an answer"
    assert body["sources"] == []
    assert body["source_count"] == 0


def test_media_says_unavailable_when_no_service(w6_server):
    """Same fallback contract for Media (real authority: the artifact registry)."""
    code, body = _get("/api/media")
    assert code == 200
    assert body["available"] is False
    assert body["reason"], "a reason is required"
    assert body["items"] == []


def test_experience_reports_lessons_when_registered(w6_server):
    code, body = _get("/api/experience")
    assert code == 200
    assert body["available"] is True
    assert body["lessons"] == []          # empty is truthful: nothing recorded yet
    assert body["lesson_count"] == 0
    assert body["task_types"] == []


@pytest.mark.parametrize("w6_server", [False], indirect=True)
def test_experience_says_unavailable_when_not_registered(w6_server):
    """An absent authority must not be indistinguishable from an empty bank."""
    code, body = _get("/api/experience")
    assert code == 200
    assert body["available"] is False
    assert "reason" in body and body["reason"]


def test_competition_states_its_safety_policy(w6_server):
    code, body = _get("/api/competition")
    assert code == 200
    assert body["available"] is True
    # The property that makes competition safe: only the winner commits.
    assert body["commit_policy"] == "winner_only"
    assert body["max_candidates"] == 4
    assert "winner" in body["outcomes"]
    assert "needs_revision" in body["outcomes"]
    assert "cancelled" in body["outcomes"]
    assert "no_candidates" in body["outcomes"]


def test_competition_history_comes_from_audit_only(w6_server):
    """History is read from what the engine actually wrote - never synthesised."""
    code, body = _get("/api/competition")
    assert code == 200
    assert isinstance(body["recent"], list)
    # No competitions have run in this throwaway stack.
    assert body["recent"] == []
