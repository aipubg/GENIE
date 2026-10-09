"""Media & Spotify control plugin.

Native integration first: playback control uses the **Windows media-key interface**
(`SendInput` with the media virtual keys), which the operating system routes to whichever
media session is active — Spotify, a browser tab, VLC, anything. No screen automation and no
coordinates are involved.

Observation (`current_track`, `state`) reads what is actually visible on the machine: the
window titles of known media applications. If nothing is playing or nothing can be observed,
the plugin says so instead of guessing.

This adapter runs in its own process (plugins/host.py) and has no access to GENIE's databases.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any, Dict, List, Optional

IS_WINDOWS = sys.platform.startswith("win")

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

MEDIA_APPS = {
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC",
    "musicbee.exe": "MusicBee",
    "foobar2000.exe": "foobar2000",
    "wmplayer.exe": "Windows Media Player",
    "groove.exe": "Groove",
    "msedge.exe": "Edge",
    "chrome.exe": "Chrome",
    "brave.exe": "Brave",
    "firefox.exe": "Firefox",
}

# Window titles that usually mean "a media page is open" in a browser
MEDIA_TITLE_HINTS = ("youtube", "spotify", "soundcloud", "music", "video", "song",
                     "playlist", "podcast")

if IS_WINDOWS:
    # The INPUT union must contain EVERY member type: its size is set by the largest member
    # (MOUSEINPUT). Declaring only the keyboard member makes ctypes.sizeof(INPUT) too small
    # and SendInput rejects the call outright.
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTunion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def _press_key(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32
    down = INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk)))
    up = INPUT(type=INPUT_KEYBOARD,
               u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)))
    array = (INPUT * 2)(down, up)
    return user32.SendInput(2, array, ctypes.sizeof(INPUT)) == 2


def _window_titles() -> List[Dict[str, str]]:
    """Visible top-level windows with their process name."""
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

    def process_name(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
        return ""

    out: List[Dict[str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            out.append({"title": title, "process": process_name(hwnd)})
        return True

    user32.EnumWindows(_callback, 0)
    return out


class Adapter:
    """Implements the capabilities declared in plugin.json."""

    def __init__(self, manifest, workspace=None):
        self.manifest = manifest
        self.workspace = workspace

    # ------------------------------------------------------------- lifecycle
    def initialize(self) -> Dict[str, Any]:
        return {"ok": True, "detail": "media control ready via the Windows media interface"}

    def health(self) -> Dict[str, Any]:
        observable = self._observable_sessions()
        return {
            "ok": IS_WINDOWS,
            "available": IS_WINDOWS,
            "detail": ("media-key interface available" if IS_WINDOWS
                       else "media control requires Windows"),
            "observable_media": observable,
        }

    def shutdown(self) -> None:
        return None

    # --------------------------------------------------------------- invoking
    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        name = capability.rsplit(".", 1)[-1]
        if name == "state":
            return self._state()
        if name == "current_track":
            return self._current_track()
        if name in ("play", "pause", "toggle", "next", "previous"):
            return self._control(name)
        if name == "volume_up":
            return self._key_result("volume_up", VK_VOLUME_UP)
        if name == "volume_down":
            return self._key_result("volume_down", VK_VOLUME_DOWN)
        if name == "mute":
            return self._key_result("mute", VK_VOLUME_MUTE)
        return self.unavailable(f"unknown capability {name}")

    # ---------------------------------------------------------------- control
    def _control(self, action: str) -> Dict[str, Any]:
        if not IS_WINDOWS:
            return self.unavailable("media control requires Windows")
        mapping = {
            "play": VK_MEDIA_PLAY_PAUSE,      # the OS toggles; GENIE verifies the result
            "pause": VK_MEDIA_PLAY_PAUSE,
            "toggle": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "previous": VK_MEDIA_PREV_TRACK,
        }
        before = self._observable_sessions()
        sent = _press_key(mapping[action])
        if not sent:
            return {"ok": False, "detail": "media key could not be sent",
                    "error": "SendInput failed", "error_code": "media_key_failed"}
        return {
            "ok": True,
            "action": action,
            "method": "windows-media-key",
            "observable_before": before,
            "verification": ("media key accepted by the OS; the active media session owns the "
                             "result, so the caller should observe state afterwards"),
            "note": ("play/pause share one media key — the OS decides based on current state"
                     if action in ("play", "pause") else ""),
        }

    def _key_result(self, action: str, vk: int) -> Dict[str, Any]:
        if not IS_WINDOWS:
            return self.unavailable("system audio control requires Windows")
        sent = _press_key(vk)
        return {"ok": sent, "action": action, "method": "system-key",
                "detail": f"{action} key sent" if sent else "key could not be sent",
                "error": "" if sent else "SendInput failed",
                "error_code": "" if sent else "key_failed"}

    # -------------------------------------------------------------- observation
    def _observable_sessions(self) -> List[Dict[str, str]]:
        sessions: List[Dict[str, str]] = []
        for window in _window_titles():
            process = window["process"]
            title = window["title"]
            app = MEDIA_APPS.get(process)
            if app:
                sessions.append({"app": app, "process": process, "title": title})
            elif process in ("chrome.exe", "brave.exe", "msedge.exe", "firefox.exe") and \
                    any(hint in title.lower() for hint in MEDIA_TITLE_HINTS):
                sessions.append({"app": f"{app or 'browser'}", "process": process,
                                 "title": title})
        return sessions

    def _current_track(self) -> Dict[str, Any]:
        sessions = [s for s in self._observable_sessions()
                    if s["app"] in ("Spotify", "VLC", "MusicBee", "foobar2000",
                                    "Windows Media Player")]
        browser_media = [s for s in self._observable_sessions()
                         if "browser" in s["app"].lower() or
                         s["process"] in ("chrome.exe", "brave.exe", "msedge.exe", "firefox.exe")]
        if sessions:
            best = sessions[0]
            title = best["title"]
            # Spotify's window title is "Artist - Track" (or "Spotify Free" when idle)
            track = title
            for suffix in (" - Spotify", " - VLC media player", "Spotify Premium", "Spotify Free"):
                track = track.replace(suffix, "")
            return {"ok": True, "app": best["app"], "track": track.strip(),
                    "raw_title": title, "source": "window-title"}
        if browser_media:
            best = browser_media[0]
            return {"ok": True, "app": best["app"], "track": best["title"],
                    "raw_title": best["title"], "source": "browser-window-title",
                    "note": "observed from a browser window; exact track metadata is not exposed"}
        return self.unavailable("no media application is currently playing or observable")

    def _state(self) -> Dict[str, Any]:
        sessions = self._observable_sessions()
        return {"ok": True, "observable_sessions": sessions, "count": len(sessions),
                "media_interface": "windows-media-keys",
                "detail": (f"{len(sessions)} media window(s) observed" if sessions
                           else "no media window observed")}
