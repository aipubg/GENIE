"""Phase 1 — Chat-to-browser action from the REAL Chat GUI (automated evidence).

Sends "YouTube open karo aur Barsaat song play karo." through the real Chat
composer and then observes:
  * the reply text the GUI renders,
  * whether a GENIE-owned browser process actually appeared,
  * a screenshot of the Chat reply.

This is the Chat->action integration the owner workflow needs (a direct CDP test
is NOT proof of it). It is REAL GUI interaction but NOT owner acceptance.

Run with the system python (pywinauto):
    "C:/Users/ghostt/AppData/Local/Programs/Python/Python312/python.exe" scripts/p1_gui_browser_action_test.py
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


def browsers() -> tuple[list[str], list[str]]:
    """Return (genie_owned, all_browsers) browser process labels."""
    genie, allb = [], []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name not in ("chrome.exe", "msedge.exe", "chromium.exe"):
                    continue
                label = f"{p.info['name']}#{p.info['pid']}"
                allb.append(label)
                try:
                    cmd = " ".join(p.cmdline())
                except Exception:
                    cmd = ""
                low = cmd.lower()
                if "genie" in low or "browser-profile" in low:
                    genie.append(label)
            except Exception:
                continue
    except Exception:
        pass
    return genie, allb


def main() -> int:
    msg = "YouTube open karo aur Barsaat song play karo."
    print("=" * 64)
    print("Phase 1 — Chat-to-browser action from the real GUI (automated evidence)")
    print("=" * 64)

    before_genie, before_all = browsers()
    proc = cu.launch(cu.DEFAULT_EXE)
    try:
        window = cu.main_window(timeout=90)
        window.set_focus()
        time.sleep(12)
        check("main window rendered", True, f"handle={window.handle}")
        check("navigated to Chat", cg.goto_index(window, "Chat"))

        check("Chat composer found", cont.resolve_composer(window) is not None)

        before = cg.all_texts(window)
        settled = cont.send(window, msg)
        check("browser-action turn settled", settled)

        # give the browser action a moment to produce evidence
        time.sleep(6)
        after = cg.all_texts(window)
        new = [t for t in after if t not in before]
        reply = " | ".join(new)[:300]
        check("Chat rendered a reply to the action request", len(new) > 0, reply)

        # truthful final message: must reflect the verified result, not a bare claim
        joined = " ".join(new).lower()
        mentions = any(k in joined for k in ("play", "youtube", "barsaat", "chal",
                                             "open", "khol", "verify", "confirm",
                                             "nahi", "could", "unable"))
        check("reply is about the browser action (not a generic answer)", mentions,
              reply[:160])

        # The action runs as a Mission (async); poll for the browser it launches.
        after_genie, after_all = [], []
        for _ in range(45):          # observe up to ~135s for the async mission
            after_genie, after_all = browsers()
            if after_genie:
                break
            time.sleep(3)
        check("GENIE-owned browser appeared", bool(after_genie),
              f"genie={after_genie} all_browsers={after_all}")

        try:
            png = REPO / "artifacts" / "p1_gui_evidence" / "chat_browser_action.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            cu.capture(window, png)
            check("browser-action screenshot captured",
                  png.exists() and png.stat().st_size > 5000,
                  f"{png.stat().st_size if png.exists() else 0} bytes")
        except Exception as exc:
            check("browser-action screenshot captured", False, str(exc))
    except Exception as exc:
        check("Chat-to-browser GUI action", False, str(exc))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
