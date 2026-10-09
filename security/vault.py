"""Secrets vault (security/vault).

Contract C2. Rules:
  * raw secrets never appear in logs, traces, config files or model context
  * modules hold refs like `secret://provider/deepseek/key`
  * every use is audited

Encryption (Assumption A-005):
  * Windows  -> DPAPI (CryptProtectData) via ctypes: per-user, no key to manage
  * fallback -> scrypt-derived keystream XOR (obfuscation-grade; documented, not a substitute
                for OS keychains). Chosen to keep GENIE dependency-free on low-end PCs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

_IS_WINDOWS = sys.platform.startswith("win")

class VaultLoadError(RuntimeError):
    """An existing encrypted Vault could not be loaded safely."""


# --------------------------------------------------------------------------- DPAPI
def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptProtectData failed")
    size = int(blob_out.cbData)
    buf = ctypes.string_at(blob_out.pbData, size)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return buf


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptUnprotectData failed")
    size = int(blob_out.cbData)
    buf = ctypes.string_at(blob_out.pbData, size)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return buf


# --------------------------------------------------------------------------- fallback
def _derive_key(salt: bytes, password: bytes) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _xor(data: bytes, key: bytes) -> bytes:
    out = bytearray(data)
    for i in range(len(out)):
        out[i] ^= key[i % len(key)]
    return bytes(out)


class Vault:
    """File-backed encrypted secret store."""

    def __init__(self, path: str | Path, audit=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: Dict[str, str] = {}
        self._audit = audit
        self._salt = b"genie-vault-v1"
        self._load()

    # ------------------------------------------------------------------ crypto
    def _encrypt(self, raw: bytes) -> bytes:
        if _IS_WINDOWS:
            return b"DPAPI:" + _dpapi_protect(raw)
        key = _derive_key(self._salt, b"genie-local")
        return b"XOR:" + _xor(raw, key)

    def _decrypt(self, blob: bytes) -> bytes:
        if blob.startswith(b"DPAPI:") and _IS_WINDOWS:
            return _dpapi_unprotect(blob[6:])
        if blob.startswith(b"XOR:"):
            key = _derive_key(self._salt, b"genie-local")
            return _xor(blob[4:], key)
        raise ValueError("unknown vault blob format")

    # -------------------------------------------------------------------- store
    def _load(self) -> None:
        if not self.path.exists():
            self._data: Dict[str, str] = {}
            return
        try:
            raw = self._decrypt(self.path.read_bytes())
            loaded = json.loads(raw.decode("utf-8"))
            if not isinstance(loaded, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()):
                raise ValueError("invalid vault entries")
            self._data = loaded
        except Exception as exc:
            raise VaultLoadError("Existing GENIE Vault is unreadable; no data was modified.") from exc

    def _flush(self) -> None:
        payload = json.dumps(self._data).encode("utf-8")
        encrypted = self._encrypt(payload)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_bytes(encrypted)
        temp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # --------------------------------------------------------------------- API
    def store(self, ref: str, value: str) -> None:
        if not ref.startswith("secret://"):
            ref = f"secret://{ref.lstrip('/')}"
        with self._lock:
            previous = dict(self._data)
            self._data[ref] = value
            try:
                self._flush()
            except Exception:
                self._data = previous
                raise
            self._cache[ref] = value
        if self._audit:
            self._audit.record(who="system", device="pc_main", action="vault.store",
                               why="credential saved", result=f"ref={ref}")

    def resolve(self, ref: str) -> Optional[str]:
        """Returns the raw secret. NEVER log or forward this value."""
        with self._lock:
            if ref in self._cache:
                return self._cache[ref]
            value = self._data.get(ref)
            if value is None:
                return None
            self._cache[ref] = value
            return value

    def has(self, ref: str) -> bool:
        return self.resolve(ref) is not None

    def delete(self, ref: str) -> bool:
        with self._lock:
            previous = dict(self._data)
            existed = self._data.pop(ref, None) is not None
            if existed:
                try:
                    self._flush()
                except Exception:
                    self._data = previous
                    raise
                self._cache.pop(ref, None)
        return existed

    def rotate(self, ref: str, new_value: str) -> None:
        self.store(ref, new_value)

    def refs(self) -> list[str]:
        with self._lock:
            return sorted(self._data.keys())

    def ref_for(self, provider_id: str, model_id: str = "") -> str:
        return f"secret://provider/{provider_id}/key"

    def status(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "count": len(self._data),
            "backend": "dpapi" if _IS_WINDOWS else "xor-fallback",
            "refs": self.refs(),
        }


_VAULT: Vault | None = None


def get_vault(path: str | Path | None = None, audit=None) -> Vault:
    global _VAULT
    if _VAULT is None:
        if path is None:
            from core.config import get_config
            path = get_config().vault_path
        _VAULT = Vault(path, audit=audit)
    return _VAULT
