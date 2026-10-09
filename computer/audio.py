"""Windows Core Audio (computer/audio).

Real master-volume and mute control through the Core Audio COM API (IMMDeviceEnumerator ->
IAudioEndpointVolume) using ctypes. This gives GENIE the ability to *read* the current
volume — which is what makes "set volume 30" verifiable instead of assumed.

Fallback: if Core Audio is unavailable (no audio endpoint, COM failure), volume up/down/mute
use media-key scancodes and absolute set is reported as approximate (`verified=False`).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_float, c_int, c_uint, c_void_p
from typing import Any, Dict, Optional

from core.logging_setup import get_logger

from . import windows_api as win

log = get_logger("computer.audio")

IS_WINDOWS = sys.platform.startswith("win")

CLSCTX_ALL = 0x17
eRender, eMultimedia = 0, 1
S_OK = 0

# CLSID_MMDeviceEnumerator {BCDE0395-E52F-467C-8E3D-C4579291692E}
# IID_IMMDeviceEnumerator {A95664D2-9614-4F35-A746-DE8DB63617E6}
# IID_IAudioEndpointVolume {5CDF2C82-841E-4546-9722-0CF74078229A}
CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

if IS_WINDOWS:
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def _guid(text: str) -> GUID:
        from uuid import UUID
        u = UUID(text)
        return GUID(u.time_low, u.time_mid, u.time_hi_version,
                    (ctypes.c_ubyte * 8)(*u.bytes[8:]))
else:  # pragma: no cover
    GUID = None  # type: ignore

    def _guid(text: str):  # type: ignore
        return None


def _vtbl(ptr, index: int, restype, *argtypes):
    """Call a COM method by vtable index."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


class _Endpoint:
    """Keeps COM pointers alive for the lifetime of one operation."""

    def __init__(self) -> None:
        self._co_init = False
        self.pEnumerator = c_void_p()
        self.pDevice = c_void_p()
        self.pVolume = c_void_p()

    def __enter__(self) -> "_Endpoint":
        if not IS_WINDOWS:
            raise RuntimeError("core audio is Windows only")
        hr = ctypes.windll.ole32.CoInitialize(None)
        self._co_init = hr in (S_OK, 1)      # S_FALSE = already initialised
        clsid = _guid(CLSID_MMDeviceEnumerator)
        iid = _guid(IID_IMMDeviceEnumerator)
        hr = ctypes.windll.ole32.CoCreateInstance(byref(clsid), None, CLSCTX_ALL,
                                                  byref(iid), byref(self.pEnumerator))
        if hr != S_OK:
            raise RuntimeError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")
        get_endpoint = _vtbl(self.pEnumerator, 4, ctypes.HRESULT, c_int, c_int,
                             POINTER(c_void_p))
        hr = get_endpoint(self.pEnumerator, eRender, eMultimedia, byref(self.pDevice))
        if hr != S_OK:
            raise RuntimeError(f"GetDefaultAudioEndpoint failed: 0x{hr & 0xFFFFFFFF:08x}")
        iid_vol = _guid(IID_IAudioEndpointVolume)
        activate = _vtbl(self.pDevice, 3, ctypes.HRESULT, POINTER(GUID), c_uint,
                         c_void_p, POINTER(c_void_p))
        hr = activate(self.pDevice, byref(iid_vol), CLSCTX_ALL, None, byref(self.pVolume))
        if hr != S_OK:
            raise RuntimeError(f"Activate(IAudioEndpointVolume) failed: 0x{hr & 0xFFFFFFFF:08x}")
        return self

    def __exit__(self, *exc) -> None:
        for ptr in (self.pVolume, self.pDevice, self.pEnumerator):
            try:
                if ptr and ptr.value:
                    release = _vtbl(ptr, 2, ctypes.c_ulong)
                    release(ptr)
            except Exception:
                pass
        if self._co_init:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    # ---------------------------------------------------------------- methods
    def get_volume(self) -> float:
        get = _vtbl(self.pVolume, 9, ctypes.HRESULT, POINTER(c_float))
        value = c_float()
        hr = get(self.pVolume, byref(value))
        if hr != S_OK:
            raise RuntimeError(f"GetMasterVolumeLevelScalar failed: 0x{hr & 0xFFFFFFFF:08x}")
        return float(value.value)

    def set_volume(self, scalar: float) -> None:
        setv = _vtbl(self.pVolume, 7, ctypes.HRESULT, c_float, c_void_p)
        hr = setv(self.pVolume, c_float(max(0.0, min(1.0, scalar))), None)
        if hr != S_OK:
            raise RuntimeError(f"SetMasterVolumeLevelScalar failed: 0x{hr & 0xFFFFFFFF:08x}")

    def get_mute(self) -> bool:
        get = _vtbl(self.pVolume, 15, ctypes.HRESULT, POINTER(c_int))
        value = c_int()
        hr = get(self.pVolume, byref(value))
        if hr != S_OK:
            raise RuntimeError(f"GetMute failed: 0x{hr & 0xFFFFFFFF:08x}")
        return bool(value.value)

    def set_mute(self, muted: bool) -> None:
        setm = _vtbl(self.pVolume, 14, ctypes.HRESULT, c_int, c_void_p)
        hr = setm(self.pVolume, c_int(1 if muted else 0), None)
        if hr != S_OK:
            raise RuntimeError(f"SetMute failed: 0x{hr & 0xFFFFFFFF:08x}")


# ------------------------------------------------------------------- public API
def get_state() -> Dict[str, Any]:
    """Current volume/mute state — the OBSERVE half of the action loop."""
    if not IS_WINDOWS:
        return {"available": False}
    try:
        with _Endpoint() as ep:
            volume = ep.get_volume()
            return {"available": True, "backend": "core_audio",
                    "volume": round(volume * 100), "scalar": round(volume, 4),
                    "muted": ep.get_mute()}
    except Exception as exc:
        log.debug("core audio unavailable: %s", exc)
        return {"available": False, "backend": "key_scancode", "error": str(exc)}


def set_volume(level: int) -> Dict[str, Any]:
    level = max(0, min(100, int(level)))
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    try:
        with _Endpoint() as ep:
            ep.set_volume(level / 100.0)
            if level > 0 and ep.get_mute():
                ep.set_mute(False)
            actual = round(ep.get_volume() * 100)
            return {"ok": True, "requested": level, "actual": actual,
                    "backend": "core_audio", "verified": abs(actual - level) <= 2,
                    "muted": ep.get_mute()}
    except Exception as exc:
        # fallback: relative key presses (approximate, unverifiable)
        win._keybd_scancode("volume_down", 50)
        if level > 0:
            win._keybd_scancode("volume_up", max(1, round(level / 2)))
        return {"ok": True, "requested": level, "actual": None,
                "backend": "key_scancode", "verified": False, "approx": True,
                "error": str(exc)}


def set_mute(muted: bool | None = None) -> Dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    try:
        with _Endpoint() as ep:
            target = (not ep.get_mute()) if muted is None else bool(muted)
            ep.set_mute(target)
            actual = ep.get_mute()
            return {"ok": True, "requested": target, "actual": actual,
                    "backend": "core_audio", "verified": actual == target}
    except Exception as exc:
        win._keybd_scancode("volume_mute", 1)
        return {"ok": True, "actual": None, "backend": "key_scancode",
                "verified": False, "error": str(exc)}


def volume_up() -> Dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    try:
        with _Endpoint() as ep:
            before = round(ep.get_volume() * 100)
            ep.set_volume(min(1.0, (before + 5) / 100.0))
            after = round(ep.get_volume() * 100)
            return {"ok": True, "before": before, "after": after, "backend": "core_audio",
                    "verified": after >= before}
    except Exception as exc:
        win._keybd_scancode("volume_up", 2)
        return {"ok": True, "backend": "key_scancode", "verified": False, "error": str(exc)}


def volume_down() -> Dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    try:
        with _Endpoint() as ep:
            before = round(ep.get_volume() * 100)
            ep.set_volume(max(0.0, (before - 5) / 100.0))
            after = round(ep.get_volume() * 100)
            return {"ok": True, "before": before, "after": after, "backend": "core_audio",
                    "verified": after <= before}
    except Exception as exc:
        win._keybd_scancode("volume_down", 2)
        return {"ok": True, "backend": "key_scancode", "verified": False, "error": str(exc)}
