"""Phase 6 exit gate — sync v1 (golden task #17) and phone control (golden task #8).

Golden #17: *"Phone + PC offline edits → conflict resolved per policy, UI shown."*
Golden #8:  *"Phone mein agla song → Device command acked + verified."*

`tests/e2e/test_devices_phase6.py` covers #8 with a real node over real TCP. This file covers the
sync half, which is what makes offline editing on two devices converge.
"""
from __future__ import annotations

import pytest

from core.contracts import CallContext
from devices.sync import DEFAULT_POLICY, SyncPolicy, merge_values


@pytest.fixture()
def sync(app):
    return app.devices.sync


def change(key: str, value, *, at: int, device: str = "pc_main", namespace: str = "notes",
           deleted: bool = False, version: int = 0) -> dict:
    return {"namespace": namespace, "key": key, "value": value, "updated_at_ms": at,
            "device_id": device, "deleted": deleted, "version": version}


# ------------------------------------------------------------------ basic convergence
def test_a_new_record_is_created(sync, app):
    outcome = sync.apply(app.ctx(), [change("todo", "buy milk", at=1000)])
    assert outcome["applied"] == 1
    record = sync.record("notes", "todo")
    assert record["value"] == "buy milk"
    assert record["version"] == 1


def test_a_newer_change_wins_without_a_conflict(sync, app):
    """A strict causal order is not a conflict — recording it as one would train the owner to
    ignore the conflict list."""
    sync.apply(app.ctx(), [change("todo", "v1", at=1000)])
    outcome = sync.apply(app.ctx(), [change("todo", "v2", at=2000, device="phone_main")])
    assert outcome["conflicts"] == []
    assert sync.record("notes", "todo")["value"] == "v2"


def test_a_stale_change_is_rejected_without_a_conflict(sync, app):
    sync.apply(app.ctx(), [change("todo", "new", at=2000, device="phone_main")])
    outcome = sync.apply(app.ctx(), [change("todo", "old", at=1000, device="pc_main")])
    assert outcome["applied"] == 0
    assert outcome["results"][0]["action"] == "stale"
    assert outcome["conflicts"] == []
    assert sync.record("notes", "todo")["value"] == "new"


def test_an_identical_change_is_a_no_op(sync, app):
    sync.apply(app.ctx(), [change("todo", "same", at=1000)])
    outcome = sync.apply(app.ctx(), [change("todo", "same", at=1000, device="phone_main")])
    assert outcome["results"][0]["action"] == "unchanged"
    assert outcome["conflicts"] == []


def test_a_batch_applies_every_change(sync, app):
    outcome = sync.apply(app.ctx(), [change("a", 1, at=1000), change("b", 2, at=1000),
                                     change("c", 3, at=1000)])
    assert outcome["applied"] == 3
    assert {r["key"] for r in sync.records("notes")} == {"a", "b", "c"}


def test_namespaces_are_independent(sync, app):
    sync.apply(app.ctx(), [change("k", "notes-value", at=1000, namespace="notes")])
    sync.apply(app.ctx(), [change("k", "tasks-value", at=1000, namespace="tasks")])
    assert sync.record("notes", "k")["value"] == "notes-value"
    assert sync.record("tasks", "k")["value"] == "tasks-value"


# ------------------------------------------------------------------ golden #17
def test_offline_edits_on_two_devices_converge(sync, app):
    """The golden case: both sides edited while apart, same timestamp, different values."""
    sync.apply(app.ctx(), [change("shopping", ["milk"], at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("shopping", ["bread"], at=5000,
                                            device="phone_main")])
    assert outcome["conflicts"], "concurrent divergent edits must be recorded"
    conflict = outcome["conflicts"][0]
    assert conflict["key"] == "shopping"
    assert conflict["incoming"] and conflict["stored"]
    assert conflict["reason"]
    # and the store is in a defined state — never half-applied
    assert sync.record("notes", "shopping")["value"] in (["milk"], ["bread"])


def test_default_policy_is_last_write_wins_with_a_deterministic_tie_break(sync, app):
    assert sync.policy_for("notes") == DEFAULT_POLICY == SyncPolicy.LAST_WRITE_WINS.value
    sync.apply(app.ctx(), [change("k", "pc", at=5000, device="pc_main")])
    sync.apply(app.ctx(), [change("k", "phone", at=5000, device="phone_main")])
    # pc_main has the higher priority rank, so it keeps the value
    assert sync.record("notes", "k")["value"] == "pc"
    # the same input always produces the same result
    before = sync.record("notes", "k")["value"]
    sync.apply(app.ctx(), [change("k", "phone", at=5000, device="phone_main")])
    assert sync.record("notes", "k")["value"] == before


def test_device_priority_policy_lets_the_preferred_device_win(sync, app):
    sync.set_policy("notes", SyncPolicy.DEVICE_PRIORITY.value)
    sync.apply(app.ctx(), [change("k", "phone-value", at=5000, device="phone_main")])
    sync.apply(app.ctx(), [change("k", "pc-value", at=5000, device="pc_main")])
    assert sync.record("notes", "k")["value"] == "pc-value", "pc_main outranks phone_main"


def test_merge_union_policy_combines_lists(sync, app):
    sync.set_policy("notes", SyncPolicy.MERGE_UNION.value)
    sync.apply(app.ctx(), [change("shopping", ["milk"], at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("shopping", ["bread"], at=5000,
                                            device="phone_main")])
    assert outcome["results"][0]["action"] == "merged"
    assert sync.record("notes", "shopping")["value"] == ["milk", "bread"]


def test_merge_union_deduplicates(sync, app):
    sync.set_policy("notes", SyncPolicy.MERGE_UNION.value)
    sync.apply(app.ctx(), [change("l", ["a", "b"], at=5000, device="pc_main")])
    sync.apply(app.ctx(), [change("l", ["b", "c"], at=5000, device="phone_main")])
    assert sync.record("notes", "l")["value"] == ["a", "b", "c"]


def test_merge_union_falls_back_when_values_are_not_mergeable(sync, app):
    sync.set_policy("notes", SyncPolicy.MERGE_UNION.value)
    sync.apply(app.ctx(), [change("k", "text", at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("k", 42, at=5000, device="phone_main")])
    assert outcome["conflicts"], "unmergeable values must still be recorded as a conflict"
    assert sync.record("notes", "k")["value"] == "text"


def test_manual_policy_keeps_both_sides_for_the_owner(sync, app):
    """`manual` must not guess: the stored value stays and the conflict waits for the owner."""
    sync.set_policy("notes", SyncPolicy.MANUAL.value)
    sync.apply(app.ctx(), [change("k", "stored", at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("k", "incoming", at=5000, device="phone_main")])
    assert outcome["results"][0]["action"] == "awaiting_owner"
    assert sync.record("notes", "k")["value"] == "stored", "nothing is applied without the owner"
    open_conflicts = sync.conflicts(unresolved_only=True)
    assert len(open_conflicts) == 1
    # for `manual` there is no winner yet — both sides are held for the owner
    assert open_conflicts[0]["needs_owner"] is True
    assert open_conflicts[0]["chosen"] == ""
    assert open_conflicts[0]["winner"] is None
    assert {open_conflicts[0]["incoming"]["value"], open_conflicts[0]["stored"]["value"]} == \
        {"stored", "incoming"}


def test_the_owner_can_resolve_a_manual_conflict(sync, app):
    sync.set_policy("notes", SyncPolicy.MANUAL.value)
    sync.apply(app.ctx(), [change("k", "stored", at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("k", "incoming", at=5000, device="phone_main")])
    conflict_id = outcome["conflicts"][0]["conflict_id"]

    resolved = sync.resolve_conflict(conflict_id, winner="incoming", by="owner")
    assert resolved["ok"] is True
    assert resolved["value"] == "incoming"
    assert sync.record("notes", "k")["value"] == "incoming"
    assert sync.conflicts(unresolved_only=True) == []


def test_resolving_with_an_unknown_choice_is_refused(sync, app):
    sync.set_policy("notes", SyncPolicy.MANUAL.value)
    sync.apply(app.ctx(), [change("k", "a", at=5000)])
    outcome = sync.apply(app.ctx(), [change("k", "b", at=5000, device="phone_main")])
    conflict_id = outcome["conflicts"][0]["conflict_id"]
    assert sync.resolve_conflict(conflict_id, winner="mine")["ok"] is False
    assert sync.resolve_conflict("conf_missing", winner="stored")["ok"] is False


def test_conflicts_are_visible_for_the_ui(sync, app):
    """Golden #17 also requires the conflict to be *shown*, so it must be queryable."""
    sync.apply(app.ctx(), [change("k", "a", at=5000, device="pc_main")])
    sync.apply(app.ctx(), [change("k", "b", at=5000, device="phone_main")])
    listed = sync.conflicts(unresolved_only=True)
    assert listed and listed[0]["key"] == "k"
    assert listed[0]["namespace"] == "notes"
    assert listed[0]["policy"] == DEFAULT_POLICY
    # a policy-resolved conflict says which side it chose
    assert listed[0]["chosen"] in ("incoming", "stored")
    assert listed[0]["winner"] is not None
    # a policy-resolved conflict says which side it chose
    assert listed[0]["chosen"] in ("incoming", "stored")
    assert listed[0]["winner"] is not None
    status = sync.status()
    assert status["unresolved_conflicts"] == 1


def test_conflicts_are_audited(sync, app):
    sync.apply(app.ctx(), [change("k", "a", at=5000, device="pc_main")])
    sync.apply(app.ctx(), [change("k", "b", at=5000, device="phone_main")])
    entries = [e for e in app.audit.tail(50) if str(e.get("action", "")).startswith("sync.conflict")]
    assert entries, "a conflict must be audited"


# ------------------------------------------------------------------ tombstones
def test_a_deletion_propagates_as_a_tombstone(sync, app):
    """An offline device must not resurrect a record it never saw deleted."""
    sync.apply(app.ctx(), [change("k", "value", at=1000)])
    outcome = sync.apply(app.ctx(), [change("k", None, at=2000, device="phone_main",
                                            deleted=True)])
    assert outcome["applied"] == 1
    record = sync.record("notes", "k")
    assert record["deleted"] is True
    # a stale edit from a device that never saw the delete must not win
    stale = sync.apply(app.ctx(), [change("k", "resurrected", at=1500, device="pc_main")])
    assert stale["results"][0]["action"] == "stale"
    assert sync.record("notes", "k")["deleted"] is True


def test_a_deletion_and_an_edit_at_the_same_time_is_a_conflict(sync, app):
    sync.apply(app.ctx(), [change("k", "value", at=5000, device="pc_main")])
    outcome = sync.apply(app.ctx(), [change("k", None, at=5000, device="phone_main",
                                            deleted=True)])
    assert outcome["conflicts"], "delete vs edit at the same instant must be surfaced"


# ------------------------------------------------------------------ cursors
def test_a_device_can_pull_only_what_changed(sync, app):
    sync.apply(app.ctx(), [change("a", 1, at=1000)])
    first_cursor = sync.cursor()
    sync.apply(app.ctx(), [change("b", 2, at=2000)])
    incremental = sync.records(since_cursor=first_cursor)
    assert [r["key"] for r in incremental] == ["b"]


def test_the_cursor_advances_with_every_write(sync, app):
    before = sync.cursor()
    sync.apply(app.ctx(), [change("a", 1, at=1000)])
    assert sync.cursor() > before


def test_a_change_without_a_key_is_rejected(sync, app):
    outcome = sync.apply(app.ctx(), [{"namespace": "notes", "key": "", "value": "x"}])
    assert outcome["applied"] == 0
    assert "required" in outcome["results"][0]["reason"]


def test_an_unknown_policy_is_refused(sync, app):
    assert sync.set_policy("notes", "coin-flip")["ok"] is False


def test_policies_are_per_namespace(sync, app):
    sync.set_policy("tasks", SyncPolicy.MERGE_UNION.value)
    assert sync.policy_for("tasks") == SyncPolicy.MERGE_UNION.value
    assert sync.policy_for("notes") == DEFAULT_POLICY


# ------------------------------------------------------------------ merge helper
def test_merge_values_handles_lists_dicts_and_scalars():
    assert merge_values(["a"], ["b"]) == ["a", "b"]
    assert merge_values({"x": 1}, {"y": 2}) == {"x": 1, "y": 2}
    assert merge_values("a", "b") is None
    assert merge_values(1, 2) is None


# ------------------------------------------------------------------ sync in status
def test_device_status_includes_sync(app):
    app.devices.sync.apply(app.ctx(), [change("k", "v", at=1000)])
    status = app.devices.status()
    assert status["sync"]["records"] == 1
    assert status["sync"]["default_policy"] == DEFAULT_POLICY
