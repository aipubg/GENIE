"""Single runtime-path authority for GENIE.

Every product module must ask HERE for paths. No module should independently
guess from `sys._MEIPASS`, `sys.frozen`, `__file__` arithmetic, the current
working directory, or an ad-hoc GENIE_PACKAGED check. Spreading that logic is
how packaged builds end up writing mutable state into read-only resources.

Two modes
---------
development (source checkout)
    application resources resolve under the repository; model storage is still
    per-user and never inside the checkout.

packaged (installed GENIE)
    read-only application resources -> inside the installed bundle
    mutable/private state          -> %LOCALAPPDATA%\\GENIE\\...

Packaged detection covers both packaging styles:
  * frozen            - PyInstaller sets sys.frozen
  * embeddable Python - <runtime>/python/ next to <runtime>/app/, or an
                        explicit GENIE_PACKAGED=1

Invariants
----------
* Mutable state NEVER lands in the bundle (no Program Files, no app.asar, no
  Electron resources, no packaged `app/` tree).
* The current working directory is never trusted.
* Secrets live in the vault, never in config JSON.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]   # .../GENIE (dev) or .../app (packaged)


def is_packaged() -> bool:
    """True when running as an installed GENIE rather than a source checkout."""
    if getattr(sys, "frozen", False):
        return True
    if os.environ.get("GENIE_PACKAGED") == "1":
        return True
    # embeddable layout: <runtime>/python/... beside <runtime>/app/<packages>
    return (_PACKAGE_ROOT.parent / "python").is_dir() and _PACKAGE_ROOT.name == "app"


def app_root() -> Path:
    """Root of the GENIE application code (read-only in packaged mode)."""
    if is_packaged():
        # PyInstaller keeps bundled data next to the executable; the embeddable
        # runtime keeps it in <runtime>/app.
        return Path(getattr(sys, "_MEIPASS", _PACKAGE_ROOT))
    return _PACKAGE_ROOT


def bundled_config_dir() -> Path:
    """Read-only configuration shipped with the application."""
    return app_root() / "config"


def bundled_ui_dir() -> Path:
    """Read-only web UI served by the daemon."""
    return app_root() / "ui"


#: Canonical override for the mutable data directory. This is the ONLY
#: supported override going forward.
DATA_DIR_ENV = "GENIE_DATA_DIR"
#: Deprecated alias kept so older scripts/docs keep working. It is mapped
#: explicitly onto GENIE_DATA_DIR and must never become a second authority.
DATA_DIR_LEGACY_ENV = "GENIE_APP_DATA"


def _is_abs_override(value: str | None) -> bool:
    """Only absolute paths are meaningful for a packaged service."""
    return bool(value) and os.path.isabs(value)


#: Source recorded by the last data_dir() resolution, so diagnostics report
#: what actually happened even after the alias is normalised into the
#: canonical variable.
_RESOLVED_SOURCE: str | None = None


def data_dir_source() -> str:
    """How the data directory was resolved — for diagnostics truth."""
    if _RESOLVED_SOURCE is not None:
        return _RESOLVED_SOURCE
    if _is_abs_override(os.environ.get(DATA_DIR_ENV)):
        return DATA_DIR_ENV
    if _is_abs_override(os.environ.get(DATA_DIR_LEGACY_ENV)):
        return f"{DATA_DIR_LEGACY_ENV} (deprecated alias)"
    return "default"


def data_dir() -> Path:
    """Mutable/private state. Never inside the bundle.

    Precedence is deterministic and single-authority:
        GENIE_DATA_DIR          (canonical)
        GENIE_APP_DATA          (deprecated alias, mapped to the same value)
        packaged default        (%LOCALAPPDATA%\\GENIE\\data)
        source default          (<package>/data)
    """
    global _RESOLVED_SOURCE
    override = os.environ.get(DATA_DIR_ENV)
    if _is_abs_override(override):
        # First resolution wins: core/__init__ may resolve during import, and a
        # later call must not rewrite the recorded source.
        if _RESOLVED_SOURCE is None:
            _RESOLVED_SOURCE = DATA_DIR_ENV
        return Path(override)
    # Legacy alias: normalise it into the canonical variable so no component
    # can read a different location than another.
    legacy = os.environ.get(DATA_DIR_LEGACY_ENV)
    if _is_abs_override(legacy):
        if _RESOLVED_SOURCE is None:
            _RESOLVED_SOURCE = f"{DATA_DIR_LEGACY_ENV} (deprecated alias)"
        os.environ[DATA_DIR_ENV] = legacy
        return Path(legacy)
    if is_packaged():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            if _RESOLVED_SOURCE is None:
                _RESOLVED_SOURCE = "default"
            return Path(base) / "GENIE" / "data"
    if _RESOLVED_SOURCE is None:
        _RESOLVED_SOURCE = "default"
    return _PACKAGE_ROOT / "data"


def log_dir() -> Path:
    return data_dir() / "logs"


def model_dir() -> Path:
    """Canonical persistent model store, shared by source and packaged runs."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "GENIE" / "models"
    return Path.home() / ".local" / "share" / "GENIE" / "models"


def ensure_runtime_dirs() -> None:
    """Create the mutable directories the daemon writes to."""
    d = data_dir()
    (d / "logs").mkdir(parents=True, exist_ok=True)
    model_dir().mkdir(parents=True, exist_ok=True)


def diagnostics() -> dict:
    """Safe, non-secret summary for logs and diagnostics."""
    return {
        "packaged": is_packaged(),
        "package_root": str(_PACKAGE_ROOT),
        "app_root": str(app_root()),
        "bundled_config_dir": str(bundled_config_dir()),
        "bundled_ui_dir": str(bundled_ui_dir()),
        "data_dir": str(data_dir()),
        "log_dir": str(log_dir()),
        "model_dir": str(model_dir()),
    }


# Convenience module-level constants (resolved lazily once at import).
APP_ROOT: Path = app_root()
BUNDLED_CONFIG_DIR: Path = bundled_config_dir()
BUNDLED_UI_DIR: Path = bundled_ui_dir()
DATA_DIR: Path = data_dir()
LOG_DIR: Path = log_dir()
MODEL_DIR: Path = model_dir()

__all__ = [
    "is_packaged", "app_root", "bundled_config_dir", "bundled_ui_dir",
    "data_dir", "log_dir", "model_dir", "ensure_runtime_dirs", "diagnostics",
    "APP_ROOT", "BUNDLED_CONFIG_DIR", "BUNDLED_UI_DIR", "DATA_DIR", "LOG_DIR",
    "MODEL_DIR",
]

_ = Optional  # re-exported for typing convenience in callers
