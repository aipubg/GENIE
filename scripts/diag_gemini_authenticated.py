"""Sanitized authenticated Gemini completion diagnostic."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from urllib.parse import urlsplit
from core.config import Config
from security.vault import Vault
from models.registry import ModelRegistry
from models import provider_http

def main() -> int:
    config = Config(); vault = Vault(config.vault_path); registry = ModelRegistry(vault=vault)
    provider = registry.provider("gemini")
    if not provider or not provider.get("enabled", True):
        print("BLOCKED: GEMINI_PROVIDER_NOT_CONFIGURED_OR_DISABLED"); return 2
    secret = vault.resolve(registry.secret_ref("gemini"))
    if not secret:
        print("BLOCKED: GEMINI_CREDENTIAL_MISSING"); return 2
    endpoint = provider_http.chat_url(registry.base_url("gemini")); parsed = urlsplit(endpoint)
    print("provider=gemini"); print("endpoint_host=", parsed.hostname); print("endpoint_path=", parsed.path)
    models = [m for m in provider.get("models", []) if m.get("enabled", True)]
    preferred = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro-preview")
    selected = next((x for x in preferred if any(m.get("model_id") == x for m in models)), models[0].get("model_id") if models else "")
    if not selected: print("BLOCKED: NO_ENABLED_GEMINI_MODEL"); return 2
    payload = {"model": selected, "messages":[{"role":"user","content":"Reply with exactly GENIE_PROVIDER_OK"}],"max_tokens":256,"stream":False}
    result = provider_http.request(endpoint, method="POST", secret=secret, auth_scheme="bearer", body=json.dumps(payload).encode(), timeout=35)
    print("model=", selected); print("http_status=", result.get("status")); print("error_code=", result.get("error_code")); print("elapsed_ms=", result.get("elapsed_ms"))
    if result.get("status") != 200: print("RESULT=AUTHENTICATED_COMPLETION_FAILED"); return 1
    try: content = ((json.loads(result["body"]).get("choices") or [])[0].get("message") or {}).get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError): print("RESULT=INVALID_COMPLETION_RESPONSE"); return 1
    print("RESULT=PASS_REAL_PROVIDER" if "GENIE_PROVIDER_OK" in content else "RESULT=UNEXPECTED_MODEL_REPLY")
    return 0 if "GENIE_PROVIDER_OK" in content else 1
if __name__ == "__main__": raise SystemExit(main())
