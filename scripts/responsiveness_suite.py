"""
Responsiveness acceptance suite (§7, §11) against the live Preview binary.

    python scripts/responsiveness_suite.py --scenario stop
    python scripts/responsiveness_suite.py --scenario unreachable
    python scripts/responsiveness_suite.py --scenario move

Assumes GENIE Preview is already running. Uses Windows' own hung-window
detection (IsHungAppWindow / SendMessageTimeout) so results are measured,
not eyeballed.
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


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]


def hung(hwnd):
    return bool(u32.IsHungAppWindow(wt.HWND(hwnd)))


def pump_alive(hwnd, ms=400):
    res = ctypes.c_ulong()
    ok = u32.SendMessageTimeoutW(wt.HWND(hwnd), 0, 0, 0, 0x0002, ms,
                                 ctypes.byref(res))
    return bool(ok)


def rect_of(hwnd):
    r = RECT()
    u32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top)


def goto(window, label):
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
    raise RuntimeError(f"nav {label!r} not found")


def click(window, title, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            b = window.child_window(title=title, control_type="Button")
            if b.exists(timeout=1):
                try:
                    b.invoke()
                except Exception:
                    b.click_input()
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def send(window, text):
    edits = list(window.descendants(control_type="Edit"))
    if not edits:
        return False
    box = edits[-1]
    try:
        box.set_focus()
    except Exception:
        box.click_input()
    time.sleep(0.3)
    box.type_keys(text, with_spaces=True)
    time.sleep(0.3)
    box.type_keys("{ENTER}", with_spaces=True)
    return True


def chat_text(window):
    return " ".join((t.window_text() or "")
                    for t in window.descendants(control_type="Text"))


def watch(window, seconds, label, stop_after=None, on_tick=None):
    hwnd = window.handle
    end = time.time() + seconds
    bad = 0
    n = 0
    acted = False
    while time.time() < end:
        h = hung(hwnd)
        p = pump_alive(hwnd)
        n += 1
        if h or not p:
            bad += 1
        if stop_after is not None and not acted and time.time() >= stop_after:
            if on_tick:
                on_tick()
            acted = True
        time.sleep(0.5)
    print(f"    [{label}] samples={n} hung={bad} -> "
          f"{'HUNG' if bad else 'RESPONSIVE'}")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True,
                    choices=["stop", "unreachable", "move"])
    ap.add_argument("--watch", type=float, default=25.0)
    args = ap.parse_args()

    window = cu.main_window(timeout=60)
    hwnd = window.handle
    goto(window, "Chat")
    time.sleep(2.0)
    print(f"scenario={args.scenario} hwnd=0x{hwnd:x}")

    if not send(window, "hi"):
        print("    could not find the composer")
        return 1
    print("    sent 'hi'")

    ok = True
    if args.scenario == "stop":
        # Stop must work WHILE the provider is still busy.
        ok = watch(window, args.watch, "waiting then Stop",
                   stop_after=time.time() + 4.0,
                   on_tick=lambda: print("    clicking Stop...",
                                         click(window, "Stop")))
        time.sleep(2.0)
        txt = chat_text(window)
        print(f"    '(stopped)' shown: {'stopped' in txt.lower()}")
        if "stopped" not in txt.lower():
            # Show what the transcript actually contains so the result is
            # diagnosed rather than guessed.
            texts = [(t.window_text() or "").strip()
                     for t in window.descendants(control_type="Text")]
            print("    transcript texts (non-empty, last 12):")
            for s in [x for x in texts if x][-12:]:
                print(f"      {s[:100]!r}")
        ok = ok and ("stopped" in txt.lower())

    elif args.scenario == "unreachable":
        ok = watch(window, args.watch, "unreachable provider")
        txt = chat_text(window)
        shown = ("could not complete" in txt.lower()) or ("retry" in txt.lower())
        print(f"    error surfaced to owner: {shown}")
        if not shown:
            texts = [(t.window_text() or "").strip()
                     for t in window.descendants(control_type="Text")]
            print("    transcript texts (non-empty, last 10):")
            for s in [x for x in texts if x][-10:]:
                print(f"      {s[:110]!r}")
            # An unreachable provider may legitimately be failed over to
            # another provider or answered in offline mode - that is a valid,
            # non-freezing outcome, not a failure of the responsiveness test.
            print("    NOTE: failover/offline reply is also an acceptable "
                  "outcome; the assertion here is only about freezing.")

    elif args.scenario == "move":
        before = rect_of(hwnd)
        ok = watch(window, args.watch, "moving window while pending",
                   stop_after=time.time() + 3.0,
                   on_tick=lambda: u32.SetWindowPos(
                       # NOSIZE | NOZORDER | NOACTIVATE - NOT NOMOVE, or the
                       # new coordinates are ignored.
                       wt.HWND(hwnd), None, before[0] + 60, before[1] + 40,
                       0, 0, 0x0001 | 0x0004 | 0x0010))
        after = rect_of(hwnd)
        moved = after != before
        print(f"    window moved {before} -> {after}: {moved}")
        ok = ok and moved

    print(f"RESULT {args.scenario}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
