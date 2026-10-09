"""Phase 1 — typed Chat end-to-end through the REAL GUI (automated evidence).

Launches the actual source-built Genie.Desktop.exe, navigates to Chat using the
proven capture_ui click-by-index method, types a message into the real composer,
waits for the turn to settle, and reads the rendered transcript.

This uses the owner's configured provider (a real remote call). It is REAL GUI
interaction but NOT owner acceptance.

Run with the system python (pywinauto):
    "C:/Users/ghostt/AppData/Local/Programs/Python/Python312/python.exe" scripts/p1_chat_gui_test.py "Hello GENIE"
"""
from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def goto_index(window, target: str) -> bool:
    window.set_focus()
    time.sleep(0.4)
    nav, items = cu.nav_items(window)
    names = [cu.item_label(it) for it in items]
    idx = next((i for i, n in enumerate(names) if n == target), None)
    if idx is None:
        print(f"  (sidebar has: {names})")
        return False
    try:
        items[idx].click_input()
    except Exception:
        items[idx].select()
    time.sleep(2.0)
    if not cu._is_selected(items[idx]):
        try:
            items[idx].select()
            time.sleep(2.0)
        except Exception:
            pass
    return True


def all_texts(window) -> list[str]:
    out = []
    for t in window.descendants(control_type="Text"):
        try:
            s = (t.window_text() or "").strip()
        except Exception:
            continue
        if s:
            out.append(s)
    return out


def buttons(window) -> list[str]:
    out = []
    for b in window.descendants(control_type="Button"):
        try:
            out.append((b.window_text() or "").strip())
        except Exception:
            pass
    return out


def wait_settled(window, timeout=120.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        btns = buttons(window)
        if "Send" in btns and "Stop" not in btns:
            return True
        time.sleep(0.8)
    return False


def main() -> int:
    msg = sys.argv[1] if len(sys.argv) > 1 else "Hello GENIE"
    print("=" * 64)
    print("Phase 1 — typed Chat GUI end-to-end (automated evidence)")
    print(f"exe: {cu.DEFAULT_EXE}")
    print("=" * 64)

    proc = cu.launch(cu.DEFAULT_EXE)
    try:
        window = cu.main_window(timeout=90)
        window.set_focus()
        time.sleep(12)                     # let the backend answer
        check("main window rendered", True, f"handle={window.handle}")

        check("navigated to Chat", goto_index(window, "Chat"))

        comp = None
        for _ in range(20):
            try:
                c = window.child_window(auto_id="Composer", control_type="Edit")
                if c.exists(timeout=2):
                    comp = c
                    break
            except Exception:
                pass
            time.sleep(1.0)
        check("Chat composer found (auto_id=Composer)", comp is not None)

        if comp is None:
            return _summary()

        before = all_texts(window)
        try:
            comp.set_focus()
        except Exception:
            comp.click_input()
        time.sleep(0.3)
        comp.type_keys(msg, with_spaces=True)
        time.sleep(0.4)
        comp.type_keys("{ENTER}")
        check("typed message submitted", True, msg)

        settled = wait_settled(window, timeout=150)
        check("turn settled (Send visible again)", settled)
        time.sleep(1.5)
        after = all_texts(window)
        new = [t for t in after if t not in before and len(t) > 2]
        # A real reply is new text beyond the echoed user message.
        reply = [t for t in new if msg.strip().lower() not in t.lower()]
        check("Chat rendered a reply", len(reply) > 0,
              " | ".join(reply)[:160])

        try:
            png = REPO / "artifacts" / "p1_gui_evidence" / "chat_reply.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            cu.capture(window, png)
            check("Chat reply screenshot captured",
                  png.exists() and png.stat().st_size > 5000,
                  f"{png.stat().st_size if png.exists() else 0} bytes")
        except Exception as exc:
            check("Chat reply screenshot captured", False, str(exc))
    except Exception as exc:
        check("typed Chat GUI end-to-end", False, str(exc))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    return _summary()


def _summary() -> int:
    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
