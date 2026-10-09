"""Phase 1 — typed continuity through the REAL GUI (automated evidence).

Launches the actual source-built Genie.Desktop.exe, navigates to Chat, sends a
fact ("My temporary phrase is BLUE ORBIT."), then a recall question, and verifies
the answer references BLUE ORBIT — all in the real GUI, same owner session.

This is the typed half of the continuity workflow. The cross-modality half (a
VOICE turn recalling a typed turn) still needs the owner's microphone.

Run with the system python (pywinauto):
    "C:/Users/ghostt/AppData/Local/Programs/Python/Python312/python.exe" scripts/p1_gui_continuity_test.py
"""
from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402
import p1_chat_gui_test as cg  # noqa: E402  (reuse the proven nav/read helpers)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def resolve_composer(window):
    """Resolve a concrete composer wrapper (a spec re-resolves per call and can go
    stale when the page re-renders)."""
    for _ in range(5):
        try:
            c = window.child_window(auto_id="Composer", control_type="Edit")
            if c.exists(timeout=3):
                return c.wrapper_object()
        except Exception:
            pass
        time.sleep(1.0)
    return None


def send(window, text) -> bool:
    # Type + VERIFY, retrying: a window that loses focus mid-type silently drops
    # the tail of the string (observed: "YouT").
    w = None
    for attempt in range(4):
        w = resolve_composer(window)
        if w is None:
            time.sleep(1.0)
            continue
        try:
            w.set_focus()
            w.click_input()
        except Exception:
            pass
        time.sleep(0.4)
        # clear whatever is there, then type
        try:
            w.type_keys("^a{BACKSPACE}")
        except Exception:
            pass
        try:
            w.type_keys(text, with_spaces=True, pause=0.06)
        except Exception as exc:
            print(f"  [INFO] type_keys failed: {exc}")
            time.sleep(1.0)
            continue
        time.sleep(0.8)
        got = ""
        try:
            got = (w.get_value() or w.window_text() or "").strip()
        except Exception:
            pass
        if got == text.strip():
            print(f"  [INFO] composer text ok ({attempt + 1} try)")
            try:
                w.type_keys("{ENTER}")
            except Exception:
                return False
            return cg.wait_settled(window, timeout=150)
        print(f"  [INFO] composer text = {got!r} (retry {attempt + 1})")
        time.sleep(0.8)
    return False


def main() -> int:
    print("=" * 64)
    print("Phase 1 — typed continuity through the real GUI (automated evidence)")
    print("=" * 64)

    proc = cu.launch(cu.DEFAULT_EXE)
    try:
        window = cu.main_window(timeout=90)
        window.set_focus()
        time.sleep(12)
        check("main window rendered", True, f"handle={window.handle}")
        check("navigated to Chat", cg.goto_index(window, "Chat"))

        check("Chat composer found", resolve_composer(window) is not None)

        settled1 = send(window, "My temporary phrase is BLUE ORBIT.")
        check("fact turn settled", settled1)

        before = cg.all_texts(window)
        settled2 = send(window, "Maine abhi kya phrase bola tha?")
        check("recall turn settled", settled2)

        after = cg.all_texts(window)
        new = [t for t in after if t not in before]
        recall_ok = any("BLUE ORBIT" in t.upper() for t in new)
        check("GUI recall references BLUE ORBIT", recall_ok,
              " | ".join(new)[:160])

        try:
            png = REPO / "artifacts" / "p1_gui_evidence" / "chat_continuity.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            cu.capture(window, png)
            check("continuity screenshot captured",
                  png.exists() and png.stat().st_size > 5000,
                  f"{png.stat().st_size if png.exists() else 0} bytes")
        except Exception as exc:
            check("continuity screenshot captured", False, str(exc))
    except Exception as exc:
        check("typed GUI continuity", False, str(exc))
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
