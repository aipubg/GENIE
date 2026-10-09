"""Golden task #6 (memory) — spec Appendix E.

    #6 | Memory | "Kal wala Blender method use kar" | Correct episode retrieved

The owner refers to *yesterday's* Blender method with different words than the episode was stored
with. GENIE must surface the right memory, not just any memory — and crucially must not return a
confidently-wrong episode when asked for something it never recorded.
"""
from __future__ import annotations

from core.contracts import CallContext


def _seed(ctx, memory):
    """Two episodes: a Blender procedure, and an unrelated one."""
    memory.write(ctx, type="episodic", entity="blender",
                 value="Exported the mesh from Blender using File > Export > glTF 2.0",
                 confidence=0.9)
    memory.write(ctx, type="episodic", entity="downloads",
                 value="Organised the Downloads folder by file type into subfolders",
                 confidence=0.9)


def _value(hit) -> str:
    """Memory results arrive in several shapes (dict, MemoryHit, MemoryRecord)."""
    if isinstance(hit, dict):
        rec = hit.get("record") or hit
        return str(rec.get("value", ""))
    to_dict = getattr(hit, "to_dict", None)
    if callable(to_dict):
        try:
            return str(to_dict().get("value", ""))
        except Exception:
            pass
    rec = getattr(hit, "record", None)
    if rec is not None:
        return str(getattr(rec, "value", "") or
                   (rec.get("value", "") if isinstance(rec, dict) else ""))
    return str(getattr(hit, "value", ""))


def _values(hits):
    return [_value(h) for h in hits]


# ------------------------------------------------------------------ the golden
def test_golden_6_the_correct_episode_is_retrieved_for_a_different_wording(app):
    ctx = CallContext()
    _seed(ctx, app.memory)

    # "Kal wala Blender method" — restated, not verbatim
    hits = app.memory.query(ctx, "Blender method", limit=5)
    assert hits, "a stored episode must be retrievable"
    assert "Blender" in _values(hits)[0], f"wrong episode retrieved: {_values(hits)[0]!r}"


def test_golden_6_the_unrelated_episode_is_not_returned_as_the_answer(app):
    ctx = CallContext()
    _seed(ctx, app.memory)
    hits = app.memory.query(ctx, "Blender export", limit=5)
    assert hits
    assert "Downloads" not in _values(hits)[0], \
        f"returned the wrong episode: {_values(hits)[0]!r}"


def test_golden_6_asking_for_something_never_recorded_does_not_invent_it(app):
    """A memory system that always answers is worse than one that admits it doesn't know."""
    ctx = CallContext()
    _seed(ctx, app.memory)
    hits = app.memory.query(ctx, "quantum chromodynamics renormalisation", limit=5)
    for value in _values(hits):
        assert "Blender" not in value, \
            "must not return unrelated memories as if they answered the question"


def test_golden_6_recent_memory_lists_the_stored_episodes(app):
    ctx = CallContext()
    _seed(ctx, app.memory)
    recent = app.memory.recent(ctx, limit=10)
    assert len(recent) >= 2
    assert "Blender" in " ".join(_values(recent)), _values(recent)
