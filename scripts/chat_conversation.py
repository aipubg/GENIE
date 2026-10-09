"""Send a short conversation through the real Chat UI and dump the transcript.

    python scripts/chat_conversation.py "hi" "how are you?" "what did I just ask you?"

Waits for each turn to settle (Send button visible again) before sending the
next, so the conversation context is real rather than overlapping turns.
"""
from __future__ import annotations

import sys
import time
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402


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


def composer(window):
    edits = list(window.descendants(control_type="Edit"))
    return edits[-1] if edits else None


def send(window, text):
    box = composer(window)
    if box is None:
        return False
    try:
        box.set_focus()
    except Exception:
        box.click_input()
    time.sleep(0.3)
    box.type_keys(text, with_spaces=True)
    time.sleep(0.3)
    box.type_keys("{ENTER}", with_spaces=True)
    return True


def wait_settled(window, timeout=60.0):
    """Wait until the composer shows Send again (turn finished)."""
    end = time.time() + timeout
    while time.time() < end:
        titles = [(b.window_text() or "").strip()
                  for b in window.descendants(control_type="Button")]
        if "Send" in titles and "Stop" not in titles:
            return True
        time.sleep(0.7)
    return False


def transcript(window):
    vals = []
    for t in window.descendants():
        try:
            s = (t.window_text() or "").strip()
        except Exception:
            continue
        if s and len(s) > 2:
            vals.append(s)
    return vals


def main():
    msgs = sys.argv[1:] or ["hi"]
    # The window can take a while to appear after a cold launch; retry rather
    # than failing the whole conversation test.
    window = None
    for _ in range(6):
        try:
            window = cu.main_window(timeout=30)
            break
        except Exception:
            time.sleep(10)
    if window is None:
        print("GENIE window not found")
        return 1
    goto(window, "Chat")
    time.sleep(2.0)
    for m in msgs:
        print(f"\n--- you: {m}")
        if not send(window, m):
            print("    (no composer)")
            break
        ok = wait_settled(window)
        time.sleep(1.0)
        vals = transcript(window)
        reply = ""
        for i, s in enumerate(vals):
            if s == "GENIE" and i + 1 < len(vals):
                reply = vals[i + 1]
        print(f"    settled={ok}")
        print(f"    GENIE: {reply[:220]!r}")
    vals = transcript(window)
    print("\nDEGRADED PRESENT:", any("offline/degraded" in s for s in vals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
