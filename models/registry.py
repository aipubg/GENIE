"""Provider / model registry (models/registry).

Requirement (D-023): adding or removing a model later must NOT require source changes.
Everything here is data: predefined templates come from config/providers.json, and user
additions/overrides are persisted to the canonical per-user GENIE data directory.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import paths as _paths
from core.contracts import ModelSpec
from core.logging_setup import get_logger

log = get_logger("models.registry")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "config" / "providers.json"


class ModelRegistry:
    def __init__(self, defaults_file: str | Path = DEFAULT_FILE,
                 user_file: str | Path | None = None, vault=None):
        self.defaults_file = Path(defaults_file)
        # User edits (custom providers + model roles) are MUTABLE state, so they
        # live in the data directory - never in the packaged/read-only app dir.
        self.user_file = Path(user_file) if user_file else _paths.data_dir() / "providers.user.json"
        self._vault = vault
        self._validation: Dict[str, Dict[str, Any]] = {}
        self._data: Dict[str, Any] = {"version": 1, "providers": []}
        self.reload()

    # ------------------------------------------------------------- validation
    def set_vault(self, vault) -> None:
        """Attach the secret vault so credential presence can be truthful.

        A `secret_ref` in config only means a reference is *configured*; it is
        NOT proof that a key was ever stored. Presence must come from the vault.
        """
        self._vault = vault

    def credential_present(self, provider_id: str) -> bool:
        ref = self.secret_ref(provider_id)
        if not ref or not self._vault:
            return False
        try:
            return bool(self._vault.has(ref))
        except Exception:
            return False

    def record_test_result(self, provider_id: str, ok: bool,
                           error_code: str = "") -> Dict[str, Any]:
        """Record a connection-test outcome. Never stores secret values."""
        entry = {
            "last_test_status": "ok" if ok else "fail",
            "last_test_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_test_error_code": (error_code or "")[:200],
        }
        self._validation[provider_id] = entry
        return dict(entry)

    def validation(self, provider_id: str) -> Dict[str, Any]:
        return dict(self._validation.get(provider_id) or {})

    @staticmethod
    def _canonical_status(enabled: bool, kind: str, local: bool,
                          needs_credential: bool, credential_present: bool,
                          base_url: str, last_test: str) -> str:
        """Canonical provider status. The backend is the authority.

        ready               validated successfully (recent test or real request)
        configured          required settings present, not yet validated
        needs_api_key       required credential absent
        needs_configuration endpoint/model missing
        unreachable         configured but validation failed
        disabled            explicitly off
        local               local runtime, no external credential
        test_only           mock/developer provider
        """
        if not enabled:
            return "disabled"
        if kind == "test":
            return "test_only"
        if local:
            return "local"
        if needs_credential and not credential_present:
            return "needs_api_key"
        if not base_url:
            return "needs_configuration"
        if last_test == "fail":
            return "unreachable"
        if last_test == "ok":
            return "ready"
        return "configured"

    # ------------------------------------------------------------------ loading
    def reload(self) -> None:
        base = json.loads(self.defaults_file.read_text(encoding="utf-8")) if self.defaults_file.exists() \
            else {"version": 1, "providers": []}
        merged = copy.deepcopy(base)
        if self.user_file.exists():
            try:
                user = json.loads(self.user_file.read_text(encoding="utf-8"))
                merged = self._merge(merged, user)
            except Exception as exc:
                log.error("bad user provider file: %s", exc)
        self._data = merged
        # B04/E31 repair: provider records carry `policy` fields that were
        # never loaded into the effective PolicyRegistry, so the Gateway
        # always saw the PUBLIC/INTERNAL default. Apply the owner's own
        # configured policies here (nothing is widened implicitly).
        self._sync_policies(merged.get('providers', []))

    def _sync_policies(self, providers) -> None:
        """Push configured provider policies into the effective registry."""
        try:
            from security.policy import get_policy
            applied = get_policy().apply_provider_policies(providers)
            if applied:
                log.debug("applied %d configured provider policy record(s)", applied)
        except Exception as exc:  # policy must never break provider loading
            log.debug("provider policy sync skipped: %s", exc)

    def _merge(self, base: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
        out = copy.deepcopy(base)
        for up in user.get("providers", []):
            match = next((p for p in out["providers"] if p["id"] == up["id"]), None)
            if not match:
                out["providers"].append(copy.deepcopy(up))
                continue
            match.update({k: v for k, v in up.items() if k != "models"})
            existing = {m["model_id"]: m for m in match.get("models", [])}
            for um in up.get("models", []):
                existing[um["model_id"]] = um
            match["models"] = list(existing.values())
        # Model roles are persisted too, otherwise owner defaults vanish on
        # restart (observed in rc14 acceptance).
        if isinstance(user.get("roles"), dict):
            out["roles"] = copy.deepcopy(user["roles"])
        return out

    def _persist_user(self) -> None:
        """Persist only the user-created/edited providers (keeps provenance clean)."""
        defaults = json.loads(self.defaults_file.read_text(encoding="utf-8"))
        defaults_by_id = {p["id"]: p for p in defaults.get("providers", [])}
        user_providers: List[Dict[str, Any]] = []
        for p in self._data["providers"]:
            baseline = defaults_by_id.get(p["id"])
            if baseline is None:
                user_providers.append(copy.deepcopy(p))
                continue
            edited = {k: copy.deepcopy(v) for k, v in p.items()
                      if k not in ("id", "models") and v != baseline.get(k)}
            baseline_models = {m["model_id"]: m for m in baseline.get("models", [])}
            changed_models = [copy.deepcopy(m) for m in p.get("models", [])
                              if m["model_id"] not in baseline_models
                              or m != baseline_models[m["model_id"]]]
            if edited or changed_models:
                user_providers.append({"id": p["id"], **edited, "models": changed_models})
        payload: Dict[str, Any] = {"version": 1, "providers": user_providers}
        if self._data.get("roles"):
            payload["roles"] = self._data["roles"]
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        self.user_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- provider
    def providers(self) -> List[Dict[str, Any]]:
        return self._data.get("providers", [])

    def provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.providers() if p["id"] == provider_id), None)

    def add_provider(self, provider: Dict[str, Any]) -> Dict[str, Any]:
        if not provider.get("id"):
            raise ValueError("provider id required")
        provider.setdefault("protocol", "openai_chat")
        provider.setdefault("models", [])
        provider.setdefault("enabled", True)
        provider.setdefault("priority", 500)
        # A blank secret_ref must never stick. setdefault() only fires when the
        # key is ABSENT, so a caller that sent "" left the provider credential-
        # less: GENIE then treated it as anonymous and sent no Authorization
        # header, which surfaced as a bogus 401 "Authentication failed" even
        # though the key was safely in the Vault.
        if provider.get("protocol") != "mock" and provider.get("kind") != "local" and \
                not str(provider.get("secret_ref") or "").strip():
            provider["secret_ref"] = f"secret://provider/{provider['id']}/key"
        if self.provider(provider["id"]):
            raise ValueError(f"provider {provider['id']} already exists")
        self._data["providers"].append(provider)
        self._persist_user()
        return provider

    def update_provider(self, provider_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        p = self.provider(provider_id)
        if not p:
            raise KeyError(provider_id)
        p.update({k: v for k, v in patch.items() if k != "id"})
        # Same guard as add_provider: an edit must never blank out the link to
        # the stored credential (see the comment there for why).
        if p.get("protocol") != "mock" and p.get("kind") != "local" and not str(p.get("secret_ref") or "").strip():
            p["secret_ref"] = f"secret://provider/{provider_id}/key"
        self._persist_user()
        return p

    def remove_provider(self, provider_id: str) -> bool:
        before = len(self._data["providers"])
        self._data["providers"] = [p for p in self._data["providers"] if p["id"] != provider_id]
        if len(self._data["providers"]) != before:
            self._persist_user()
            return True
        return False

    # ------------------------------------------------------------------- model
    def add_model(self, provider_id: str, model: Dict[str, Any]) -> Dict[str, Any]:
        p = self.provider(provider_id)
        if not p:
            raise KeyError(provider_id)
        if not model.get("model_id"):
            raise ValueError("model_id required")
        model.setdefault("display_name", model["model_id"])
        # setdefault() only fires when the key is ABSENT, so a caller that sent
        # an explicit empty list (the WPF client does) stored capabilities=[].
        # That made the model unroutable. An empty/absent capability set means
        # "unverified", which routes as general-capable.
        if not [c for c in (model.get("capabilities") or []) if str(c).strip()]:
            model["capabilities"] = ["general"]
        model.setdefault("context_window", 8192)
        model.setdefault("max_output", 2048)
        model.setdefault("priority", 100)
        model.setdefault("enabled", True)
        p.setdefault("models", [])
        p["models"] = [m for m in p["models"] if m["model_id"] != model["model_id"]]
        p["models"].append(model)
        self._persist_user()
        return model

    def update_model(self, provider_id: str, model_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        p = self.provider(provider_id)
        if not p:
            raise KeyError(provider_id)
        m = next((m for m in p.get("models", []) if m["model_id"] == model_id), None)
        if not m:
            raise KeyError(model_id)
        m.update(patch)
        self._persist_user()
        return m

    def remove_model(self, provider_id: str, model_id: str) -> bool:
        p = self.provider(provider_id)
        if not p:
            return False
        before = len(p.get("models", []))
        p["models"] = [m for m in p.get("models", []) if m["model_id"] != model_id]
        ok = len(p["models"]) != before
        if ok:
            self._persist_user()
        return ok

    # ------------------------------------------------------------------ resolve
    @staticmethod
    def _capabilities(m: Dict[str, Any]) -> List[str]:
        """Saved capability list, defaulting to 'general' when unknown.

        Never returns an empty list: an empty capability set is indistinguishable
        from "cannot do anything", which silently removed the model from routing.
        """
        caps = [str(c).strip().lower()
                for c in (m.get("capabilities") or []) if str(c).strip()]
        return caps or ["general"]

    def models(self, enabled_only: bool = True) -> List[ModelSpec]:
        specs: List[ModelSpec] = []
        for p in self.providers():
            if enabled_only and not p.get("enabled", True):
                continue
            for m in p.get("models", []):
                specs.append(ModelSpec(
                    provider_id=p["id"],
                    model_id=m["model_id"],
                    display_name=m.get("display_name", m["model_id"]),
                    protocol=p.get("protocol", "openai_chat"),
                    # Capability metadata is OPTIONAL and often absent: most
                    # OpenAI-compatible /v1/model lists simply do not advertise
                    # it. An empty list made ModelSpec.supports() reject EVERY
                    # saved model, so `candidates()` came back empty and Chat
                    # fell to "offline/degraded mode" even though the provider
                    # was Ready with a resolvable key and 111 models.
                    # Unverified capability is treated as generally capable -
                    # the router still verifies by actually calling the model.
                    capabilities=self._capabilities(m),
                    context_window=int(m.get("context_window", 8192)),
                    max_output=int(m.get("max_output", 2048)),
                    cost_in_per_1k=float(m.get("cost_in_per_1k", 0.0)),
                    cost_out_per_1k=float(m.get("cost_out_per_1k", 0.0)),
                    priority=int(m.get("priority", 100)) + int(p.get("priority", 100)),
                    enabled=bool(m.get("enabled", True)),
                ))
        return specs

    def model(self, provider_id: str, model_id: str) -> Optional[ModelSpec]:
        return next((s for s in self.models(enabled_only=False)
                     if s.provider_id == provider_id and s.model_id == model_id), None)

    def base_url(self, provider_id: str) -> str:
        p = self.provider(provider_id) or {}
        return p.get("base_url", "")

    def secret_ref(self, provider_id: str) -> str:
        p = self.provider(provider_id) or {}
        return p.get("secret_ref", f"secret://provider/{provider_id}/key")

    # -------------------------------------------------------------- model roles
    # LEGACY / DEPRECATED — retained as compatibility metadata only.
    # The automatic Model Gateway is the active routing authority.
    # These role preferences are no longer used for routing decisions.
    VALID_ROLES = ("everyday", "fast", "deep_work")

    def roles(self) -> Dict[str, Any]:
        stored = self._data.get("roles") or {}
        out = {}
        for role in self.VALID_ROLES:
            entry = stored.get(role) or {}
            out[role] = {
                "provider_id": entry.get("provider_id", ""),
                "model_id": entry.get("model_id", ""),
            }
        return out

    def set_role(self, role: str, provider_id: str, model_id: str) -> Dict[str, Any]:
        """Assign a model to a role. Returns the full roles map."""
        if role not in self.VALID_ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {self.VALID_ROLES}")
        provider_id = (provider_id or "").strip()
        model_id = (model_id or "").strip()
        if provider_id:
            p = self.provider(provider_id)
            if not p:
                raise KeyError(f"unknown provider {provider_id!r}")
            if model_id:
                ids = {m.get("model_id") for m in (p.get("models") or [])}
                if model_id not in ids:
                    raise ValueError(
                        f"model {model_id!r} is not registered on provider {provider_id!r}")
        roles = self._data.setdefault("roles", {})
        if provider_id and model_id:
            roles[role] = {"provider_id": provider_id, "model_id": model_id}
        else:
            roles.pop(role, None)          # clearing a role
        self._persist_user()
        return self.roles()

    def summary(self) -> List[Dict[str, Any]]:
        out = []
        for p in self.providers():
            kind = p.get("kind") or ("test" if p.get("protocol") == "mock" else "remote")
            out.append({
                "id": p["id"], "display_name": p.get("display_name", p["id"]),
                "protocol": p.get("protocol"), "base_url": p.get("base_url", ""),
                "enabled": p.get("enabled", True),
                "has_secret_ref": bool(p.get("secret_ref")),
                # A local/test provider needs no external credential, so the UI
                # must not label it "Needs API key" merely because secret_ref
                # is empty.
                "kind": kind,
                "local": kind in ("local", "test"),
                # Canonical status - the BACKEND is the authority. The frontend
                # must only map this enum to a label; it must not infer
                # usability from field combinations.
                "status": self._canonical_status(
                    enabled=bool(p.get("enabled", True)),
                    kind=kind,
                    local=kind in ("local", "test"),
                    needs_credential=bool(p.get("secret_ref")),
                    credential_present=self.credential_present(p["id"]),
                    base_url=p.get("base_url", ""),
                    last_test=(self._validation.get(p["id"]) or {}).get("last_test_status", ""),
                ),
                # Presence only - never a secret value.
                "credential_present": self.credential_present(p["id"]),
                "last_test_status": (self._validation.get(p["id"]) or {}).get("last_test_status"),
                "last_test_at": (self._validation.get(p["id"]) or {}).get("last_test_at"),
                # Point 0 — persisted custom-provider Advanced settings. The
                # frontend restores these into the Edit form, and Rediscover /
                # Test read them from the record. Never includes the API key —
                # that lives only in the Vault (has_secret_ref is presence only).
                "auth_scheme": p.get("auth_scheme", "bearer"),
                "discovery_url": p.get("discovery_url", ""),
                "timeout": p.get("timeout", 20),
                "headers": p.get("headers", {}) or {},
                "custom": bool(p.get("custom")),
                "models": [{"model_id": m["model_id"], "display_name": m.get("display_name"),
                            "capabilities": m.get("capabilities", []),
                            "context_window": m.get("context_window"),
                            "enabled": m.get("enabled", True)} for m in p.get("models", [])],
            })
        return out
