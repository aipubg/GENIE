"""Experience is a real authority now — it should answer useful questions.

A raw dump of trajectories is a debug view, not a product. The digest turns
recorded experience into durable learning: what worked, what failed, what the
verifier taught us, what each agent profile learned, which provider
configuration is reusable, and an outcome summary.

The rule throughout: every number counts real rows. With nothing recorded the
digest is honestly empty; it never invents a lesson to look useful.
"""
from __future__ import annotations

import pytest

from experience.bank import ExperienceBank


@pytest.fixture()
def bank(app):
    return ExperienceBank(app.db)


def _record(bank, **kw):
    defaults = dict(task_type="code.write", steps=[{"n": 1}], success=True,
                    strategy="plan-then-write", provider="deepseek",
                    role="worker", mission_id="m1")
    defaults.update(kw)
    bank.record(**defaults)


def test_digest_is_honestly_empty_with_nothing_recorded(bank):
    d = bank.digest()
    assert d["count"] == 0
    assert d["successful_strategies"] == []
    assert d["failed_approaches"] == []
    assert d["verifier_lessons"] == []
    assert d["outcome_summary"]["success_rate"] is None


def test_digest_separates_what_worked_from_what_failed(bank):
    _record(bank, success=True, strategy="plan-then-write")
    _record(bank, success=True, strategy="plan-then-write")
    _record(bank, success=False, strategy="write-immediately",
            failures=["verifier rejected: missing tests"])

    d = bank.digest()
    assert d["count"] == 3
    assert ("plan-then-write", 2) in d["successful_strategies"]
    assert ("write-immediately", 1) in d["failed_approaches"]
    assert d["outcome_summary"]["success"] == 2
    assert d["outcome_summary"]["failure"] == 1
    assert d["outcome_summary"]["success_rate"] == pytest.approx(2 / 3, abs=0.01)


def test_verifier_lessons_carry_the_actual_failure_reasons(bank):
    _record(bank, success=False, strategy="skip-tests",
            failures=["verifier rejected: no tests", "verifier rejected: lint"])
    d = bank.digest()
    assert d["verifier_lessons"][0]["strategy"] == "skip-tests"
    assert "verifier rejected: no tests" in d["verifier_lessons"][0]["failures"]


def test_digest_filters_by_mission_capability_agent_and_provider(bank):
    _record(bank, mission_id="m1", task_type="code.write", role="worker",
            provider="deepseek")
    _record(bank, mission_id="m2", task_type="research", role="critic",
            provider="openai")

    assert bank.digest(mission_id="m1")["count"] == 1
    assert bank.digest(capability="research")["count"] == 1
    assert bank.digest(agent="critic")["count"] == 1
    assert bank.digest(provider="openai")["count"] == 1
    # Combining filters narrows rather than widens.
    assert bank.digest(mission_id="m1", capability="research")["count"] == 0


def test_outcome_filter_rejects_unknown_values_instead_of_widening(bank):
    _record(bank, success=True)
    _record(bank, success=False)

    assert len(bank.query(outcome="success")) == 1
    assert len(bank.query(outcome="failure")) == 1
    # An unrecognised outcome must not silently become "everything".
    assert bank.query(outcome="sort-of") == []


def test_digest_tracks_learning_per_capability(bank):
    _record(bank, task_type="code.write", success=True)
    _record(bank, task_type="code.write", success=False)
    _record(bank, task_type="research", success=True)

    d = bank.digest()
    assert d["by_capability"]["code.write"] == {"total": 2, "success": 1,
                                                "failure": 1}
    assert d["by_capability"]["research"]["success"] == 1


def test_agent_profile_and_reusable_configuration_are_counted(bank):
    _record(bank, role="worker", provider="deepseek")
    _record(bank, role="worker", provider="deepseek")
    _record(bank, role="critic", provider="openai")

    d = bank.digest()
    assert ("worker", 2) in d["agent_profile_learning"]
    assert ("deepseek", 2) in d["reusable_configuration"]
