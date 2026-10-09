import threading
import time

import pytest

from computer.owner_policy import OwnerPolicy
from computer.approvals import ActionApprovals
from core.contracts import CallContext, Persona


def test_persistent_and_revocable(tmp_path):
    path = tmp_path / "policy.json"
    policy = OwnerPolicy(path)
    policy.configure({"enabled": True, "grants": []})
    assert OwnerPolicy(path).allows(CallContext(), {"kind": "ordinary_visual"})
    policy.configure({"enabled": False, "grants": []})
    assert not OwnerPolicy(path).allows(CallContext(), {"kind": "ordinary_visual"})


@pytest.mark.parametrize("overrides", [{"agent_id": "agent"}, {"mission_id": "scheduled"},
                                       {"person_id": "other"}, {"persona": Persona.GUEST}, {"dry_run": True}])
def test_grants_cannot_authorize_other_principals(overrides):
    policy = OwnerPolicy()
    policy.configure({"enabled": True, "grants": []})
    assert not policy.allows(CallContext(**overrides), {"kind": "ordinary_visual"})


def test_screen_grant_pins_destination_and_expiry():
    policy = OwnerPolicy()
    scope = {"kind": "screen_analysis", "provider": "configured", "destination": "https://example.invalid/v1",
             "scope": "cropped-redacted-window"}
    policy.configure({"enabled": True, "grants": [{**scope, "expires_at": time.time() + 60}]})
    assert policy.allows(CallContext(), scope)
    assert not policy.allows(CallContext(), {**scope, "destination": "https://other.invalid/v1"})
    assert not policy.allows(CallContext(), {**scope, "provider": "other"})
    policy.configure({"enabled": True, "grants": [{**scope, "expires_at": time.time() - 1}]})
    assert not policy.allows(CallContext(), scope)


def test_message_grant_matches_exact_contact_and_text():
    scope = OwnerPolicy.message_scope("WhatsApp", "Exact recipient", "Exact message")
    policy = OwnerPolicy()
    policy.configure({"enabled": True, "grants": [{**scope, "expires_at": time.time() + 60}]})
    assert policy.allows(CallContext(), scope)
    for changed in (OwnerPolicy.message_scope("WhatsApp", "Other", "Exact message"),
                    OwnerPolicy.message_scope("WhatsApp", "Exact recipient", "Exact message!")):
        assert not policy.allows(CallContext(), changed)


def test_no_unscoped_or_destructive_automatic_approval():
    policy = OwnerPolicy()
    policy.configure({"enabled": True, "grants": []})
    assert not policy.allows(CallContext(), None)
    assert not policy.allows(CallContext(), {"kind": "delete"})
    with pytest.raises(ValueError):
        policy.configure({"enabled": True, "grants": [{"kind": "wildcard"}]})


def test_automatic_approval_preserves_cancel_and_audit():
    from unittest.mock import MagicMock
    policy, audit = OwnerPolicy(), MagicMock()
    policy.configure({"enabled": True, "grants": []})
    approvals = ActionApprovals(policy, audit)
    cancel = threading.Event()
    assert approvals.request(CallContext(), "grounded ordinary click", cancel,
                             scope={"kind": "ordinary_visual"})["ok"]
    audit.record.assert_called_once()
    cancel.set()
    assert not approvals.request(CallContext(), "click", cancel, scope={"kind": "ordinary_visual"})["ok"]
