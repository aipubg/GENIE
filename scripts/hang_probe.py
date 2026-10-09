"""
Reproduce and MEASURE the Chat UI hang on the real Preview binary.

Uses the same API Windows itself uses to decide a window is hung
(IsHungAppWindow / SendMessageTimeout) so "Not Responding" is measured
objectively instead of guessed from a screenshot.

    python scripts/hang_probe.py [--no-send] [--watch SECONDS]

    --no-send   navigate to Chat but never send (tests §8 navigation only)
    --watch     how long to poll responsiveness after the action
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.IsHungAppWindow.restype = wt.BOOL
u32.IsHungAppWindow.argtypes = [wt.HWND]


def is_hung(hwnd: int) -> bool:
    return bool(u32.IsHungAppWindow(wt.HWND(hwnd)))


def pump_alive(hwnd: int, timeout_ms: int = 400) -> bool:
    """Ask the window a harmless question. If the message pump is blocked the
    call times out - which is precisely what Windows reports as hung."""
    SMTO_ABORTIFHUNG = 0x0002
    WM_NULL = 0x0000
    res = ctypes.c_ulong()
    ok = u32.SendMessageTimeoutW(
        wt.HWND(hwnd), WM_NULL, 0, 0,
        SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(res))
    return bool(ok)


def goto(window, label: str) -> None:
    window.set_focus()
    time.sleep(0.4)
    nav, items = cu.nav_items(window)
    for item in items:
        if cu.item_label(item).strip().lower() == label.lower():
            try:
                item.invoke()
            except Exception:
                item.click_input()
            time.sleep(3.0)
            return
    raise RuntimeError(f"nav item {label!r} not found")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--text", default="hi", help="message to send in Chat")
    ap.add_argument("--watch", type=float, default=45.0)
    ap.add_argument("--launch-wait", type=float, default=0.0,
                    help="seconds to wait while an already-started app settles")
    args = ap.parse_args()

    if args.launch_wait:
        time.sleep(args.launch_wait)

    window = cu.main_window(timeout=60)
    hwnd = window.handle
    print(f"window hwnd=0x{hwnd:x} title={window.window_text()!r}")

    print("[1] navigate to Chat")
    goto(window, "Chat")
    time.sleep(3.0)

    # Baseline: is the pump healthy just from being on Chat?
    print(f"    after navigation: hung={is_hung(hwnd)} pump_alive={pump_alive(hwnd)}")

    if args.no_send:
        print("[2] --no-send: watching navigation only")
    else:
        print("[2] type 'hi' and press Enter")
        edits = list(window.descendants(control_type="Edit"))
        if not edits:
            print("    no Edit control found - cannot send")
            return 1
        box = edits[-1]
        try:
            box.set_focus()
        except Exception:
            box.click_input()
        time.sleep(0.4)
        box.type_keys(args.text, with_spaces=True)
        time.sleep(0.4)
        box.type_keys("{ENTER}", with_spaces=True)
        print("    sent")

    print(f"[3] watching responsiveness for {args.watch:.0f}s")
    deadline = time.time() + args.watch
    hung_at = None
    samples = 0
    hung_samples = 0
    while time.time() < deadline:
        h = is_hung(hwnd)
        p = pump_alive(hwnd)
        samples += 1
        if h or not p:
            hung_samples += 1
            if hung_at is None:
                hung_at = time.time()
                print(f"    !! HUNG detected (IsHungAppWindow={h} pump_alive={p})")
        if samples % 10 == 0:
            print(f"    t+{args.watch - (deadline - time.time()):5.1f}s "
                  f"hung={h} pump={p}")
        time.sleep(0.5)

    print()
    print(f"RESULT samples={samples} hung_samples={hung_samples} "
          f"first_hung={'yes' if hung_at else 'no'}")
    if hung_samples:
        print("VERDICT: WPF message pump stopped -> Windows shows "
              "'GENIE (Not Responding)'")
        return 2
    print("VERDICT: UI stayed responsive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
