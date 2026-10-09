"""
Multi-turn Chat stability harness (P0).

Sends N sequential turns through the real Chat UI and records, per turn:

    frontend alive?   backend healthy?   composer state   reply rendered
    process exit / window loss / "Not Responding"

    python scripts/multi_turn_test.py 20            # "message 1" .. "message 20"
    python scripts/multi_turn_test.py --case a      # conversational
    python scripts/multi_turn_test.py --case b      # mixed (incl. action request)
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.IsHungAppWindow.restype = wt.BOOL
u32.IsHungAppWindow.argtypes = [wt.HWND]

CONV = ["hi", "how are you?", "what did I just ask you?",
        "tell me something short", "thanks"]
MIXED = ["hi", "explain what GENIE is",
         "open YouTube and play a trending song",
         "what are you doing now?", "stop that action"]


def health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=5) as r:
            return b'"ok"' in r.read()
    except Exception:
        return False


def frontend_pids() -> list[int]:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Genie.Desktop.exe",
                              "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=20).stdout
        pids = []
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower().startswith("genie.desktop"):
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return pids
    except Exception:
        return []


def goto(window, label):
    window.set_focus()
    time.sleep(0.3)
    nav, items = cu.nav_items(window)
    for item in items:
        if cu.item_label(item).strip().lower() == label.lower():
            try:
                item.invoke()
            except Exception:
                item.click_input()
            time.sleep(2.5)
            return
    raise RuntimeError(f"nav {label!r} not found")


def wait_composer(window, timeout=25.0):
    """The Chat surface can take a moment to materialise after navigation."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            edits = list(window.descendants(control_type="Edit"))
            if edits:
                return edits[-1]
        except Exception:
            pass
        time.sleep(0.8)
    return None


def send(window, text) -> bool:
    box = wait_composer(window)
    if box is None:
        return False
    try:
        box.set_focus()
    except Exception:
        box.click_input()
    time.sleep(0.25)
    box.type_keys(text, with_spaces=True)
    time.sleep(0.25)
    box.type_keys("{ENTER}", with_spaces=True)
    return True


def texts(window):
    vals = []
    for t in window.descendants():
        try:
            s = (t.window_text() or "").strip()
        except Exception:
            continue
        if s and len(s) > 2:
            vals.append(s)
    return vals


def settled(window, timeout=70.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            titles = [(b.window_text() or "").strip()
                      for b in window.descendants(control_type="Button")]
            if "Send" in titles and "Stop" not in titles:
                return True
        except Exception:
            return False
        time.sleep(0.7)
    return False


def reply_of(window):
    vals = texts(window)
    for i, s in enumerate(vals):
        if s == "GENIE" and i + 1 < len(vals):
            return vals[i + 1]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("count", nargs="?", type=int, default=20)
    ap.add_argument("--case", choices=["a", "b", "c"], default="c")
    args = ap.parse_args()

    if args.case == "a":
        msgs = CONV
    elif args.case == "b":
        msgs = MIXED
    else:
        msgs = [f"message {i}" for i in range(1, args.count + 1)]

    window = None
    for _ in range(6):
        try:
            window = cu.main_window(timeout=30)
            break
        except Exception:
            time.sleep(8)
    if window is None:
        print("FATAL: GENIE window not found")
        return 1

    hwnd = window.handle
    goto(window, "Chat")
    time.sleep(2.0)
    if wait_composer(window) is None:
        print("FATAL: composer never appeared on Chat")
        return 1

    pids0 = frontend_pids()
    print(f"start: frontend pids={pids0} backend_healthy={health()}")
    print("-" * 78)

    failures = []
    for n, msg in enumerate(msgs, start=1):
        alive_before = frontend_pids()
        sent = send(window, msg)
        if not sent:
            print(f"turn {n:>2}: COMPOSER LOST - frontend pids={frontend_pids()} "
                  f"backend={health()}")
            failures.append((n, "composer lost"))
            break

        ok = False
        try:
            ok = settled(window)
        except Exception as exc:
            print(f"turn {n:>2}: window access failed: {exc}")
            failures.append((n, "window lost"))
            break

        time.sleep(0.8)
        pid_after = frontend_pids()
        be = health()
        try:
            hung = bool(u32.IsHungAppWindow(wt.HWND(hwnd)))
            reply = reply_of(window)
        except Exception:
            hung = None
            reply = ""

        status = "OK" if (ok and pid_after and be and not hung) else "PROBLEM"
        print(f"turn {n:>2}: {status:<8} settled={ok} pids={pid_after} "
              f"backend={be} hung={hung} reply={reply[:60]!r}")

        if status != "OK":
            failures.append((n, status))
            if not pid_after:
                print("    >>> FRONTEND PROCESS GONE")
                break
            if not be:
                print("    >>> BACKEND UNHEALTHY")

    print("-" * 78)
    print(f"turns={len(msgs)} failures={len(failures)}")
    for n, kind in failures:
        print(f"   turn {n}: {kind}")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
