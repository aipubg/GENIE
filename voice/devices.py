"""Audio device discovery (voice/devices).

GENIE never assumes a fixed audio device. Enumeration works through an optional provider
(`sounddevice`) and falls back to a dependency-free WinMM enumeration, so the daemon can
always report what the machine actually has — even when no capture library is installed.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from ctypes import wintypes
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("voice.devices")

IS_WINDOWS = sys.platform.startswith("win")


@dataclass
class AudioDevice:
    index: int
    name: str
    max_input_channels: int = 0
    max_output_channels: int = 0
    default_samplerate: int = 0
    is_default_input: bool = False
    is_default_output: bool = False
    source: str = "unknown"

    @property
    def can_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def can_output(self) -> bool:
        return self.max_output_channels > 0

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "name": self.name,
                "in_channels": self.max_input_channels,
                "out_channels": self.max_output_channels,
                "default_samplerate": self.default_samplerate,
                "default_input": self.is_default_input,
                "default_output": self.is_default_output, "source": self.source}


def sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


def list_devices() -> List[AudioDevice]:
    """All audio devices, via the best available provider."""
    if sounddevice_available():
        try:
            return _list_sounddevice()
        except Exception as exc:
            log.warning("sounddevice enumeration failed (%s) — falling back to WinMM", exc)
    return _list_winmm()


def _list_sounddevice() -> List[AudioDevice]:
    import sounddevice as sd
    try:
        pair = list(sd.default.device)          # DeviceList behaves like a 2-tuple
        default_in, default_out = (pair + [None, None])[:2]
    except Exception:
        default_in = default_out = None
    out: List[AudioDevice] = []
    for index, dev in enumerate(sd.query_devices()):
        out.append(AudioDevice(
            index=index, name=dev["name"],
            max_input_channels=int(dev["max_input_channels"]),
            max_output_channels=int(dev["max_output_channels"]),
            default_samplerate=int(dev["default_samplerate"]),
            is_default_input=(index == default_in),
            is_default_output=(index == default_out),
            source="sounddevice",
        ))
    return out


def _list_winmm() -> List[AudioDevice]:
    """Dependency-free enumeration (names + channel counts only)."""
    if not IS_WINDOWS:
        return []
    try:
        winmm = ctypes.WinDLL("winmm")

        class WAVEINCAPSW(ctypes.Structure):
            _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                        ("vDriverVersion", ctypes.c_uint), ("szPname", wintypes.WCHAR * 32),
                        ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                        ("wReserved1", wintypes.WORD)]

        class WAVEOUTCAPSW(ctypes.Structure):
            _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                        ("vDriverVersion", ctypes.c_uint), ("szPname", wintypes.WCHAR * 32),
                        ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                        ("wReserved1", wintypes.WORD), ("dwSupport", wintypes.DWORD)]

        winmm.waveInGetNumDevs.restype = ctypes.c_uint
        winmm.waveOutGetNumDevs.restype = ctypes.c_uint
        winmm.waveInGetDevCapsW.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        winmm.waveOutGetDevCapsW.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

        out: List[AudioDevice] = []
        for i in range(int(winmm.waveInGetNumDevs())):
            caps = WAVEINCAPSW()
            if winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                out.append(AudioDevice(index=i, name=caps.szPname,
                                       max_input_channels=int(caps.wChannels),
                                       source="winmm"))
        for i in range(int(winmm.waveOutGetNumDevs())):
            caps = WAVEOUTCAPSW()
            if winmm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                out.append(AudioDevice(index=i, name=caps.szPname,
                                       max_output_channels=int(caps.wChannels),
                                       source="winmm"))
        return out
    except Exception as exc:
        log.debug("winmm enumeration failed: %s", exc)
        return []


def default_input() -> Optional[AudioDevice]:
    devices = list_devices()
    for dev in devices:
        if dev.can_input and dev.is_default_input:
            return dev
    return next((d for d in devices if d.can_input), None)


def default_output() -> Optional[AudioDevice]:
    devices = list_devices()
    for dev in devices:
        if dev.can_output and dev.is_default_output:
            return dev
    return next((d for d in devices if d.can_output), None)


def summary() -> Dict[str, Any]:
    devices = list_devices()
    mic = default_input()
    speaker = default_output()
    return {
        "provider": "sounddevice" if sounddevice_available() else ("winmm" if IS_WINDOWS else "none"),
        "count": len(devices),
        "inputs": [d.to_dict() for d in devices if d.can_input],
        "outputs": [d.to_dict() for d in devices if d.can_output],
        "default_input": mic.to_dict() if mic else None,
        "default_output": speaker.to_dict() if speaker else None,
        "microphone_present": mic is not None,
        "speaker_present": speaker is not None,
    }
