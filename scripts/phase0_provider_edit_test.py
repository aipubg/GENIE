"""
Phase 0 / P0.2 — provider edit + restart, on ISOLATED data.

Creates a provider with Base URL A, restarts (fresh registry loaded from disk),
edits to Base URL B leaving the stored key untouched, restarts again, then
proves the runtime really uses Base URL B, models survived and the key is still
resolvable. Deletes at the end.

Never prints secret material.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def fresh_registry():
    """A brand-new registry instance = what a restart actually loads."""
    from core.config import Config
    from security.vault import Vault
    from models.registry import ModelRegistry
    cfg = Config()
    vault = Vault(cfg.vault_path)
    return ModelRegistry(vault=vault), vault


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p0_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print(f"isolated data dir: {iso}\n")

    from core.paths import data_dir, data_dir_source
    check("P0.1 isolated dir honoured",
          str(data_dir()) == str(iso), f"source={data_dir_source()}")
    owner_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "GENIE" / "data"
    check("P0.1 owner dir not used", str(data_dir()) != str(owner_dir))

    import core.config  # reimport so config picks up the new data dir
    import importlib
    importlib.reload(core.config)

    URL_A = "https://example-a.test/v1"
    URL_B = "https://example-b.test/v1"
    PID = "p0edit"

    # 1. create
    reg, vault = fresh_registry()
    reg.add_provider({
        "id": PID, "display_name": "P0 Edit", "base_url": URL_A,
        "protocol": "openai_chat", "auth_scheme": "bearer", "custom": True,
        "enabled": True, "secret_ref": f"secret://provider/{PID}/key",
    })
    vault.store(f"secret://provider/{PID}/key", "not-a-real-secret")
    for m in ("model-one", "model-two", "model-three"):
        reg.add_model(PID, {"model_id": m, "capabilities": ["general"]})
    check("created with URL A", reg.provider(PID)["base_url"] == URL_A, URL_A)
    check("3 models saved", len(reg.provider(PID)["models"]) == 3)

    # 2. restart
    reg, vault = fresh_registry()
    check("after restart URL A", reg.provider(PID)["base_url"] == URL_A)
    check("key stored after restart",
          bool(vault.resolve(f"secret://provider/{PID}/key")))

    # 3. edit -> URL B, key untouched (as the UI does: no key in the patch)
    reg.update_provider(PID, {
        "display_name": "P0 Edit", "base_url": URL_B,
        "auth_scheme": "bearer", "discovery_url": "",
        "headers": {}, "timeout": 45.0,
    })
    check("edited to URL B", reg.provider(PID)["base_url"] == URL_B, URL_B)
    check("timeout persisted", reg.provider(PID).get("timeout") == 45.0)
    check("key still resolvable after edit",
          bool(vault.resolve(f"secret://provider/{PID}/key")))

    # 4. restart again — this is the actual defect being tested
    reg, vault = fresh_registry()
    p = reg.provider(PID)
    check("P0.2 after restart URL B (not stale A)",
          p["base_url"] == URL_B, f"got {p['base_url']}")
    check("P0.2 models preserved", len(p["models"]) == 3,
          str([m["model_id"] for m in p["models"]]))
    check("P0.2 key still stored",
          bool(vault.resolve(f"secret://provider/{PID}/key")))
    check("P0.2 secret_ref intact",
          str(p.get("secret_ref")) == f"secret://provider/{PID}/key")
    check("P0.2 timeout survived restart", p.get("timeout") == 45.0)

    # 5. runtime authority uses URL B (what the gateway would call)
    check("P0.2 runtime base_url() is B",
          reg.base_url(PID) == URL_B, reg.base_url(PID))

    # 6. delete
    reg.remove_provider(PID)
    reg2, _ = fresh_registry()
    check("delete works", reg2.provider(PID) is None)

    shutil.rmtree(iso.parent, ignore_errors=True)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
