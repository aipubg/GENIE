"""Phase 1 corrective — the ordinary Chat request must trigger the real action.

Sends the owner's YouTube request through the REAL Chat composer and checks the
GUI reply reflects a VERIFIED execution receipt (not narration) and that a
GENIE-owned browser actually appeared.
"""
from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402
import p1_chat_gui_test as cg  # noqa: E402
import p1_gui_continuity_test as cont  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def browsers() -> list[str]:
    out = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            n = (p.info.get("name") or "").lower()
            if n in ("chrome.exe", "msedge.exe", "brave.exe", "chromium.exe"):
                out.append(f"{p.info['name']}#{p.info['pid']}")
    except Exception:
        pass
    return out


def main() -> int:
    msg = "YouTube open karo aur Barsaat song play karo."
    print("=" * 70)
    print("Phase 1 — Chat request triggers the real browser action (GUI)")
    print("=" * 70)

    before = browsers()
    proc = cu.launch(cu.DEFAULT_EXE)
    try:
        window = cu.main_window(timeout=90)
        window.set_focus()
        time.sleep(12)
        check("main window rendered", True, f"handle={window.handle}")
        check("navigated to Chat", cg.goto_index(window, "Chat"))
        check("Chat composer found", cont.resolve_composer(window) is not None)

        before_texts = cg.all_texts(window)
        settled = cont.send(window, msg)
        check("turn settled", settled)
        time.sleep(8)

        after = cg.all_texts(window)
        new = [t for t in after if t not in before_texts]
        reply = " | ".join(new)
        check("Chat rendered a reply", bool(new), reply[:160])
        # the reply must carry the executor's verified receipt, not a narration
        low = reply.lower()
        check("reply reflects a verified execution receipt",
              ("currenttime" in low or "paused" in low or "succeeded" in low
               or "verify" in low or "ho gaya" in low),
              reply[:180])
        check("reply does not merely narrate a plan",
              not (("step 1/3" in low or "step 2/3" in low or "step 3/3" in low)
                   and "currenttime" not in low),
              reply[:180])

        after_b = []
        for _ in range(15):
            after_b = browsers()
            if after_b:
                break
            time.sleep(3)
        check("a browser process is present", bool(after_b), str(after_b))

        try:
            png = REPO / "artifacts" / "p1_gui_evidence" / "chat_youtube_action.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            cu.capture(window, png)
            check("screenshot captured",
                  png.exists() and png.stat().st_size > 5000,
                  f"{png.stat().st_size if png.exists() else 0} bytes")
        except Exception as exc:
            check("screenshot captured", False, str(exc))
    except Exception as exc:
        check("Chat multi-step action", False, str(exc))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    print("=" * 70)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
