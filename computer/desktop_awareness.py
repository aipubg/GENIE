"""Desktop Awareness service (computer/desktop_awareness).

Observes every connected Windows display authorized by the owner, tracks windows
and applications, detects display configuration changes, and provides bounded
screenshot capture on demand.

Design principles:
- Reuses existing windows_api, state, uia — no second computer authority.
- Lightweight metadata polling; rolling screenshots require local_preview opt-in.
- Respects owner pause/resume, exclusions, and lock-screen state.
- Clears temporary image buffers on pause or shutdown.
- Never steals focus or interrupts the owner's work.
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.events import get_bus
from core.logging_setup import get_logger

from . import state, uia, windows_api as win

log = get_logger("computer.desktop_awareness")

DEFAULT_POLL_INTERVAL_S = 3.0
MIN_POLL_INTERVAL_S = 1.0
MAX_POLL_INTERVAL_S = 30.0

# Common sensitive window class names / process names to exclude by default
_DEFAULT_EXCLUSIONS = {
    "process": {"1password", "bitwarden", "keepass", "lastpass",
                "authy", "microsoftauthexe"},
    "class": {"#32770"},  # generic dialog; owners may add more
    "title": {"password", "credit card", "ssn", "social security"},
}


# ------------------------------------------------------------------ settings
class DesktopSettings:
    """Persistent owner preferences for desktop awareness."""

    def __init__(self, data_dir: Optional[str] = None):
        root = Path(data_dir or os.environ.get("GENIE_DATA_DIR", "."))
        self._path = root / "desktop_awareness.json"
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
                return
            except Exception as exc:
                log.debug("settings load failed: %s", exc)
        self._data = {
            # The owner explicitly asked for a continuously aware assistant.
            # Metadata and a local redacted preview are safe defaults; sending
            # pixels to a remote provider still requires remote_visual_consent.
            "enabled": True,
            "auto_start": True,
            "authorized_displays": [],  # empty = all
            "excluded_processes": list(_DEFAULT_EXCLUSIONS["process"]),
            "excluded_classes": list(_DEFAULT_EXCLUSIONS["class"]),
            "excluded_title_fragments": list(_DEFAULT_EXCLUSIONS["title"]),
            "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
            "remote_visual_consent": False,
            "local_preview": True,
        }
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            log.debug("settings save failed: %s", exc)

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("enabled", False))

    @enabled.setter
    def enabled(self, v: bool) -> None:
        self._data["enabled"] = bool(v)
        self._save()

    @property
    def auto_start(self) -> bool:
        return bool(self._data.get("auto_start", False))

    @auto_start.setter
    def auto_start(self, v: bool) -> None:
        self._data["auto_start"] = bool(v)
        self._save()

    @property
    def authorized_displays(self) -> List[int]:
        return list(self._data.get("authorized_displays", []))

    @authorized_displays.setter
    def authorized_displays(self, v: List[int]) -> None:
        self._data["authorized_displays"] = list(v)
        self._save()

    @property
    def excluded_processes(self) -> Set[str]:
        return set(self._data.get("excluded_processes", []))

    @excluded_processes.setter
    def excluded_processes(self, v: Set[str]) -> None:
        self._data["excluded_processes"] = list(v)
        self._save()

    @property
    def excluded_classes(self) -> Set[str]:
        return set(self._data.get("excluded_classes", []))

    @excluded_classes.setter
    def excluded_classes(self, v: Set[str]) -> None:
        self._data["excluded_classes"] = list(v)
        self._save()

    @property
    def excluded_title_fragments(self) -> Set[str]:
        return set(self._data.get("excluded_title_fragments", []))

    @excluded_title_fragments.setter
    def excluded_title_fragments(self, v: Set[str]) -> None:
        self._data["excluded_title_fragments"] = list(v)
        self._save()

    @property
    def poll_interval_s(self) -> float:
        return max(MIN_POLL_INTERVAL_S, min(MAX_POLL_INTERVAL_S,
                                            float(self._data.get("poll_interval_s",
                                                                 DEFAULT_POLL_INTERVAL_S))))

    @poll_interval_s.setter
    def poll_interval_s(self, v: float) -> None:
        self._data["poll_interval_s"] = max(MIN_POLL_INTERVAL_S, min(MAX_POLL_INTERVAL_S, float(v)))
        self._save()

    @property
    def remote_visual_consent(self) -> bool:
        return bool(self._data.get("remote_visual_consent", False))

    @remote_visual_consent.setter
    def remote_visual_consent(self, v: bool) -> None:
        self._data["remote_visual_consent"] = bool(v)
        self._save()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def update(self, changes: Dict[str, Any]) -> None:
        if not isinstance(changes, dict):
            raise ValueError("desktop settings must be an object")
        boolean_keys = {"enabled", "auto_start", "remote_visual_consent", "local_preview"}
        lists = {"authorized_displays", "excluded_processes", "excluded_classes",
                 "excluded_title_fragments"}
        for key, value in changes.items():
            if key in boolean_keys:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif key in lists:
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list")
                if key == "authorized_displays":
                    if any(type(v) is not int or v < 0 for v in value):
                        raise ValueError("display indices must be non-negative integers")
                elif any(not isinstance(v, str) or not v.strip() for v in value):
                    raise ValueError(f"{key} must contain non-empty strings")
            elif key == "poll_interval_s":
                if type(value) not in (int, float) or not MIN_POLL_INTERVAL_S <= value <= MAX_POLL_INTERVAL_S:
                    raise ValueError("poll interval must be between 1 and 30 seconds")
            else:
                raise ValueError(f"unknown desktop setting: {key}")
        self._data.update(changes)
        self._save()


# ------------------------------------------------------------------ lock detection
_UOI_NAME = 2


def _is_desktop_locked() -> bool:
    """Return True when the Windows session is locked (Winlogon desktop)."""
    if not win.IS_WINDOWS:
        return False
    try:
        from ctypes import wintypes
        win.user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        win.user32.OpenInputDesktop.restype = wintypes.HANDLE
        win.user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        win.user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        desktop = win.user32.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return True
        try:
            buf = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_ulong(0)
            if win.user32.GetUserObjectInformationW(desktop, _UOI_NAME, buf, ctypes.sizeof(buf),
                                                     ctypes.byref(needed)):
                return buf.value != "Default"
        finally:
            win.user32.CloseDesktop(desktop)
    except Exception:
        pass
    return True


# ------------------------------------------------------------------ observation
@dataclass
class DisplayObservation:
    index: int
    hmon: int
    rect: tuple
    work_rect: tuple
    primary: bool
    dpi_scale: float
    windows: List[Dict[str, Any]] = field(default_factory=list)
    foreground_on_display: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index, "hmon": self.hmon,
            "rect": list(self.rect), "work_rect": list(self.work_rect),
            "primary": self.primary, "dpi_scale": self.dpi_scale,
            "windows": self.windows,
            "foreground_on_display": self.foreground_on_display,
        }


@dataclass
class DesktopObservation:
    ts: float
    displays: List[DisplayObservation]
    foreground: Optional[Dict[str, Any]]
    cursor: tuple
    locked: bool
    paused: bool
    display_count: int
    window_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "displays": [d.to_dict() for d in self.displays],
            "foreground": self.foreground,
            "cursor": list(self.cursor),
            "locked": self.locked,
            "paused": self.paused,
            "display_count": self.display_count,
            "window_count": self.window_count,
        }


class DesktopAwareness:
    """Centralized desktop observation service."""

    def __init__(self, data_dir: Optional[str] = None):
        self.settings = DesktopSettings(data_dir)
        self._bus = get_bus()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = False
        self._last_observation: Optional[DesktopObservation] = None
        self._last_display_signature: str = ""
        self._temp_buffer_dir = Path(data_dir or os.environ.get("GENIE_DATA_DIR", ".")) / "desktop_buffer"
        # auto-start if configured
        if self.settings.enabled and self.settings.auto_start:
            self.start()

    # -------------------------------------------------------------- lifecycle
    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "detail": "already running"}
            self._stop_event.clear()
            self._paused = False
            self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                            name="desktop-awareness")
            self._thread.start()
            return {"ok": True, "detail": "started"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        with self._lock:
            if thread is None or not thread.is_alive():
                self._thread = None
            self._last_observation = None
            self._clear_buffers()
        return {"ok": not self.is_running, "detail": "stopped" if not self.is_running else "stopping"}

    def configure(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        self.settings.update(changes)
        if self.settings.enabled:
            self.start()
        else:
            self.stop()
        if not self.settings.to_dict().get("local_preview"):
            self._clear_buffers()
        return {"ok": True, "updated": list(changes), "status": self.status,
                "settings": self.settings.to_dict()}

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            self._paused = True
            self._last_observation = None
            self._clear_buffers()
            return {"ok": True, "detail": "paused"}

    def resume(self) -> Dict[str, Any]:
        with self._lock:
            self._paused = False
        if self.settings.enabled:
            self.start()
        return {"ok": True, "detail": "resumed"}

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def status(self) -> Dict[str, Any]:
        with self._lock:
            obs = self._last_observation
            return {
                "running": self.is_running,
                "paused": self._paused,
                "enabled": self.settings.enabled,
                "auto_start": self.settings.auto_start,
                "locked": obs.locked if obs else False,
                "display_count": obs.display_count if obs else 0,
                "window_count": obs.window_count if obs else 0,
                "poll_interval_s": self.settings.poll_interval_s,
                "remote_visual_consent": self.settings.remote_visual_consent,
                "local_preview": bool(self.settings.to_dict().get("local_preview", False)),
                "displays": [{"index": d.index, "primary": d.primary} for d in obs.displays] if obs else [],
            }

    # -------------------------------------------------------------- observation
    def observe(self) -> DesktopObservation:
        """Return a full desktop observation. Lightweight — no screenshots."""
        with self._lock:
            paused = self._paused
        locked = _is_desktop_locked()
        if locked or paused:
            # honest: report locked, do not attempt to enumerate protected desktop
            obs = DesktopObservation(
                ts=time.time(), displays=[], foreground=None,
                cursor=(0, 0), locked=locked, paused=paused,
                display_count=0, window_count=0)
            with self._lock:
                self._clear_buffers()
                self._last_observation = obs
            return obs

        monitors = win.list_monitors()
        windows = win.list_windows(visible_only=True)
        fg = win.foreground_window()
        auth = self.settings.authorized_displays
        if auth:
            monitors = [m for m in monitors if m.index in auth]

        excluded_procs = self.settings.excluded_processes
        excluded_classes = self.settings.excluded_classes
        excluded_titles = self.settings.excluded_title_fragments

        def _allowed(w: win.WindowInfo) -> bool:
            pname = w.process.lower()
            if any(p in pname for p in excluded_procs):
                return False
            cname = w.class_name.lower()
            if any(c in cname for c in excluded_classes):
                return False
            tlower = w.title.lower()
            if any(t in tlower for t in excluded_titles):
                return False
            return True

        indices = {m.index for m in monitors}
        allowed_windows = [w for w in windows if _allowed(w) and w.monitor in indices]
        if fg and (not _allowed(fg) or fg.monitor not in indices):
            fg = None

        displays: List[DisplayObservation] = []
        for m in monitors:
            disp_windows = [w.to_dict() for w in allowed_windows if w.monitor == m.index]
            displays.append(DisplayObservation(
                index=m.index, hmon=m.hmon,
                rect=m.rect, work_rect=m.work_rect,
                primary=m.primary, dpi_scale=m.dpi_scale,
                windows=disp_windows,
                foreground_on_display=(fg is not None and fg.monitor == m.index)))

        obs = DesktopObservation(
            ts=time.time(), displays=displays,
            foreground=fg.to_dict() if fg else None,
            cursor=win.cursor_pos(), locked=False, paused=paused,
            display_count=len(monitors), window_count=len(allowed_windows))

        with self._lock:
            self._last_observation = obs
        return obs

    def observe_display(self, index: int) -> Dict[str, Any]:
        """Observe a specific display by index."""
        obs = self.observe()
        for d in obs.displays:
            if d.index == index:
                return {"ok": True, **d.to_dict()}
        return {"ok": False, "error": f"display {index} not found or not authorized"}

    def list_displays(self) -> Dict[str, Any]:
        """List all authorized displays with metadata."""
        obs = self.observe()
        return {
            "ok": True,
            "displays": [d.to_dict() for d in obs.displays],
            "count": len(obs.displays),
            "virtual_screen": list(win.virtual_screen_rect()),
        }

    def _excluded_visible_windows(self, monitor_index: int) -> List[Dict[str, Any]]:
        """Windows on this monitor whose pixels must never appear in a capture."""
        excluded_procs = self.settings.excluded_processes
        excluded_classes = self.settings.excluded_classes
        excluded_titles = self.settings.excluded_title_fragments
        hits: List[Dict[str, Any]] = []
        for w in win.list_windows(visible_only=True):
            # A sensitive window can straddle displays; clip its rectangle
            # against the captured monitor below instead of using its anchor.
            pname = (w.process or "").lower()
            cname = (w.class_name or "").lower()
            tlower = (w.title or "").lower()
            if (any(p in pname for p in excluded_procs)
                    or any(c in cname for c in excluded_classes)
                    or any(t in tlower for t in excluded_titles)):
                hits.append({"hwnd": w.hwnd, "title": w.title,
                             "process": w.process, "rect": list(w.rect)})
        return hits

    def capture_display(self, index: int, out_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture a screenshot of a specific display. Requires explicit request.

        Sensitive-content guarantee: every visible window matching the owner's
        exclusions is REDACTED from the actual captured pixels (black-boxed in
        the BMP) before the file is returned, and the receipt lists what was
        redacted. Exclusions therefore hold for images, not just for metadata.
        """
        if _is_desktop_locked():
            return {"ok": False, "error": "desktop is locked"}
        if self.is_paused:
            return {"ok": False, "error": "desktop awareness is paused"}
        auth = self.settings.authorized_displays
        if auth and index not in auth:
            return {"ok": False, "error": f"display {index} is not authorized"}
        self._temp_buffer_dir.mkdir(parents=True, exist_ok=True)
        if out_path is None:
            self._temp_buffer_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(self._temp_buffer_dir / f"display_{index}_{int(time.time())}.bmp")
        result = win.capture_monitor(index, out_path)
        if not result.get("ok"):
            return result
        # redact excluded windows from the real pixels
        monitor = next((m for m in win.list_monitors() if m.index == index), None)
        if monitor is None:
            Path(out_path).unlink(missing_ok=True)
            return {"ok": False, "error": "display configuration changed during capture"}
        mx, my = monitor.rect[0], monitor.rect[1]
        rects = []
        for w in self._excluded_visible_windows(index):
            wx1, wy1, wx2, wy2 = w["rect"]
            ix1, iy1 = max(wx1 - mx, 0), max(wy1 - my, 0)
            ix2, iy2 = min(wx2 - mx, result["width"]), min(wy2 - my, result["height"])
            if ix2 > ix1 and iy2 > iy1:
                rects.append((ix1, iy1, ix2, iy2))
        redacted = _redact_bmp_rects(out_path, rects) if rects else 0
        if rects and redacted != len(rects):
            Path(out_path).unlink(missing_ok=True)
            return {"ok": False, "error": "sensitive windows could not be redacted"}
        if redacted:
            result["redacted_windows"] = redacted
        return result


    # -------------------------------------------------------------- polling loop
    def _poll_loop(self) -> None:
        """Background thread: lightweight metadata polling with change detection."""
        while not self._stop_event.is_set():
            try:
                interval = self.settings.poll_interval_s
                # clamp dynamic interval when paused
                if self.is_paused:
                    self._stop_event.wait(min(interval, 5.0))
                    continue

                # detect display changes
                monitors = win.list_monitors()
                sig = "|".join(f"{m.hmon}:{m.rect}:{m.dpi_scale}" for m in monitors)
                with self._lock:
                    changed = sig != self._last_display_signature
                    if changed:
                        self._last_display_signature = sig

                if changed:
                    self._bus.publish("DISPLAY_CONFIG_CHANGED",
                                      {"count": len(monitors),
                                       "displays": [m.to_dict() for m in monitors]})

                # take a lightweight observation for caching
                obs = self.observe()
                if self.settings.enabled and self.settings.to_dict().get("local_preview"):
                    for display in obs.displays:
                        with self._lock:
                            if self._paused or self._stop_event.is_set():
                                break
                            self.capture_display(display.index, str(self._temp_buffer_dir /
                                f"preview_{display.index}.bmp"))

            except Exception as exc:
                log.debug("desktop awareness poll error: %s", exc)

            # wait with early exit on stop
            if self._stop_event.wait(timeout=interval):
                break

    def _clear_buffers(self) -> None:
        """Remove temporary screenshot buffers."""
        try:
            if self._temp_buffer_dir.exists():
                for f in self._temp_buffer_dir.glob("*.bmp"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("buffer clear error: %s", exc)

    def preview_frame(self, index: int) -> bytes:
        with self._lock:
            if (not self.settings.enabled or self._paused or _is_desktop_locked()
                    or not self.settings.to_dict().get("local_preview") or not self.is_running):
                self._clear_buffers()
                return b""
            auth = self.settings.authorized_displays
            if auth and index not in auth:
                return b""
            path = self._temp_buffer_dir / f"preview_{index}.bmp"
            if not path.exists() or time.time() - path.stat().st_mtime > self.settings.poll_interval_s * 2 + 5:
                return b""
            return path.read_bytes()
# ------------------------------------------------------------------ redaction
def _redact_bmp_rects(bmp_path: str, rects: List[tuple]) -> int:
    """Zero out pixel rects inside a 24-bit top-down BMP written by
    windows_api.capture_region. Returns the number of rects redacted.

    This makes exclusions apply to the ACTUAL captured pixels, not only to
    the window metadata lists: an excluded window's content can never leak
    through a screenshot.
    """
    import struct
    p = Path(bmp_path)
    if not p.exists() or not rects:
        return 0
    raw = bytearray(p.read_bytes())
    if len(raw) < 54 or raw[:2] != b"BM":
        return 0
    width = struct.unpack_from("<i", raw, 18)[0]
    height_signed = struct.unpack_from("<i", raw, 22)[0]
    height = abs(height_signed)
    bpp = struct.unpack_from("<H", raw, 14 + 14)[0]  # biBitCount
    if bpp != 24 or width <= 0 or height <= 0:
        return 0
    row_stride = (width * 3 + 3) & ~3
    top_down = height_signed < 0
    zero = b"\x00"
    for (rx1, ry1, rx2, ry2) in rects:
        # clip to image bounds, in image space (y down from top)
        x1, x2 = max(0, rx1), min(width, rx2)
        y1, y2 = max(0, ry1), min(height, ry2)
        if x1 >= x2 or y1 >= y2:
            continue
        for y in range(y1, y2):
            row = y if top_down else (height - 1 - y)
            base = 54 + row * row_stride
            s = base + x1 * 3
            e = base + x2 * 3
            if e <= len(raw):
                raw[base + x1 * 3: e] = b"\x00" * (x2 - x1) * 3
    p.write_bytes(bytes(raw))
    return len(rects)
