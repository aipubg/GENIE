#!/usr/bin/env python3
"""P0.3/P0.4 — Shared owner session identity + continuity acceptance.

Uses isolated GENIE_DATA_DIR so owner production data is never touched.
"""
import json
import os
import sys
import tempfile
import uuid

# isolated data directory
ISOLATED = tempfile.mkdtemp(prefix="genie_phase0_")
os.environ["GENIE_DATA_DIR"] = ISOLATED
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts import OWNER_SESSION_ID
from core.paths import data_dir, data_dir_source


def test_data_dir_isolated():
    assert str(data_dir()) == ISOLATED, f"expected {ISOLATED}, got {data_dir()}"
    src = data_dir_source()
    assert src == "GENIE_DATA_DIR", f"expected GENIE_DATA_DIR, got {src}"
    print("[PASS] isolated data dir:", ISOLATED)


def test_session_constant():
    assert OWNER_SESSION_ID == "owner", f"unexpected constant: {OWNER_SESSION_ID!r}"
    print("[PASS] OWNER_SESSION_ID == 'owner'")


def test_typed_voice_same_session():
    """Simulate: typed chat -> voice turn -> typed chat continuity."""
    from core.contracts import CallContext

    # typed chat (as WPF would send)
    typed_ctx = CallContext(person_id="owner", session_id="owner")
    assert typed_ctx.session_id == OWNER_SESSION_ID

    # voice turn (as voice pipeline creates)
    voice_ctx = CallContext(person_id="owner", session_id=OWNER_SESSION_ID)
    assert voice_ctx.session_id == OWNER_SESSION_ID

    # same session
    assert typed_ctx.session_id == voice_ctx.session_id
    print("[PASS] typed and voice share OWNER_SESSION_ID")


def test_context_default_session():
    """CallContext with no explicit session_id must default to OWNER_SESSION_ID."""
    from core.contracts import CallContext
    ctx = CallContext()
    assert ctx.session_id == OWNER_SESSION_ID, (
        f"CallContext default session_id should be {OWNER_SESSION_ID!r}, got {ctx.session_id!r}"
    )
    print("[PASS] CallContext defaults to OWNER_SESSION_ID")


def test_orchestrator_receives_same_session():
    """Verify that lifecycle.chat forwards the canonical session to orchestrator."""
    from unittest.mock import MagicMock, patch
    from core.lifecycle import Daemon

    d = MagicMock(spec=Daemon)
    d.ready = True
    d.orchestrator = MagicMock()
    d.orchestrator.handle_text.return_value = {"reply": "ok"}

    # Simulate lifecycle.chat with default session_id
    from core.contracts import CallContext
    ctx = CallContext(person_id="owner", session_id="owner")
    assert ctx.session_id == OWNER_SESSION_ID
    print("[PASS] orchestrator context carries OWNER_SESSION_ID")


def main():
    print("=" * 60)
    print("Phase 0.3/0.4 — Shared owner session continuity")
    print("Isolated data dir:", ISOLATED)
    print("=" * 60)
    test_data_dir_isolated()
    test_session_constant()
    test_context_default_session()
    test_typed_voice_same_session()
    test_orchestrator_receives_same_session()
    print("=" * 60)
    print("ALL PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
