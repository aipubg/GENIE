"""Configuration service.

Assumption A-001: configuration is JSON (stdlib only) instead of YAML, so GENIE has
zero third-party runtime dependencies and stays light on low-end PCs.
Assumption A-002: config resolution order = env var GENIE_* > user config file > defaults.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

# Path model: ALL path decisions come from core.paths (the single authority).
# Do not recompute them here - spreading sys._MEIPASS / sys.frozen / layout
# checks across modules is exactly how packaged builds end up writing mutable
# state into read-only resources.
from core import paths as _paths  # noqa: E402

ROOT = _paths.app_root()
CONFIG_DIR = _paths.bundled_config_dir()
DATA_DIR = _paths.data_dir()
LOG_DIR = _paths.log_dir()
MODEL_DIR = _paths.model_dir()
USER_CONFIG_PATH = DATA_DIR / "user.json"

DEFAULTS: Dict[str, Any] = {
    "instance_id": "genie-pc-main",
    "data_dir": str(DATA_DIR),
    "log_level": "INFO",
    "ipc": {"host": "127.0.0.1", "port": 8787, "token_required": True},
    "database": {"path": "genie.db", "wal": True},
    "security": {
        "default_deny": True,
        "owner_id": "owner",
        "confirm_destructive": True,
        "vault_file": "vault.enc",
    },
    "director": {
        "engine": "auto",           # auto | needle | heuristic
        "needle": {
            "enabled": True,
            "runtime": "python",     # python (official package) | cli | http
            "model_path": "",
            "cli_path": "",
            "endpoint": "",
            "timeout_ms": 800,
            # runtime dir: empty -> %LOCALAPPDATA%\GENIE\runtime\needle2 (GENIE-managed)
            "runtime_dir": "",
            # official production contract: act on a call only at/above this confidence,
            # otherwise treat it as a refusal and escalate
            "confidence_threshold": 0.4,
            "auto_provision": True,  # setup/boot may provision the runtime automatically
        },
        "fast_path": True,
        "laya": {
            "mode": "shadow", "shadow_sampling": False,
            "shadow_sample_interval_s": 120,
            "python": str(MODEL_DIR / "laya" / "venv" / "Scripts" / "python.exe"),
            "source": str(MODEL_DIR / "laya" / "source-1e28ac20c089"),
            "model": str(MODEL_DIR / "laya" / "weights"),
            "timeout_s": 0.75, "confidence_threshold": 0.9,
        },
    },
    "models": {
        "registry_file": "providers.json",
        "failover": True,
        "max_failover_attempts": 3,
        "budget": {"per_mission_usd": 0.5, "per_day_usd": 5.0},
        "mock_mode": False,          # Production Chat must fail honestly when no real model is available
    },
    "memory": {"fts": True, "embed_provider": None, "recent_limit": 25},
    "missions": {"max_concurrent": 4, "default_targets": ["pc_main"]},
    "agents": {"max_concurrent": 4, "default_max_steps": 20},
    "computer": {"enabled": True, "dry_run_default": False, "workspace": "workspace"},
    "voice": {
        "enabled": True,
        "mode": "push_to_talk",          # push_to_talk | continuous
        "sample_rate": 16000,
        "language": "hinglish",          # hinglish | hi | en
        "speak_replies": True,
        "tts_volume": 100,
        "barge_in": True,
        "silence_timeout_ms": 700,
        "min_speech_ms": 250,
        "input_provider": "auto",        # auto | wav-file
        "wav_input": "",
        "input_device": None,
        "output_device": None,
        # Speech providers are optional; GENIE degrades honestly without them (D-045)
        "stt": {"base_url": "", "api_key_ref": "", "model": "whisper-1",
                # real offline recognizer (no cloud key required)
                "vosk_model": str(MODEL_DIR / "vosk-model-small-en-us-0.15"),
                "allow_vosk": True,
                "allow_file_provider": True},
        "tts": {"base_url": "", "api_key_ref": "", "model": "tts-1", "voice": "alloy",
                "allow_windows_sapi": True, "allow_sapi_file": True,
                "allow_synthetic": True},
        "realtime_model": "gemini-3.8-live",
    },
    "resource": {"profile": "auto", "low_end_polling_scale": 0.5},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> Dict[str, Any]:
    """GENIE_IPC__PORT=9000 -> {'ipc': {'port': 9000}} (2-level paths use "__").

    NOTE: the separator is a DOUBLE underscore. A single underscore is kept
    inside the key name, so GENIE_IPC_PORT would set the top-level key
    "ipc_port" and leave ipc.port untouched.
    """
    out: Dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith("GENIE_"):
            continue
        parts = key[len("GENIE_"):].lower().split("__")
        value: Any = raw
        for cast in (int, float):
            try:
                value = cast(raw)
                break
            except ValueError:
                continue
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return out


class Config:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data: Dict[str, Any] = _deep_merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, user_file: str | os.PathLike | None = None) -> "Config":
        cfg = cls()
        path = Path(user_file) if user_file else USER_CONFIG_PATH
        if path.exists():
            try:
                cfg._data = _deep_merge(cfg._data, json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # corrupt user config must not brick GENIE
                print(f"[config] ignoring bad user config {path}: {exc}")
        cfg._data = _deep_merge(cfg._data, _env_overrides())
        cfg.apply()
        return cfg

    def apply(self) -> None:
        data_dir = Path(self.get("data_dir"))
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "logs").mkdir(exist_ok=True)
        (data_dir / self.get("security.vault_file")).parent.mkdir(parents=True, exist_ok=True)
        ws = data_dir / self.get("computer.workspace")
        ws.mkdir(parents=True, exist_ok=True)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def save_user(self, path: str | os.PathLike | None = None) -> Path:
        p = Path(path) if path else USER_CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        return p

    def as_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self._data))

    # convenience
    @property
    def data_dir(self) -> Path:
        return Path(self.get("data_dir"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.get("database.path")

    @property
    def vault_path(self) -> Path:
        return self.data_dir / self.get("security.vault_file")


_CONFIG: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _CONFIG
    if _CONFIG is None or reload:
        _CONFIG = Config.load()
    return _CONFIG
