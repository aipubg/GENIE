#!/usr/bin/env python3
"""P0.5-P0.8 — ContextCompiler + memory once + recent turns + persistence test.

Uses isolated GENIE_DATA_DIR.
"""
import os
import sys
import tempfile

ISOLATED = tempfile.mkdtemp(prefix="genie_phase0_")
os.environ["GENIE_DATA_DIR"] = ISOLATED
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts import CallContext, OWNER_SESSION_ID
from context.compiler import ContextCompiler


class FakeMemory:
    """Records how many times query() is called."""
    def __init__(self):
        self.query_count = 0
        self.records = []

    def query(self, ctx, text, limit=6):
        self.query_count += 1
        # Return fake hits
        Hit = type("Hit", (), {"record": type("Rec", (), {
            "value": f"memory_about_{text[:10]}", "type": "semantic",
            "entity": "test", "confidence": 0.8, "source": "test"
        })(), "score": 0.95})
        return [Hit()]


def test_memory_retrieved_once():
    mem = FakeMemory()
    compiler = ContextCompiler(memory=mem)
    ctx = CallContext(session_id=OWNER_SESSION_ID)

    packet = compiler.compile(ctx, "What is my favorite color?",
                               memory_query="favorite color")

    assert mem.query_count == 1, f"memory.query called {mem.query_count} times, expected 1"
    assert len(packet.retrieved_memory) == 1
    assert packet.provenance[0].startswith("memory.query")
    print("[PASS] memory retrieved exactly once per compile()")


def test_recent_turns_included():
    mem = FakeMemory()
    compiler = ContextCompiler(memory=mem)
    ctx = CallContext(session_id=OWNER_SESSION_ID)

    recent = [
        ("Hello GENIE", "Hello! Aap kaise hain?"),
        ("What is my favorite color?", "Aapka favorite color blue hai."),
    ]
    packet = compiler.compile(ctx, "Repeat what you just said",
                               recent_turns=recent)

    assert len(packet.recent_turns) == 2
    assert packet.recent_turns[0]["user"] == "Hello GENIE"
    assert "recent" in packet.sections
    print("[PASS] recent turns included in packet")


def test_provenance_present():
    mem = FakeMemory()
    compiler = ContextCompiler(memory=mem)
    ctx = CallContext(session_id=OWNER_SESSION_ID)
    packet = compiler.compile(ctx, "Test", memory_query="test")

    assert any("memory.query" in p for p in packet.provenance)
    assert any("recent_turns" in p for p in packet.provenance)
    print("[PASS] provenance recorded")


def test_budget_enforced():
    compiler = ContextCompiler(memory=None, default_budget_tokens=10)
    ctx = CallContext(session_id=OWNER_SESSION_ID)
    packet = compiler.compile(ctx, "x" * 1000)

    assert packet.budget_overflow is True or packet.approx_tokens <= 10
    print("[PASS] budget tracking works")


def test_session_persistence_boundary():
    """Recent turns are ephemeral session state, not permanent memory."""
    from core import lifecycle

    previous = lifecycle._SESSION_STORE
    lifecycle._SESSION_STORE = None
    try:
        lifecycle._remember_turn(OWNER_SESSION_ID, "My test word is ORBIT", "OK, noted ORBIT")
        turns = lifecycle._history(OWNER_SESSION_ID)
        assert len(turns) >= 1
        assert turns[-1][0] == "My test word is ORBIT"
        print("[PASS] session turns persist in isolated fallback history")
    finally:
        lifecycle._SESSION_STORE = previous


def main():
    print("=" * 60)
    print("Phase 0.5-0.8 — ContextCompiler + memory + recent turns")
    print("Isolated data dir:", ISOLATED)
    print("=" * 60)
    test_memory_retrieved_once()
    test_recent_turns_included()
    test_provenance_present()
    test_budget_enforced()
    test_session_persistence_boundary()
    print("=" * 60)
    print("ALL PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
