"""Model Policy Registry (security/policy) — master spec §1.6.

Decides which provider may see which data class. Hard filter: a provider that is not
cleared for a data class must never receive that payload.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional

from core.contracts import DataClass, EventType
from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("security.policy")

# A-007: conservative defaults. `SECRET`/`RESTRICTED` are never allowed remotely.
DEFAULT_POLICY: Dict[str, Dict[str, object]] = {
    "_default": {
        "allowed_data_classes": ["PUBLIC", "INTERNAL"],
        "vision_allowed": True,
        "code_execution_allowed": False,
        "max_cost_per_mission": 0.5,
    }
}

_RANK = {d: i for i, d in enumerate(
    ["PUBLIC", "INTERNAL", "SENSITIVE", "RESTRICTED", "SECRET"])}


class PolicyRegistry:
    def __init__(self, policies: Dict[str, Dict[str, object]] | None = None):
        self._policies: Dict[str, Dict[str, object]] = dict(DEFAULT_POLICY)
        self._policies.update(policies or {})
        self._bus = get_bus()
        # Bounded, owner-approved, per-request elevations. These are NOT policy
        # changes: they exist only for the duration of one already-approved
        # request and are cleared in the context manager's finally block.
        self._request_scopes: Dict[str, set] = {}

    def policy_for(self, provider_id: str) -> Dict[str, object]:
        return self._policies.get(provider_id, self._policies["_default"])

    def set_policy(self, provider_id: str, policy: Dict[str, object]) -> None:
        self._policies[provider_id] = policy

    def allows_data(self, provider_id: str, data_class: DataClass) -> bool:
        # SECRET/RESTRICTED are never allowed remotely, not even with a scope.
        if data_class in (DataClass.SECRET, DataClass.RESTRICTED):
            return False
        if data_class.value in self._request_scopes.get(provider_id, set()):
            return True
        allowed = self.policy_for(provider_id).get("allowed_data_classes", ["PUBLIC"])
        return data_class.value in allowed

    def allows_vision(self, provider_id: str) -> bool:
        return bool(self.policy_for(provider_id).get("vision_allowed", False))

    def max_cost(self, provider_id: str) -> float:
        return float(self.policy_for(provider_id).get("max_cost_per_mission", 0.5))

    def check(self, provider_id: str, data_class: DataClass, needs_vision: bool = False) -> bool:
        ok = self.allows_data(provider_id, data_class)
        if ok and needs_vision:
            ok = self.allows_vision(provider_id)
        if not ok:
            self._bus.publish(EventType.POLICY_VIOLATION_BLOCKED, {
                "provider_id": provider_id, "data_class": data_class.value,
                "needs_vision": needs_vision})
            log.warning("policy blocked provider=%s data_class=%s", provider_id, data_class.value)
        return ok

    @contextmanager
    def request_scope(self, provider_id: str, data_class: DataClass, *, reason: str = ""):
        """Allow ONE provider to see ONE data class for the duration of one call.

        This is the bounded repair for B04/E31: the owner already consented to
        remote visual processing, and the per-request approval named this exact
        provider and destination. Elevating just that provider, just for this
        request, does NOT change the stored policy for anyone else and is fully
        audited. SECRET/RESTRICTED still cannot be elevated (see allows_data).
        """
        bucket = self._request_scopes.setdefault(provider_id, set())
        bucket.add(data_class.value)
        self._bus.publish(EventType.POLICY_VIOLATION_BLOCKED, {
            "provider_id": provider_id, "data_class": data_class.value,
            "scope_granted": True, "reason": reason})
        log.info("policy scope granted provider=%s data_class=%s reason=%s",
                 provider_id, data_class.value, reason)
        try:
            yield
        finally:
            bucket.discard(data_class.value)
            if not bucket:
                self._request_scopes.pop(provider_id, None)

    def apply_provider_policies(self, records) -> int:
        """Load configured provider policy records into the effective registry.

        This is the missing caller the connectivity map identified (B04/E31):
        provider JSON carried `policy` fields, but nothing ever imported them, so
        the Gateway always saw the PUBLIC/INTERNAL default. Only fields the owner
        actually configured are applied; nothing is widened implicitly.
        """
        applied = 0
        for record in records or []:
            provider_id = str(record.get("id", "") or "")
            policy = record.get("policy")
            if not provider_id or not isinstance(policy, dict) or not policy:
                continue
            merged = dict(self.policy_for(provider_id))
            merged.update(policy)
            self._policies[provider_id] = merged
            applied += 1
        return applied

    def as_dict(self) -> Dict[str, Dict[str, object]]:
        return dict(self._policies)


_POLICY: Optional[PolicyRegistry] = None


def get_policy() -> PolicyRegistry:
    global _POLICY
    if _POLICY is None:
        _POLICY = PolicyRegistry()
    return _POLICY
