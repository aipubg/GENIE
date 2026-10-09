"""Sanitized authenticated Gemini completion diagnostic."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_APP = (PROJECT_ROOT / "backend-dist" / "backend-runtime" / "app").resolve()
sys.path.insert(0, str(RUNTIME_APP))
from urllib.parse import urlsplit
from core.config import get_config
from security.vault import Vault, VaultLoadError
from models.registry import ModelRegistry
from models import provider_http

def diagnostic_event(name, value=""):
    print(f"DIAG_{name}={value}", flush=True)

def main() -> int:
    diagnostic_event("STARTED", True)
    if not (RUNTIME_APP / "core" / "paths.py").is_file(): print("BLOCKED: PACKAGED_RUNTIME_APP_NOT_FOUND"); return 2
    from core import paths
    if Path(paths.__file__).resolve().parents[1] != RUNTIME_APP or not paths.is_packaged(): print("BLOCKED: PACKAGED_IDENTITY_MISMATCH", flush=True); return 2
    diagnostic_event("PACKAGED_IDENTITY", True)
    config = get_config(reload=True)
    diagnostic_event("CONFIG_LOADED", True)
    print("runtime_mode=packaged"); print("data_dir_source=", paths.data_dir_source()); print("vault_exists=", config.vault_path.is_file())
    try: vault = Vault(config.vault_path)
    except VaultLoadError: print("BLOCKED: VAULT_UNREADABLE_OR_WRONG_IDENTITY"); return 2
    diagnostic_event("VAULT_READABLE", True)
    registry = ModelRegistry(vault=vault)
    provider = registry.provider("gemini")
    if not provider or not provider.get("enabled", True):
        print("BLOCKED: GEMINI_PROVIDER_NOT_CONFIGURED_OR_DISABLED"); return 2
    secret = vault.resolve(registry.secret_ref("gemini"))
    diagnostic_event("GEMINI_CREDENTIAL_PRESENT", bool(secret))
    if not secret:
        print("BLOCKED: GEMINI_CREDENTIAL_MISSING"); return 2
    endpoint = provider_http.chat_url(registry.base_url("gemini")); parsed = urlsplit(endpoint)
    print("provider=gemini"); print("endpoint_host=", parsed.hostname); print("endpoint_path=", parsed.path)
    diagnostic_event("ENDPOINT_VALID", True)
    models_probe = provider_http.request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", method="GET", secret=secret, auth_scheme="x-goog-api-key", timeout=12)
    diagnostic_event("AUTH_GET_STATUS", models_probe.get("status", 0)); diagnostic_event("AUTH_GET_ERROR", models_probe.get("error_code") or "NONE"); diagnostic_event("AUTH_GET_ELAPSED_MS", models_probe.get("elapsed_ms"))
    models = [m for m in provider.get("models", []) if m.get("enabled", True)]
    preferred = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro-preview")
    selected = next((x for x in preferred if any(m.get("model_id") == x for m in models)), models[0].get("model_id") if models else "")
    if not selected: print("BLOCKED: NO_ENABLED_GEMINI_MODEL"); return 2
    payload = {"model": selected, "messages":[{"role":"user","content":"Reply with exactly GENIE_PROVIDER_OK"}],"max_tokens":256,"stream":False}
    diagnostic_event("HTTP_REQUEST_STARTED", True); started = time.perf_counter()
    result = provider_http.request(endpoint, method="POST", secret=secret, auth_scheme="bearer", body=json.dumps(payload).encode(), timeout=20)
    diagnostic_event("HTTP_REQUEST_FINISHED", True); diagnostic_event("HTTP_STATUS", result.get("status", 0)); diagnostic_event("HTTP_ERROR_CODE", result.get("error_code") or "NONE"); diagnostic_event("HTTP_ELAPSED_MS", round((time.perf_counter()-started)*1000, 1))
    print("model=", selected); print("http_status=", result.get("status")); print("error_code=", result.get("error_code")); print("elapsed_ms=", result.get("elapsed_ms"))
    if result.get("status") != 200: print("RESULT=AUTHENTICATED_COMPLETION_FAILED"); return 1
    try: content = ((json.loads(result["body"]).get("choices") or [])[0].get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError): print("RESULT=INVALID_COMPLETION_RESPONSE"); return 1
    print("RESULT=PASS_REAL_PROVIDER" if "GENIE_PROVIDER_OK" in content else "RESULT=UNEXPECTED_MODEL_REPLY")
    return 0 if "GENIE_PROVIDER_OK" in content else 1
if __name__ == "__main__": raise SystemExit(main())
