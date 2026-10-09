"""Visual fallback policy wiring tests (B04/E31 repair)."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from core.contracts import DataClass  # noqa: E402
from security.policy import PolicyRegistry, get_policy  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    p = PolicyRegistry()

    # 1. Privacy is NOT weakened globally: default still blocks SENSITIVE.
    check("default_blocks_sensitive", not p.allows_data("anyone", DataClass.SENSITIVE))

    # 2. SECRET/RESTRICTED are never allowed, even with a scope.
    check("secret_never_allowed", not p.allows_data("anyone", DataClass.SECRET))
    check("restricted_never_allowed", not p.allows_data("anyone", DataClass.RESTRICTED))
    with p.request_scope("anyone", DataClass.SECRET, reason="test"):
        check("scope_cannot_unlock_secret", not p.allows_data("anyone", DataClass.SECRET))

    # 3. Configured provider policies are applied (the missing caller).
    applied = p.apply_provider_policies([
        {"id": "vis", "policy": {"allowed_data_classes": ["PUBLIC", "SENSITIVE"],
                                 "vision_allowed": True}},
        {"id": "plain", "policy": {"allowed_data_classes": ["PUBLIC"]}},
        {"id": "nopolicy"},
    ])
    check("configured_policies_applied", applied == 2, f"applied={applied}")
    check("configured_provider_allows_sensitive", p.allows_data("vis", DataClass.SENSITIVE))
    check("other_provider_still_blocked", not p.allows_data("plain", DataClass.SENSITIVE))

    # 4. Bounded per-request scope: grants, then fully clears.
    with p.request_scope("lend", DataClass.SENSITIVE, reason="owner approved"):
        check("scope_grants_during_request", p.allows_data("lend", DataClass.SENSITIVE))
    check("scope_cleared_after_request", not p.allows_data("lend", DataClass.SENSITIVE))

    # 5. Scope does not leak to other providers.
    with p.request_scope("a", DataClass.SENSITIVE):
        check("scope_not_global", not p.allows_data("b", DataClass.SENSITIVE))

    # 6. Gateway exposes a policy-free vision listing for pre-approval discovery.
    from models.gateway import Gateway
    check("gateway_vision_capable_exists", hasattr(Gateway, "vision_capable"))

    # 7. Visual fallback actually uses both the discovery and the scope.
    from computer.visual import VisualFallback
    import inspect
    src = inspect.getsource(VisualFallback._ask)
    check("ask_uses_vision_capable", "vision_capable" in src)
    check("ask_uses_request_scope", "request_scope" in src)
    check("ask_still_requires_owner_consent", "remote_visual_consent" in src)
    check("ask_still_requires_approval", "approvals.request" in src)

    # 8. Registry reload pushes configured policies into the effective registry.
    from models.registry import ModelRegistry
    reg = ModelRegistry()
    provs = reg._data.get("providers", [])
    sens = [r["id"] for r in provs
            if "SENSITIVE" in (r.get("policy") or {}).get("allowed_data_classes", [])]
    check("registry_loaded_some_policies", bool(sens), f"sensitive-cleared: {sens}")
    check("effective_registry_reflects_config",
          all(get_policy().allows_data(pid, DataClass.SENSITIVE) for pid in sens))

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Visual Fallback Policy Wiring Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
