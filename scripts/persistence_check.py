"""
§20 relaunch persistence check for the Custom Provider workflow.

Phases are run around a full GENIE exit / relaunch:

    python scripts/persistence_check.py create    # add provider + key + 3 models
    python scripts/persistence_check.py verify    # after relaunch: still there?
    python scripts/persistence_check.py cleanup   # remove the test provider

The owner's credential is resolved from the Vault and never printed.
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8787"
PID = "perstest"
NAME = "Persistence Test"
URL = "https://api.xkiro.com/v1"
MODELS = [
    ("mistralai/mistral-small-2603", "Mistral Small"),
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
    ("google/gemini-3-flash", "Gemini 3 Flash"),
]

_results = []


def _post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def check(label, ok, detail=""):
    _results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


def rows():
    d = _get("/api/providers")
    return d.get("providers") or []


def owner_key():
    from core.config import Config
    from security.vault import Vault
    cfg = Config()
    return Vault(cfg.vault_path).resolve("secret://provider/mock/key") or ""


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "verify"

    if phase == "create":
        print("[create] add provider + credential + 3 models")
        for r in rows():
            if r.get("id") == PID:
                _post("/api/providers/remove", {"id": PID})
                break
        _post("/api/providers", {
            "id": PID, "display_name": NAME, "base_url": URL,
            "protocol": "openai_chat", "auth_scheme": "bearer", "custom": True,
        })
        _post(f"/api/providers/{PID}/key", {"key": owner_key()})
        for mid, disp in MODELS:
            _post(f"/api/providers/{PID}/models",
                  {"model_id": mid, "display_name": disp})
        row = next((r for r in rows() if r.get("id") == PID), None)
        check("created with 3 models",
              row is not None and len(row.get("models") or []) == 3,
              f"{len(row.get('models') or []) if row else 0} models")
        check("credential stored",
              bool(row and row.get("credential_present")),
              str(row.get("credential_present") if row else None))

    elif phase == "verify":
        print("[verify] after a FULL exit and relaunch")
        row = next((r for r in rows() if r.get("id") == PID), None)
        check("provider persisted", row is not None)
        if row:
            check("display name persisted", row.get("display_name") == NAME,
                  str(row.get("display_name")))
            check("base URL persisted", row.get("base_url") == URL,
                  str(row.get("base_url")))
            check("model count still 3", len(row.get("models") or []) == 3,
                  f"{len(row.get('models') or [])} models")
            saved = {m.get("model_id") for m in (row.get("models") or [])}
            check("selected models preserved",
                  all(mid in saved for mid, _ in MODELS),
                  ", ".join(sorted(saved))[:90])
            check("credential reference persisted",
                  bool(row.get("credential_present")))

    elif phase == "cleanup":
        print("[cleanup] remove the test provider")
        _post("/api/providers/remove", {"id": PID})
        _post(f"/api/providers/{PID}/key/remove", {})
        check("test provider removed",
              not any(r.get("id") == PID for r in rows()))
        check("owner provider untouched",
              any(r.get("id") == "mock" for r in rows()))

    ok = all(_results)
    print(f"  -> {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
