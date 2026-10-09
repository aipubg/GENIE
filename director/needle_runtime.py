"""NEDLE2 runtime provisioning & verification (director/needle_runtime).

The owner must never browse for a model path by hand. This module provisions the official
Cactus Needle 2 runtime automatically:

    runtime dir   : %LOCALAPPDATA%\\GENIE\\runtime\\needle2\\   (config override available)
    python package: `cactus-needle` (official, module name `needle`)
    native engine : libneedle.dll / libneedle.so / libneedle.dylib from
                    HuggingFace `Cactus-Compute/needle2`

Provisioning order (owner-specified):
    1. official Python package  (cactus-needle)
    2. official native runtime  (fetch_library, else direct HTTPS download + sha256 verify)
    3. a localhost server is only a fallback and is NOT required here

Every artifact is recorded in `runtime.json` (version + sha256 + source) so `verify()`
can detect corruption or tampering.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from core.logging_setup import get_logger

log = get_logger("director.needle_runtime")

HF_REPO = "Cactus-Compute/needle2"
GENERATION = 2
DEFAULT_ENGINE_VERSION = "2.0.4"          # pinned fallback; live version comes from the package
MANIFEST_NAME = "runtime.json"
TIMEOUT_S = 180.0

_PLATFORM_LIB = {"win32": "libneedle.dll", "darwin": "libneedle.dylib", "linux": "libneedle.so"}
_PLATFORM_TAG = {"win32": "win_amd64", "darwin": "macosx_11_0_arm64", "linux": "manylinux2014_x86_64"}


def default_runtime_dir() -> Path:
    """GENIE-managed location (never a user-chosen path)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "GENIE" / "runtime" / "needle2"


def lib_name() -> str:
    return _PLATFORM_LIB.get(sys.platform, "libneedle.so")


class NeedleRuntime:
    def __init__(self, runtime_dir: str | Path | None = None):
        self.dir = Path(runtime_dir) if runtime_dir else default_runtime_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lib_path = self.dir / lib_name()
        self.manifest_path = self.dir / MANIFEST_NAME

    # ------------------------------------------------------------------ manifest
    def manifest(self) -> Dict[str, Any]:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _write_manifest(self, **fields: Any) -> None:
        data = self.manifest()
        data.update(fields)
        data["runtime_dir"] = str(self.dir)
        data["engine"] = f"needle{GENERATION}"
        data["updated_at"] = int(time.time())
        self.manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------ python package
    def package_installed(self) -> bool:
        try:
            import needle  # noqa: F401
            return True
        except Exception:
            return False

    def package_version(self) -> Optional[str]:
        try:
            import needle
            return getattr(needle, "__version__", None)
        except Exception:
            return None

    def install_package(self) -> Dict[str, Any]:
        """pip install the official package (bootstrap step)."""
        if self.package_installed():
            return {"ok": True, "already": True, "version": self.package_version()}
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "cactus-needle"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            ok = out.returncode == 0
            return {"ok": ok, "version": self.package_version() if ok else None,
                    "detail": (out.stdout or out.stderr)[-400:]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------ native library
    def verify(self) -> Dict[str, Any]:
        m = self.manifest()
        if not self.lib_path.exists():
            return {"ok": False, "reason": "library missing", "path": str(self.lib_path)}
        digest = self._sha256(self.lib_path)
        expected = m.get("sha256")
        if expected and digest != expected:
            return {"ok": False, "reason": "sha256 mismatch (corrupted or replaced)",
                    "path": str(self.lib_path), "expected": expected, "actual": digest}
        return {"ok": True, "path": str(self.lib_path), "sha256": digest,
                "version": m.get("version"), "source": m.get("source")}

    def _wheel_filename(self) -> str:
        version = self._engine_version()
        tag = self._platform_tag()
        return f"cactus_needle-{version}-py3-none-{tag}.whl"

    def _engine_version(self) -> str:
        try:
            from needle.agent import fetch
            return fetch.engine_version(GENERATION)
        except Exception:
            return DEFAULT_ENGINE_VERSION

    def _platform_tag(self) -> str:
        try:
            from needle.agent import fetch
            return fetch._platform_tag()
        except Exception:
            tag = _PLATFORM_TAG.get(sys.platform, "manylinux2014_x86_64")
            if sys.platform == "win32" and platform.machine().lower() in ("arm64", "aarch64"):
                tag = "win_arm64"
            return tag

    def _download_wheel_direct(self) -> Path:
        """Fallback path: direct HTTPS from the official HF repo (works when the
        huggingface_hub xet bridge returns a truncated file)."""
        wheel = self._wheel_filename()
        url = f"https://huggingface.co/{HF_REPO}/resolve/main/python/{wheel}"
        out = self.dir / wheel
        log.info("downloading official needle runtime: %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "GENIE-runtime-provisioner"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = resp.read()
        if len(data) < 1_000_000:
            raise RuntimeError(f"downloaded file too small ({len(data)} bytes) — likely an error page")
        out.write_bytes(data)
        return out

    def _extract_library(self, wheel: Path, source: str) -> Dict[str, Any]:
        with zipfile.ZipFile(wheel) as archive:
            member = f"needle/{lib_name()}"
            if member not in archive.namelist():
                raise RuntimeError(f"{member} not present in {wheel.name}")
            data = archive.read(member)
        self.lib_path.write_bytes(data)
        digest = self._sha256(self.lib_path)
        self._write_manifest(version=self._engine_version(), sha256=digest, source=source,
                             wheel=wheel.name, lib=lib_name())
        log.info("needle runtime installed: %s (%s bytes, source=%s)",
                 self.lib_path, len(data), source)
        return {"ok": True, "path": str(self.lib_path), "bytes": len(data),
                "sha256": digest, "source": source}

    def ensure_library(self, force: bool = False) -> Dict[str, Any]:
        """Provision the native engine if missing or invalid."""
        if not force:
            check = self.verify()
            if check.get("ok"):
                return {**check, "already": True}

        # 1) official fetch helper
        try:
            from needle.agent import fetch
            path = fetch.fetch_library(dest_dir=str(self.dir))
            lib = Path(path)
            if lib.exists() and lib.stat().st_size > 1_000_000:
                digest = self._sha256(lib)
                self.lib_path = lib
                self._write_manifest(version=self._engine_version(), sha256=digest,
                                     source="official:fetch_library", lib=lib.name)
                return {"ok": True, "path": str(lib), "sha256": digest,
                        "source": "official:fetch_library"}
            log.warning("official fetch produced an unusable file (%s bytes)", lib.stat().st_size if lib.exists() else 0)
        except Exception as exc:
            log.warning("official fetch_library failed: %s — falling back to direct download", exc)

        # 2) direct HTTPS download of the same official wheel
        try:
            wheel = self._download_wheel_direct()
            return self._extract_library(wheel, source="official:hf-direct")
        except Exception as exc:
            log.error("needle runtime provisioning failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ activate
    def activate(self) -> Dict[str, Any]:
        """Make the runtime visible to the `needle` package (single env contract)."""
        if not self.lib_path.exists():
            return {"ok": False, "error": "library not provisioned"}
        os.environ["NEEDLE2_LIB_PATH"] = str(self.lib_path)
        os.environ.setdefault("NEEDLE_TELEMETRY", "0")   # no telemetry from GENIE installs
        return {"ok": True, "lib": str(self.lib_path)}

    # ------------------------------------------------------------------- provision
    def provision(self, force: bool = False) -> Dict[str, Any]:
        """Full bootstrap: package -> native engine -> verify -> activate."""
        steps: Dict[str, Any] = {}
        steps["package"] = self.install_package()
        if not steps["package"].get("ok"):
            return {"ok": False, "steps": steps, "error": "python package unavailable"}
        steps["engine"] = self.ensure_library(force=force)
        if not steps["engine"].get("ok"):
            return {"ok": False, "steps": steps, "error": "native engine unavailable"}
        steps["verify"] = self.verify()
        steps["activate"] = self.activate()
        ok = all(s.get("ok") for s in steps.values())
        self._write_manifest(provisioned=True)
        return {"ok": ok, "steps": steps}

    # --------------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        m = self.manifest()
        return {
            "runtime_dir": str(self.dir),
            "package_installed": self.package_installed(),
            "package_version": self.package_version(),
            "engine_version": m.get("version"),
            "library": str(self.lib_path),
            "library_present": self.lib_path.exists(),
            "library_bytes": self.lib_path.stat().st_size if self.lib_path.exists() else 0,
            "sha256": m.get("sha256"),
            "source": m.get("source"),
            "verified": self.verify().get("ok", False),
        }


_RUNTIME: Optional[NeedleRuntime] = None


def get_runtime(runtime_dir: str | Path | None = None) -> NeedleRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        if runtime_dir is None:
            try:
                from core.config import get_config
                runtime_dir = get_config().get("director.needle.runtime_dir") or None
            except Exception:
                runtime_dir = None
        _RUNTIME = NeedleRuntime(runtime_dir)
    return _RUNTIME
