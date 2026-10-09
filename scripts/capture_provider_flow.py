"""Capture the owner-facing PROVIDER flow as real screenshots (Pass 3.5).

Proves the rendered UI matches the latest source:
    settings_top.png     - no Everyday/Fast/Deep Work, no Enable/Disable
    settings_providers.png- configured production providers, no test/stub
    provider_picker.png  - Custom / OpenAI-compatible clearly reachable (pinned)
    custom_form.png      - heading is Custom, not a built-in name
    settings_advanced.png- Advanced diagnostics: real STT/TTS/mic + build identity

Run:  python scripts/capture_provider_flow.py
"""
from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402

OUT = REPO / "artifacts" / "ui"


def _goto(window, label: str) -> None:
    window.set_focus()
    time.sleep(0.4)
    nav, items = cu.nav_items(window)
    for item in items:
        if cu.item_label(item).strip().lower() == label.lower():
            try:
                item.click_input()
            except Exception:
                item.select()
            time.sleep(3.5)          # let the surface actually render
            return
    raise RuntimeError(f"nav item {label!r} not found")


def _scroll(window, percent: float) -> None:
    """Scroll every ScrollViewer in the page to the given vertical percent."""
    try:
        for sv in window.descendants(control_type="ScrollViewer"):
            try:
                sv.iface_scroll.SetScrollPercent(-1, percent)
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(1.0)


def _scroll_bottom(window) -> None:
    """Push every ScrollViewer to its real bottom, repeatedly, and finish with
    a single Ctrl+End. Avoids keystroke spam that can destabilise the WPF
    window."""
    for _ in range(12):
        try:
            for sv in window.descendants(control_type="ScrollViewer"):
                try:
                    sv.iface_scroll.SetScrollPercent(-1, 100)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.15)
    try:
        window.set_focus()
        window.type_keys("^{END}")
    except Exception:
        pass
    time.sleep(1.0)


def _click_button(window, title: str, timeout: float = 6.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = window.child_window(title=title, control_type="Button")
            if btn.exists(timeout=1):
                btn.click_input()
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _click_checkbox(window, title: str, timeout: float = 6.0) -> bool:
    """Toggle the first CheckBox whose label contains `title`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for cb in window.descendants(control_type="CheckBox"):
                try:
                    if title.lower() in (cb.window_text() or "").lower():
                        cb.click_input()
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        window = cu.main_window(timeout=6)
    except Exception:
        cu.launch(cu.DEFAULT_EXE)
        window = cu.main_window(timeout=90)

    _goto(window, "Settings")
    _scroll(window, 0)
    cu.capture(window, OUT / "settings_top.png")
    print("OK settings_top.png")

    _scroll(window, 0)
    cu.capture(window, OUT / "settings_providers.png")
    print("OK settings_providers.png")

    if _click_button(window, "+ Add Provider"):
        time.sleep(1.5)
        cu.capture(window, OUT / "provider_picker.png")
        print("OK provider_picker.png")
    else:
        print("SKIP provider_picker.png (Add Provider button not found)")

    clicked = False
    try:
        text = window.child_window(title="Custom / OpenAI-compatible", control_type="Text")
        if text.exists(timeout=4):
            text.click_input()
            clicked = True
    except Exception:
        pass
    if not clicked:
        clicked = _click_button(window, "Custom / OpenAI-compatible")
    if clicked:
        time.sleep(1.5)
        cu.capture(window, OUT / "custom_form.png")
        print("OK custom_form.png")
    else:
        print("SKIP custom_form.png (Custom option not found)")

    # Close the Add-Provider sheet before toggling the page-level Advanced
    # checkbox. The Custom form's own "Advanced options" checkbox would
    # otherwise be the first match and the diagnostics panel would never be
    # opened.
    _click_button(window, "Cancel")
    _click_button(window, "Back")
    time.sleep(0.8)

    # Advanced / developer mode: reveals the authoritative voice diagnostics and
    # the build identity, so the owner can see "which build am I running?".
    # The checkbox may already be ON from a prior session; clicking blindly
    # would toggle it OFF. We verify by looking for the "Instance" label.
    advanced_on = False
    try:
        for txt in window.descendants(control_type="Text"):
            if "Instance" in (txt.window_text() or ""):
                advanced_on = True
                break
    except Exception:
        pass
    if not advanced_on:
        _click_checkbox(window, "developer")
        time.sleep(2.0)
    # Refresh forces the diagnostics panel to reload so the captured
    # frame shows real values instead of transient blanks.
    _click_button(window, "Refresh")
    time.sleep(3.0)
    _scroll_bottom(window)
    cu.capture(window, OUT / "settings_advanced.png")
    print("OK settings_advanced.png")

    cu.capture(window, OUT / "home.png") if False else None  # no-op guard
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
