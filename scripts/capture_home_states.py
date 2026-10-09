"""Capture Home in its real voice states, with assertions.

Home is voice-first: the genie figure IS the microphone control. This drives it
for real — click the core, wait for the backend to confirm capture, and only
then file the screenshot as "Listening". If the state label never changes the
run FAILS instead of writing a picture that merely looks like listening.

Run:
    python scripts/capture_home_states.py
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from capture_ui import DEFAULT_EXE, capture, launch, main_window  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "artifacts" / "ui"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))
    return ok


def state_text(window) -> str:
    """The label under the core: 'Ready.', 'Listening…', etc."""
    try:
        for t in window.descendants(control_type="Text"):
            name = (t.window_text() or "").strip()
            if name in ("Ready.", "Listening…", "Thinking…", "Speaking…",
                        "Something went wrong."):
                return name
    except Exception:
        pass
    return ""


def wait_for_state(window, wanted: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state_text(window) == wanted:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    exe = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXE
    proc = launch(exe)
    try:
        window = main_window()
        window.set_focus()
        time.sleep(14)   # backend + first poll

        # ---------------------------------------------------------- idle
        idle = state_text(window)
        if not check("Home reports a known state on launch",
                     idle in ("Ready.", "Listening…", "Thinking…", "Speaking…",
                              "Something went wrong."),
                     f"state={idle!r}"):
            return 1
        if idle != "Ready.":
            wait_for_state(window, "Ready.", timeout=20)
        check("Home is idle before the first click", state_text(window) == "Ready.",
              f"state={state_text(window)!r}")
        size = capture(window, OUT / "home_state_idle.png")
        print(f"     saved home_state_idle.png {size[0]}x{size[1]}")

        # ------------------------------------------------------- listening
        core = window.child_window(auto_id="CoreButton", control_type="Button")
        core.wait("exists enabled", timeout=20)

        # Invoke the button through UI Automation rather than a synthetic mouse
        # click: a click depends on the window being in front and on the exact
        # hit point, and silently does nothing when it is not.
        def press() -> None:
            try:
                core.invoke()
            except Exception:
                core.click_input()

        press()

        listened = wait_for_state(window, "Listening…", timeout=25)
        check("clicking the core puts Home into Listening", listened,
              f"state={state_text(window)!r}")
        if listened:
            size = capture(window, OUT / "home_state_listening.png")
            print(f"     saved home_state_listening.png {size[0]}x{size[1]}")

        # ------------------------------------------------------ back to idle
        press()
        back = wait_for_state(window, "Ready.", timeout=25)
        check("clicking again returns Home to Idle", back,
              f"state={state_text(window)!r}")
        if back:
            size = capture(window, OUT / "home_state_idle_after.png")
            print(f"     saved home_state_idle_after.png {size[0]}x{size[1]}")

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print()
    print(f"RESULT {len(PASS)}/{len(PASS) + len(FAIL)} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
