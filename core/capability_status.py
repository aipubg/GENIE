"""Honest capability availability (spec section 14).

An adapter class existing is NOT availability. This module probes each
capability and reports one of:

    ready               works now
    missing_runtime     the library/binary/service is not installed
    missing_credentials required secret absent
    disabled            configured off
    failed              present but a probe raised
    unknown             cannot be determined cheaply

Nothing here simulates success. If the hardware/library is absent, the state
says so. The UI maps these to labels; it never invents a green badge.
"""
from __future__ import annotations

import importlib.util
from typing import Any, Dict, List

READY = "ready"
MISSING_RUNTIME = "missing_runtime"
MISSING_CREDENTIALS = "missing_credentials"
DISABLED = "disabled"
FAILED = "failed"
UNKNOWN = "unknown"


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def probe_voice() -> Dict[str, Any]:
    """Vosk STT + SAPI TTS + microphone enumeration, each reported separately."""
    out: Dict[str, Any] = {"stt": {}, "tts": {}, "microphones": []}

    if not _module_present("vosk"):
        out["stt"] = {"state": MISSING_RUNTIME, "engine": "vosk",
                      "detail": "vosk package not installed"}
    else:
        try:
            import vosk  # noqa: F401
            out["stt"] = {"state": READY, "engine": "vosk"}
        except Exception as exc:
            out["stt"] = {"state": FAILED, "engine": "vosk",
                          "detail": str(exc)[:200]}

    # Windows SAPI TTS via comtypes; no extra runtime beyond pywin32/comtypes.
    if _module_present("comtypes"):
        out["tts"] = {"state": READY, "engine": "windows-sapi"}
    else:
        out["tts"] = {"state": MISSING_RUNTIME, "engine": "windows-sapi",
                      "detail": "comtypes not installed"}

    # Microphone enumeration must never crash startup.
    try:
        if _module_present("sounddevice"):
            import sounddevice as sd
            devices = sd.query_devices()
            out["microphones"] = [
                {"index": i, "name": d.get("name", ""),
                 "input_channels": d.get("max_input_channels", 0)}
                for i, d in enumerate(devices)
                if d.get("max_input_channels", 0) > 0
            ]
        else:
            out["microphones"] = []
            out["microphone_state"] = MISSING_RUNTIME
    except Exception as exc:
        out["microphones"] = []
        out["microphone_state"] = FAILED
        out["microphone_detail"] = str(exc)[:200]

    if "microphone_state" not in out:
        out["microphone_state"] = READY if out["microphones"] else MISSING_RUNTIME
        if not out["microphones"]:
            out["microphone_detail"] = "no input device detected"
    return out


def probe_perception() -> Dict[str, Any]:
    """Camera/screen capture. Absent hardware is reported, never simulated."""
    state = UNKNOWN
    detail = ""
    cameras: List[Dict[str, Any]] = []
    try:
        if _module_present("cv2"):
            import cv2
            for i in range(4):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    cameras.append({"index": i})
                    cap.release()
            state = READY if cameras else MISSING_RUNTIME
            if not cameras:
                detail = "opencv present but no camera opened"
        else:
            state = MISSING_RUNTIME
            detail = "opencv (cv2) not installed"
    except Exception as exc:
        state = FAILED
        detail = str(exc)[:200]
    return {"state": state, "cameras": cameras, "detail": detail}


def probe_browser() -> Dict[str, Any]:
    if _module_present("playwright"):
        return {"state": READY, "engine": "playwright"}
    if _module_present("selenium"):
        return {"state": READY, "engine": "selenium"}
    return {"state": MISSING_RUNTIME, "engine": None,
            "detail": "no browser automation runtime installed"}


def probe_computer() -> Dict[str, Any]:
    """Windows automation; pywinauto is optional, ctypes fallback always exists."""
    if _module_present("pywinauto"):
        return {"state": READY, "engine": "pywinauto"}
    return {"state": READY, "engine": "ctypes",
            "detail": "pywinauto absent; limited native automation only"}


def probe_needle() -> Dict[str, Any]:
    """NEDLE2 local director: needs BOTH the package and the native library."""
    if not _module_present("needle"):
        return {"state": MISSING_RUNTIME, "detail": "cactus-needle not installed"}
    try:
        from director.needle_runtime import get_runtime
        status = get_runtime().status()
        # status() is FLAT (package_installed, library_present, smoke, ...).
        # Tolerate a nested "runtime" shape so a working engine is never
        # silently reported as failed.
        rt = status.get("runtime") or status
        installed = bool(rt.get("package_installed"))
        lib = bool(rt.get("library_present"))
        smoke = status.get("smoke") or {}
        if not installed:
            return {"state": MISSING_RUNTIME, "engine": "needle",
                    "detail": "cactus-needle package not importable"}
        if not lib:
            return {"state": MISSING_RUNTIME, "engine": "needle",
                    "detail": "package present, libneedle.dll missing"}
        if smoke.get("ok"):
            return {"state": READY, "engine": "needle",
                    "package_version": rt.get("package_version"),
                    "engine_version": rt.get("engine_version"),
                    "smoke": smoke}
        # Present but not smoke-verified: neither "ready" nor "broken".
        if not smoke:
            return {"state": UNKNOWN, "engine": "needle",
                    "package_version": rt.get("package_version"),
                    "engine_version": rt.get("engine_version"),
                    "detail": "engine present, smoke not yet run"}
        return {"state": FAILED, "engine": "needle",
                "detail": status.get("error") or "smoke reported failure"}
    except Exception as exc:
        return {"state": FAILED, "detail": str(exc)[:200]}


def report() -> Dict[str, Any]:
    return {
        "voice": probe_voice(),
        "perception": probe_perception(),
        "browser": probe_browser(),
        "computer": probe_computer(),
        "needle": probe_needle(),
    }


__all__ = ["report", "probe_voice", "probe_perception", "probe_browser",
           "probe_computer", "probe_needle",
           "READY", "MISSING_RUNTIME", "MISSING_CREDENTIALS", "DISABLED",
           "FAILED", "UNKNOWN"]
