"""Win32 layer (computer/windows_api).

Thin, dependency-free ctypes wrappers over the real Windows APIs used by the computer
engine: windows, monitors, processes, input (SendInput), clipboard, idle detection and
GDI screen capture.

Nothing here makes policy decisions — the executor/verifier do that. All functions are
safe to call on non-Windows (they report `available=False`) so the test-suite and CI can
import them anywhere.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
else:  # pragma: no cover - non-Windows dev boxes
    user32 = kernel32 = gdi32 = shell32 = ole32 = None  # type: ignore


# --------------------------------------------------------------------------- types
@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process: str = ""
    visible: bool = True
    minimized: bool = False
    maximized: bool = False
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    monitor: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"hwnd": self.hwnd, "title": self.title, "class_name": self.class_name,
                "pid": self.pid, "process": self.process, "visible": self.visible,
                "minimized": self.minimized, "maximized": self.maximized,
                "rect": list(self.rect), "monitor": self.monitor,
                "presentation": "shell_surface" if self.class_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
                else "minimized" if self.minimized else "visible_window" if self.visible else "hidden_window"}


@dataclass
class MonitorInfo:
    index: int
    hmon: int
    rect: Tuple[int, int, int, int]
    work_rect: Tuple[int, int, int, int]
    primary: bool
    dpi_scale: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "hmon": self.hmon,
                "rect": list(self.rect), "work_rect": list(self.work_rect),
                "primary": self.primary, "dpi_scale": self.dpi_scale}


# --------------------------------------------------------------------------- window
def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return ""


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def application_user_model_id(pid: int) -> str:
    """Read packaged application identity, not a guessed executable or title."""
    if not IS_WINDOWS:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD()
        if kernel32.GetApplicationUserModelId(handle, ctypes.byref(size), None) != 122:
            return ""
        if not 0 < size.value <= 4096:
            return ""
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.GetApplicationUserModelId(handle, ctypes.byref(size), buffer) == 0:
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _rect(hwnd: int) -> Tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def list_windows(visible_only: bool = True) -> List[WindowInfo]:
    if not IS_WINDOWS:
        return []
    out: List[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        # Exceptions must not escape a ctypes callback into EnumWindows; doing
        # so raises an unraisable-exception warning and can abort enumeration.
        try:
            if visible_only and not user32.IsWindowVisible(hwnd):
                return True
            title = _window_title(hwnd)
            if visible_only and not title:
                return True
            pid = _pid_of(hwnd)
            out.append(WindowInfo(
                hwnd=hwnd, title=title, class_name=_class_name(hwnd), pid=pid,
                process=_process_name(pid),
                visible=bool(user32.IsWindowVisible(hwnd)),
                minimized=bool(user32.IsIconic(hwnd)),
                maximized=bool(user32.IsZoomed(hwnd)),
                rect=_rect(hwnd),
                monitor=monitor_index_for_window(hwnd),
            ))
        except Exception:
            return True
        return True

    user32.EnumWindows(_cb, 0)
    return out


def foreground_window() -> Optional[WindowInfo]:
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = _pid_of(hwnd)
    return WindowInfo(hwnd=hwnd, title=_window_title(hwnd), class_name=_class_name(hwnd),
                      pid=pid, process=_process_name(pid),
                      visible=bool(user32.IsWindowVisible(hwnd)),
                      minimized=bool(user32.IsIconic(hwnd)),
                      maximized=bool(user32.IsZoomed(hwnd)),
                      rect=_rect(hwnd), monitor=monitor_index_for_window(hwnd))


def find_windows(title_contains: str = "", process: str = "", class_name: str = "",
                 visible_only: bool = True) -> List[WindowInfo]:
    tl = (title_contains or "").lower()
    pl = (process or "").lower()
    cl = (class_name or "").lower()
    result = []
    for w in list_windows(visible_only=visible_only):
        if tl and tl not in w.title.lower():
            continue
        if pl and pl not in w.process.lower():
            continue
        if cl and cl not in w.class_name.lower():
            continue
        result.append(w)
    return result


def focus_window(hwnd: int) -> bool:
    """Bring a window to the foreground.

    Windows refuses foreground changes from background processes (foreground lock). We try
    the standard ladder, cheapest first, and verify by reading the actual foreground window:
      1. AttachThreadInput + SetForegroundWindow (the documented approach)
      2. a synthetic ALT press, which satisfies the foreground lock
      3. SwitchToThisWindow (shell-level activation)
    """
    if not IS_WINDOWS or not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE

    def _is_foreground() -> bool:
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)

    if _is_foreground():
        return True

    fg = user32.GetForegroundWindow()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    attached = False
    if target_thread and target_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, target_thread, False)
    if _is_foreground():
        return True

    # 2. ALT press unlocks SetForegroundWindow for the calling thread
    _keybd_scancode("alt", 1)
    user32.SetForegroundWindow(hwnd)
    if _is_foreground():
        return True

    # 3. shell activation
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    return _is_foreground()


def show_window(hwnd: int, command: str) -> bool:
    """command: restore|minimize|maximize|hide|show"""
    codes = {"hide": 0, "show": 5, "restore": 9, "minimize": 6, "maximize": 3}
    if command not in codes or not IS_WINDOWS:
        return False
    return bool(user32.ShowWindow(hwnd, codes[command]))


_SHELL_WINDOW_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
})


def minimize_all_application_windows() -> Dict[str, Any]:
    """Minimize visible app windows while leaving desktop and taskbar surfaces alone."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "Windows window management is unavailable on this OS."}
    before = list_windows(visible_only=True)
    targets = [w for w in before if w.visible and not w.minimized
               and w.class_name not in _SHELL_WINDOW_CLASSES]
    minimized, failed = [], []
    for window in targets:
        if show_window(int(window.hwnd), "minimize"):
            minimized.append(window)
        else:
            failed.append(window)
    return {
        "ok": not failed,
        "target_hwnds": [int(w.hwnd) for w in targets],
        "minimized_hwnds": [int(w.hwnd) for w in minimized],
        "failed_hwnds": [int(w.hwnd) for w in failed],
        "skipped_shell_hwnds": [int(w.hwnd) for w in before
                                 if w.class_name in _SHELL_WINDOW_CLASSES],
        "targets": [{"hwnd": int(w.hwnd), "pid": int(w.pid), "process": w.process,
                     "class_name": w.class_name, "title": w.title} for w in targets],
        "error": "One or more app windows could not be minimized." if failed else "",
    }


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    if not IS_WINDOWS:
        return False
    SWP_NOZORDER, SWP_SHOWWINDOW = 0x0004, 0x0040
    return bool(user32.SetWindowPos(hwnd, 0, x, y, width, height,
                                    SWP_NOZORDER | SWP_SHOWWINDOW))


def close_window(hwnd: int) -> bool:
    if not IS_WINDOWS:
        return False
    WM_CLOSE = 0x0010
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return True


def is_window(hwnd: int) -> bool:
    return bool(IS_WINDOWS and user32.IsWindow(hwnd))


# ------------------------------------------------------------------------- monitors
def list_monitors() -> List[MonitorInfo]:
    if not IS_WINDOWS:
        return []
    monitors: List[MonitorInfo] = []

    # Optional: per-monitor DPI (Windows 8.1+)
    _dpi_x = ctypes.c_uint()
    _dpi_y = ctypes.c_uint()
    _get_dpi = None
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_uint),
                                            ctypes.POINTER(ctypes.c_uint)]
        shcore.GetDpiForMonitor.restype = ctypes.c_long
        _get_dpi = shcore.GetDpiForMonitor
    except Exception:
        pass

    # MONITORINFO for primary flag and work area
    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    MONITORINFOF_PRIMARY = 0x00000001

    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(wintypes.RECT), ctypes.c_double)

    def _cb(hmon, _hdc, lprect, _data):
        r = lprect.contents
        primary = False
        work = (r.left, r.top, r.right, r.bottom)
        try:
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(int(hmon), ctypes.byref(mi)):
                primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)
                work = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
        except Exception:
            pass
        dpi = 96.0
        if _get_dpi:
            try:
                if _get_dpi(int(hmon), 0, ctypes.byref(_dpi_x), ctypes.byref(_dpi_y)) == 0:
                    dpi = float(_dpi_x.value)
            except Exception:
                pass
        monitors.append(MonitorInfo(index=len(monitors), hmon=int(hmon),
                                    rect=(r.left, r.top, r.right, r.bottom),
                                    work_rect=work, primary=primary,
                                    dpi_scale=round(dpi / 96.0, 2)))
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_cb), 0)
    return monitors


def monitor_index_for_window(hwnd: int) -> int:
    if not IS_WINDOWS:
        return 0
    MONITOR_DEFAULTTONEAREST = 2
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    monitors = list_monitors()
    if hmon:
        for m in monitors:
            if m.hmon == int(hmon):
                return m.index
    # fallback: centre-point containment
    r = _rect(hwnd)
    cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
    for m in monitors:
        x1, y1, x2, y2 = m.rect
        if x1 <= cx < x2 and y1 <= cy < y2:
            return m.index
    return 0


def virtual_screen_rect() -> Tuple[int, int, int, int]:
    if not IS_WINDOWS:
        return (0, 0, 0, 0)
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


# ---------------------------------------------------------------------------- input
# ------------------------------------------------------------------ SendInput structs
if IS_WINDOWS:
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


INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000

VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "volume_up": 0xAF, "volume_down": 0xAE, "volume_mute": 0xAD,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
}


def _send_inputs(inputs: List["INPUT"]) -> bool:
    if not IS_WINDOWS or not inputs:
        return False
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    # pass the array itself: ctypes converts it to POINTER(INPUT); byref() would give a
    # pointer-to-array and SendInput rejects it
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    return sent == n


def resolve_vk(key: str) -> Optional[int]:
    """Resolve a key name ('enter', 'ctrl', 'v', 'f5') to a virtual key code."""
    if not key:
        return None
    k = key.strip().lower()
    if k in VK:
        return VK[k]
    if len(k) == 1 and (k.isalnum()):
        return ord(k.upper())
    return None


def key_press(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    down = INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk)))
    up = INPUT(type=INPUT_KEYBOARD,
               u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)))
    return _send_inputs([down, up])


def type_text(text: str, per_char_delay: float = 0.0) -> bool:
    """Type exact text using unicode scancodes (keyboard-layout independent)."""
    if not IS_WINDOWS:
        return False
    ok = True
    for ch in text:
        code = ord(ch)
        down = INPUT(type=INPUT_KEYBOARD,
                     u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE)))
        up = INPUT(type=INPUT_KEYBOARD,
                   u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=code,
                                               dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)))
        ok = _send_inputs([down, up]) and ok
        if per_char_delay:
            time.sleep(per_char_delay)
    return ok


def _keybd_scancode(name: str, times: int = 1) -> bool:
    """Press a named virtual key N times (used as the audio fallback path)."""
    vk = resolve_vk(name)
    if vk is None:
        return False
    ok = True
    for _ in range(max(1, int(times))):
        ok = key_press(vk) and ok
    return ok


def _absolute_coords(x: int, y: int) -> Tuple[int, int]:
    vx, vy, vw, vh = virtual_screen_rect()
    if vw <= 0 or vh <= 0:
        return x, y
    nx = int(round((x - vx) * 65535 / (vw - 1)))
    ny = int(round((y - vy) * 65535 / (vh - 1)))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def mouse_move(x: int, y: int) -> bool:
    if not IS_WINDOWS:
        return False
    nx, ny = _absolute_coords(x, y)
    mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                    dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    return _send_inputs([INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=mi))])


def mouse_click(x: Optional[int] = None, y: Optional[int] = None,
                button: str = "left", double: bool = False) -> bool:
    if not IS_WINDOWS:
        return False
    if x is not None and y is not None:
        mouse_move(x, y)
        time.sleep(0.05)
    down_f = MOUSEEVENTF_RIGHTDOWN if button == "right" else MOUSEEVENTF_LEFTDOWN
    up_f = MOUSEEVENTF_RIGHTUP if button == "right" else MOUSEEVENTF_LEFTUP
    seq = [down_f, up_f] * (2 if double else 1)
    inputs = [INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=MOUSEINPUT(dwFlags=f))) for f in seq]
    return _send_inputs(inputs)


def mouse_wheel(delta: int) -> bool:
    if not IS_WINDOWS:
        return False
    mi = MOUSEINPUT(dx=0, dy=0, mouseData=ctypes.c_ulong(delta & 0xFFFFFFFF).value,
                    dwFlags=MOUSEEVENTF_WHEEL)
    return _send_inputs([INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=mi))])


def cursor_pos() -> Tuple[int, int]:
    if not IS_WINDOWS:
        return (0, 0)
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


# --------------------------------------------------------------- idle / takeover
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def last_input_tick() -> int:
    """Millisecond tick of the last real user input (mouse or keyboard)."""
    if not IS_WINDOWS:
        return 0
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return int(info.dwTime)


def idle_ms() -> int:
    if not IS_WINDOWS:
        return 0
    return int(kernel32.GetTickCount()) - last_input_tick()


# ------------------------------------------------------------------------ clipboard
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def clipboard_get_text() -> str:
    if not IS_WINDOWS:
        return ""
    if not user32.OpenClipboard(None):
        return ""
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set_text(text: str) -> bool:
    if not IS_WINDOWS:
        return False
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        buf_size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, buf_size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), buf_size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        return clipboard_get_text() == text          # verify immediately
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------- screen capture
def capture_region(x: int, y: int, width: int, height: int,
                   out_path: str | Path) -> Dict[str, Any]:
    """Capture a screen region to a 24-bit BMP using GDI (no third-party deps)."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    width, height = max(1, int(width)), max(1, int(height))
    hwnd_desktop = user32.GetDesktopWindow()
    hdc_screen = user32.GetWindowDC(hwnd_desktop)
    if not hdc_screen:
        return {"ok": False, "error": "GetWindowDC failed"}
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        user32.ReleaseDC(hwnd_desktop, hdc_screen)
        return {"ok": False, "error": "CreateCompatibleDC failed"}
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    if not hbmp:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd_desktop, hdc_screen)
        return {"ok": False, "error": "CreateCompatibleBitmap failed"}
    old_object = gdi32.SelectObject(hdc_mem, hbmp)
    SRCCOPY = 0x00CC0020

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height          # top-down
    bi.biPlanes = 1
    bi.biBitCount = 24
    bi.biCompression = 0           # BI_RGB

    row_bytes = (width * 3 + 3) & ~3
    buf = ctypes.create_string_buffer(row_bytes * height)
    DIB_RGB_COLORS = 0
    try:
        if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, x, y, SRCCOPY):
            return {"ok": False, "error": "BitBlt failed"}
        got = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf,
                              ctypes.byref(bi), DIB_RGB_COLORS)
    finally:
        # Restore the DC before deleting the bitmap. Repeated capture cycles
        # otherwise leak a selected GDI object and eventually destabilize the
        # embedded backend process.
        if old_object:
            gdi32.SelectObject(hdc_mem, old_object)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd_desktop, hdc_screen)

    if not got:
        return {"ok": False, "error": "GetDIBits failed"}

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        file_size = 14 + 40 + len(buf.raw)
        fh.write(b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54))
        fh.write(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0,
                             len(buf.raw), 2835, 2835, 0, 0))
        fh.write(buf.raw)
    return {"ok": True, "path": str(out), "width": width, "height": height,
            "bytes": file_size}


def capture_window(hwnd: int, out_path: str | Path) -> Dict[str, Any]:
    r = _rect(hwnd)
    return capture_region(r[0], r[1], r[2] - r[0], r[3] - r[1], out_path)


def capture_screen(out_path: str | Path) -> Dict[str, Any]:
    x, y, w, h = virtual_screen_rect()
    return capture_region(x, y, w, h, out_path)


def capture_monitor(index: int, out_path: str | Path) -> Dict[str, Any]:
    """Capture the full bounds of a specific monitor by index."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    monitors = list_monitors()
    if not monitors:
        return {"ok": False, "error": "no monitors detected"}
    if index < 0 or index >= len(monitors):
        return {"ok": False, "error": f"monitor index {index} out of range (0-{len(monitors)-1})"}
    m = monitors[index]
    x1, y1, x2, y2 = m.rect
    return capture_region(x1, y1, x2 - x1, y2 - y1, out_path)


def available() -> bool:
    return bool(IS_WINDOWS)


# ------------------------------------------------------------------ prototypes
# On 64-bit Python, ctypes truncates unknown return values to 32-bit ints. Handle and
# pointer-returning APIs MUST declare restype/argtypes or they silently corrupt data
# (this caused a real access violation in clipboard_set_text before prototypes existed).
def _install_prototypes() -> None:
    if not IS_WINDOWS:
        return
    HWND, HANDLE, BOOL, DWORD, UINT = (wintypes.HWND, wintypes.HANDLE, wintypes.BOOL,
                                       wintypes.DWORD, wintypes.UINT)
    LPVOID, LPCWSTR, LPWSTR = wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPWSTR

    # user32
    user32.OpenClipboard.argtypes = [HWND]
    user32.OpenClipboard.restype = BOOL
    user32.CloseClipboard.restype = BOOL
    user32.EmptyClipboard.restype = BOOL
    user32.GetClipboardData.argtypes = [UINT]
    user32.GetClipboardData.restype = HANDLE
    user32.SetClipboardData.argtypes = [UINT, HANDLE]
    user32.SetClipboardData.restype = HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [UINT]
    user32.IsClipboardFormatAvailable.restype = BOOL

    user32.GetForegroundWindow.restype = HWND
    user32.GetDesktopWindow.restype = HWND
    user32.IsWindow.argtypes = [HWND]
    user32.IsWindow.restype = BOOL
    user32.IsWindowVisible.argtypes = [HWND]
    user32.IsWindowVisible.restype = BOOL
    user32.IsIconic.argtypes = [HWND]
    user32.IsIconic.restype = BOOL
    user32.IsZoomed.argtypes = [HWND]
    user32.IsZoomed.restype = BOOL
    user32.GetWindowTextLengthW.argtypes = [HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [HWND, LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [HWND, LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = BOOL
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.ShowWindow.restype = BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.SetForegroundWindow.restype = BOOL
    user32.BringWindowToTop.argtypes = [HWND]
    user32.BringWindowToTop.restype = BOOL
    user32.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
    user32.AttachThreadInput.restype = BOOL
    user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, UINT]
    user32.SetWindowPos.restype = BOOL
    user32.PostMessageW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = BOOL
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
    user32.GetWindowThreadProcessId.restype = DWORD
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = BOOL
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = BOOL
    user32.SendInput.argtypes = [UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = UINT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.MonitorFromWindow.argtypes = [HWND, DWORD]
    user32.MonitorFromWindow.restype = HANDLE
    user32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = BOOL
    user32.GetWindowDC.argtypes = [HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    # kernel32
    kernel32.GlobalAlloc.argtypes = [UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.GetApplicationUserModelId.argtypes = [HANDLE, ctypes.POINTER(DWORD), LPWSTR]
    kernel32.GetApplicationUserModelId.restype = ctypes.c_long
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, LPWSTR,
                                                    ctypes.POINTER(DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = BOOL
    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.GetTickCount.restype = DWORD

    # gdi32
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, DWORD]
    gdi32.BitBlt.restype = BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, UINT, UINT, LPVOID,
                                ctypes.c_void_p, UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = BOOL

    # ole32
    ole32.CoInitialize.argtypes = [LPVOID]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, LPVOID, DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p]
    ole32.CoCreateInstance.restype = ctypes.c_long


_install_prototypes()
