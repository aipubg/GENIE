"""Capture Pass-4 Custom Provider evidence from the LIVE Preview binary.

Renders, in order:
    p4_01_add_form.png      - Add Provider sheet, Advanced collapsed
    p4_02_models_found.png  - "111 models found" + "3 selected" + checkboxes
    p4_03_provider_row.png  - saved provider row (name / status / Key stored / N models)
    p4_04_edit_provider.png - Edit sheet with the saved models
    p4_05_delete_confirm.png- delete confirmation state

The owner's API key is resolved from the Vault at runtime, typed into the
live form, and never written to disk or to any artifact.
"""
from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture_ui as cu  # noqa: E402

OUT = REPO / "artifacts" / "ui"

DISPLAY_NAME = "Owner Gateway"
BASE_URL = "https://api.xkiro.com/v1"


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
            time.sleep(3.5)
            return
    raise RuntimeError(f"nav item {label!r} not found")


def _click_button(window, title: str, timeout: float = 8.0) -> bool:
    """Prefer the UIA Invoke pattern; a synthetic mouse click does not
    reliably reach this window from an automation client."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = window.child_window(title=title, control_type="Button")
            if btn.exists(timeout=1):
                try:
                    btn.invoke()
                except Exception:
                    btn.click_input()
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _wait_edits(window, n: int, timeout: float = 20.0) -> list:
    """The sheet's controls are realised asynchronously; wait for them.

    Tree order matches visual order here (display name, base URL, API key),
    and reading rectangles is not required - some elements refuse geometry
    while the sheet is still animating in.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            edits = list(window.descendants(control_type="Edit"))
        except Exception:
            edits = []
        if len(edits) >= n:
            return edits[:n]
        time.sleep(1.0)
    return []


def _fill_fields(window, values: list[str]) -> bool:
    """Type into the Add-sheet's Edit controls in visual (top-to-bottom)
    order: display name, base URL, API key."""
    try:
        edits = _wait_edits(window, len(values))
        if len(edits) < len(values):
            print(f"    only {len(edits)} edit controls found")
            return False
        for ed, value in zip(edits, values):
            try:
                ed.set_focus()
            except Exception:
                ed.click_input()
            time.sleep(0.3)
            ed.type_keys("^a", with_spaces=True)
            ed.type_keys(value, with_spaces=True)
            time.sleep(0.3)
        return True
    except Exception as exc:
        print(f"    fill error: {exc}")
        return False


def _scroll_top(window) -> None:
    try:
        for sv in window.descendants(control_type="ScrollViewer"):
            try:
                sv.iface_scroll.SetScrollPercent(-1, 0)
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(1.0)


def _owner_key() -> str:
    from core.config import Config
    from security.vault import Vault
    cfg = Config()
    return Vault(cfg.vault_path).resolve("secret://provider/mock/key") or ""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    key = _owner_key()
    if not key:
        print("FATAL: cannot resolve the gateway credential from the Vault.")
        return 1

    try:
        window = cu.main_window(timeout=6)
    except Exception:
        cu.launch(cu.DEFAULT_EXE)
        window = cu.main_window(timeout=90)

    # Settings can need several seconds to render after a cold launch; retry
    # until the provider surface is actually present rather than assuming.
    loaded = False
    for _ in range(6):
        try:
            _goto(window, "Settings")
        except Exception:
            pass
        time.sleep(4.0)
        if any((b.window_text() or "").strip() == "+ Add Provider"
               for b in window.descendants(control_type="Button")):
            loaded = True
            break
    if not loaded:
        print("SKIP: Settings surface never loaded")
        return 1

    # 1. Add Provider sheet, Advanced collapsed -----------------------------
    if not _click_button(window, "+ Add Provider"):
        print("SKIP: '+ Add Provider' not found")
        return 1
    time.sleep(2.5)
    cu.capture(window, OUT / "p4_01_add_form.png")
    print("OK p4_01_add_form.png")

    # 2. Fill the three fields and discover ---------------------------------
    ok_fill = _fill_fields(window, [DISPLAY_NAME, BASE_URL, key])
    print(f"    filled three fields: {ok_fill}")
    time.sleep(0.8)

    if not _click_button(window, "Discover models"):
        print("SKIP: 'Discover models' button not found")
        return 1

    # Discovery hits the live gateway; give it room to finish.
    for _ in range(30):
        time.sleep(1.0)
        try:
            texts = [t.window_text() for t in window.descendants(control_type="Text")]
            if any("models found" in (x or "") for x in texts):
                break
        except Exception:
            pass
    time.sleep(2.0)
    cu.capture(window, OUT / "p4_02_models_found.png")
    print("OK p4_02_models_found.png")

    # 3. Save -> provider row ----------------------------------------------
    if not _click_button(window, "Save Provider"):
        print("SKIP: 'Save Provider' button not found")
        return 1
    time.sleep(4.0)
    cu.capture(window, OUT / "p4_03_provider_row.png")
    print("OK p4_03_provider_row.png")

    # 4. Edit sheet ---------------------------------------------------------
    # The new row can sit below the fold; bring the page back to the top and
    # retry before giving up.
    _scroll_top(window)
    if _click_button(window, "Edit", timeout=10.0):
        time.sleep(3.0)
        cu.capture(window, OUT / "p4_04_edit_provider.png")
        print("OK p4_04_edit_provider.png")
    else:
        print("SKIP p4_04_edit_provider.png")

    # 5. Delete confirmation ----------------------------------------------
    _scroll_top(window)
    if _click_button(window, "Delete", timeout=10.0):
        time.sleep(2.5)
        cu.capture(window, OUT / "p4_05_delete_confirm.png")
        print("OK p4_05_delete_confirm.png")
    else:
        print("SKIP p4_05_delete_confirm.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
