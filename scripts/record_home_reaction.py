"""Record a short Home CORE-REACTION clip (animated GIF) from the live build.

Direct proof that the ring reacts to voice state, captured from the exact binary
the owner runs — not a different executable.

No ffmpeg on this host, so frames are assembled into an animated GIF (viewable
everywhere). The clip shows:

    * Idle   — calm breathing
    * Listening — clearly active (higher scale + glow floor) versus Idle
    * back to Idle

The amplitude is the REAL backend meter. Owner speech would drive it further;
this environment has no microphone, so the state-driven floor is what is shown.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import pathlib
import sys
import time

from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import capture_ui as cu  # noqa: E402

OUT = REPO / "artifacts" / "ui" / "home_reaction.gif"


def _window_image(window):
    hwnd = window.handle
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc_w = user32.GetWindowDC(hwnd)
    hdc_m = gdi32.CreateCompatibleDC(hdc_w)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_w, w, h)
    prev = gdi32.SelectObject(hdc_m, hbmp)
    try:
        user32.PrintWindow(hwnd, hdc_m, 0x2)
        info = cu._BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(info)
        info.biWidth = w
        info.biHeight = -h
        info.biPlanes = 1
        info.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_m, hbmp, 0, h, buf, ctypes.byref(info), 0)
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.SelectObject(hdc_m, prev)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_m)
        user32.ReleaseDC(hwnd, hdc_w)


def main() -> int:
    try:
        window = cu.main_window(timeout=6)
    except Exception:
        cu.launch(cu.DEFAULT_EXE)
        window = cu.main_window(timeout=90)
    _ = cu  # imported for helpers

    window.set_focus()
    time.sleep(2.0)
    cu._goto_home(window) if hasattr(cu, "_goto_home") else None

    # Go to Home surface.
    nav, items = cu.nav_items(window)
    for it in items:
        if cu.item_label(it).strip().lower() == "home":
            try:
                it.click_input()
            except Exception:
                it.select()
            time.sleep(2.5)
            break

    frames = []
    # Idle ~2.5s
    for _ in range(18):
        frames.append(_window_image(window))
        time.sleep(0.14)
    # Toggle Listening by clicking the core (window centre).
    try:
        window.click_input(coords=(540, 360), absolute=False)
    except Exception:
        pass
    time.sleep(0.8)
    # Listening ~3s (visible floor vs Idle)
    for _ in range(22):
        frames.append(_window_image(window))
        time.sleep(0.14)
    # Toggle back to Idle
    try:
        window.click_input(coords=(540, 360), absolute=False)
    except Exception:
        pass
    time.sleep(0.8)
    for _ in range(16):
        frames.append(_window_image(window))
        time.sleep(0.14)

    if not frames:
        print("NO FRAMES")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Downscale a touch to keep the GIF light.
    small = [f.resize((f.width // 2, f.height // 2)) for f in frames]
    small[0].save(OUT, save_all=True, append_images=small[1:],
                  duration=140, loop=0, optimize=True)
    print(f"OK {OUT}  frames={len(small)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
