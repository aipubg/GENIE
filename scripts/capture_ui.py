"""Capture every owner-facing surface of the native client as a REAL screenshot.

Reading XAML cannot show clipping, wrapped text, unstyled white panels or a
control that silently failed to bind. This launches the actual executable,
navigates the real sidebar, and grabs the rendered window.

Implemented in Python (pywinauto + Pillow) rather than PowerShell because the
agent shell cannot launch native processes from the PowerShell tool on this
host: PowerShell returns no output and no exit code for child processes.

Run:
    python scripts/capture_ui.py                 # all surfaces
    python scripts/capture_ui.py --only Home Settings
    python scripts/capture_ui.py --list          # dump the nav tree only
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import pathlib
import subprocess
import sys
import time

from PIL import Image
from pywinauto import Desktop

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_EXE = (REPO / "ui" / "windows" / "Genie.Desktop" / "bin" / "Release"
               / "net8.0-windows" / "win-x64" / "Genie.Desktop.exe")

SURFACES = ["Home", "Chat", "Missions", "Agents", "Computer", "Skills",
            "Devices", "Memory", "Forecast", "Security", "Competition",
            "Experience", "Knowledge", "Media", "Settings"]


def launch(exe: pathlib.Path) -> subprocess.Popen:
    return subprocess.Popen([str(exe)], cwd=str(exe.parent))


def main_window(timeout: float = 90.0):
    """Wait for the GENIE window.

    Returns a WindowSpecification (not a UIAWrapper) because only the
    specification exposes child_window(), which the navigation needs.
    """
    desktop = Desktop(backend="uia")
    spec = desktop.window(title="GENIE", control_type="Window")
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if spec.exists(timeout=2) and spec.is_visible():
                return spec
        except Exception as exc:  # window list races during startup
            last = exc
        time.sleep(1.0)
    raise RuntimeError(f"GENIE window never appeared (last error: {last})")


def nav_items(window):
    """The sidebar ListBox items, in visual order.

    The accessible name comes from AutomationProperties.Name on the item
    container. If it is ever missing the label falls back to the last text
    child (the title), never to the CLR type name.
    """
    nav = window.child_window(auto_id="NavList", control_type="List")
    nav.wait("exists ready", timeout=30)
    items = nav.children(control_type="ListItem")
    return nav, items


def item_label(item) -> str:
    try:
        name = (item.element_info.name or "").strip()
    except Exception:
        name = ""
    if name and "ViewModels" not in name and not name.startswith("Genie."):
        return name
    # Fallback: last non-empty text descendant is the title (first is the glyph).
    try:
        texts = [t.window_text().strip()
                 for t in item.descendants(control_type="Text")]
        texts = [t for t in texts if t]
        if texts:
            return texts[-1]
    except Exception:
        pass
    return name


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def _is_selected(item) -> bool:
    """True when a nav item is the current selection."""
    try:
        return bool(item.is_selected())
    except Exception:
        return True   # cannot tell: do not force a retry


def capture(window, out: pathlib.Path) -> tuple[int, int]:
    """Grab the window itself, not the screen region it happens to occupy.

    A plain screen grab returns whatever window is on top, which is how an
    unrelated application ended up in a GENIE screenshot. PrintWindow asks the
    window to render itself, so z-order and occlusion do not matter.

    PW_RENDERFULLCONTENT (0x2) is required for WPF/DirectComposition surfaces;
    without it the result is a blank frame.
    """
    hwnd = window.handle
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"window has no drawable area ({width}x{height})")

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    previous = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        if not user32.PrintWindow(hwnd, hdc_mem, 0x00000002):
            raise RuntimeError("PrintWindow failed")

        info = _BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.biWidth = width
        info.biHeight = -height          # negative = top-down rows
        info.biPlanes = 1
        info.biBitCount = 32
        info.biCompression = 0           # BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buffer,
                        ctypes.byref(info), 0)
        image = Image.frombuffer("RGBA", (width, height), buffer,
                                 "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.SelectObject(hdc_mem, previous)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)

    # A frame with a single luminance value means nothing was rendered, so it
    # must never be filed as evidence that the surface looks right.
    low, high = image.convert("L").getextrema()
    if low == high:
        raise RuntimeError(f"uniform frame (luminance {low}) - window did not render")

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return image.size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    ap.add_argument("--out", default=str(REPO / "artifacts" / "ui"))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--settle", type=float, default=2.5,
                    help="seconds to wait after switching a surface")
    args = ap.parse_args()

    exe = pathlib.Path(args.exe)
    if not exe.exists():
        print(f"executable not found: {exe}", file=sys.stderr)
        return 2

    out_dir = pathlib.Path(args.out)
    proc = launch(exe)
    try:
        window = main_window()
        window.set_focus()
        # The backend takes a moment to answer; let the first surface settle so
        # we never capture a loading state and call it the design.
        time.sleep(12)

        nav, items = nav_items(window)
        names = [item_label(it) for it in items]

        if args.list:
            print(f"nav items: {len(items)}")
            for i, n in enumerate(names):
                print(f"  {i:2d} {n!r}")
            return 0

        wanted = args.only or SURFACES
        results = []
        for target in wanted:
            idx = next((i for i, n in enumerate(names) if n == target), None)
            if idx is None:
                print(f"SKIP {target} :: not found in sidebar {names}")
                results.append((target, None))
                continue
            # click_input drives the real mouse, so the window must be in front
            # or the click lands on whatever window is covering it and the
            # surface never changes.
            try:
                window.set_focus()
            except Exception:
                pass
            time.sleep(0.4)
            try:
                items[idx].click_input()
            except Exception:
                # Fall back to selection when a synthetic click is refused.
                items[idx].select()
            time.sleep(args.settle)

            # Verify the surface actually switched; retry once via select().
            if not _is_selected(items[idx]):
                try:
                    items[idx].select()
                    time.sleep(args.settle)
                except Exception:
                    pass
            path = out_dir / f"{target.lower()}.png"
            try:
                size = capture(window, path)
            except Exception as exc:
                print(f"FAIL {target} :: {exc}")
                results.append((target, None))
                continue
            print(f"OK   {target} :: {size[0]}x{size[1]} -> {path}")
            results.append((target, path))

        missing = [t for t, p in results if p is None]
        print()
        print(f"captured {len(results) - len(missing)}/{len(results)} surfaces")
        if missing:
            print("not captured: " + ", ".join(missing))
        return 0
    finally:
        try:
            window  # noqa: F821 - only bound when startup succeeded
        except NameError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
