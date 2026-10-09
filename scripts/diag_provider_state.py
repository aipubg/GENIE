"""
Safe provider / Vault diagnostics (§2).

Reports ONLY boolean or non-secret state. The credential value is resolved to
test that it exists and is then discarded - it is never printed, logged, or
written to any artifact.

    python scripts/diag_provider_state.py [provider_id ...]
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8787"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    wanted = sys.argv[1:] or None
    d = _get("/api/providers")
    rows = d.get("providers") or []

    from core.config import Config
    from security.vault import Vault
    cfg = Config()
    vault = Vault(cfg.vault_path)

    print(f"{'id':<16}{'name':<22}{'secret_ref':<12}{'vault':<12}"
          f"{'auth':<9}{'models':<8}{'status':<12}base_url")
    print("-" * 110)
    for p in rows:
        pid = p.get("id", "")
        if wanted and pid not in wanted:
            continue
        ref = str(p.get("secret_ref") or "").strip()
        ref_present = bool(ref)

        # Resolve to prove existence; the value is never stored or shown.
        secret = vault.resolve(ref) if ref_present else ""
        vault_ok = bool(secret)
        del secret

        name = str(p.get("display_name") or "")[:20]
        print(f"{pid:<16}{name:<22}"
              f"{('present' if ref_present else 'MISSING'):<12}"
              f"{('resolvable' if vault_ok else 'NO'):<12}"
              f"{str(p.get('auth_scheme') or '-'):<9}"
              f"{len(p.get('models') or []):<8}"
              f"{str(p.get('status') or '-'):<12}"
              f"{p.get('base_url') or ''}")

    # Deeper look at any provider the caller asked for by name.
    for pid in (wanted or []):
        p = next((r for r in rows if r.get("id") == pid), None)
        if not p:
            print(f"\n[!] provider {pid!r} not found")
            continue
        print(f"\n--- {pid} detail ---")
        for k in ("id", "display_name", "base_url", "protocol", "auth_scheme",
                  "secret_ref", "discovery_url", "timeout", "enabled",
                  "priority", "custom", "status", "last_test_status"):
            v = p.get(k)
            if k == "secret_ref":
                v = "present" if str(v or "").strip() else "MISSING"
            print(f"  {k:18s} = {v}")
        models = p.get("models") or []
        print(f"  models saved        = {len(models)}")
        for m in models[:10]:
            print(f"    - {m.get('model_id')}  caps={m.get('capabilities')}")
        if len(models) > 10:
            print(f"    ... and {len(models) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
