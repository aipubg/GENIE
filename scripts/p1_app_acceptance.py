"""Track 4: REAL generic Windows application acceptance with Notepad.

Runs the actual ComputerService the daemon uses: open -> bind
ApplicationInteractionContext -> observe the real HWND -> locate the editable
control -> type unique text -> verify by read-back -> menu interaction ->
verify. No mocks.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

from core.contracts import CallContext  # noqa: E402
from computer.service import ComputerService  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail and not cond else ""))


def main():
    ctx = CallContext(person_id="owner", device_id="pc_main", trace_id="app-accept")
    svc = ComputerService()
    token = "GENIE" + str(int(time.time()))[-6:]

    try:
        # ---- 1. open the real application --------------------------------
        r = svc.execute(ctx, "application.open", {"target": "notepad"})
        check("notepad_opened", r.ok, str(r.detail)[:120])
        time.sleep(2.5)

        # ---- 2. bind ONE stable context for the whole task ---------------
        bind = svc.execute(ctx, "app.context.bind", {"app": "notepad", "task_id": "notepad-accept"})
        check("context_bound", bind.ok, str(bind.detail)[:120])
        info = (bind.data or {}).get("context") or {}
        hwnd = info.get("hwnd")
        check("context_has_real_hwnd", bool(hwnd) and int(hwnd) > 0, f"hwnd={hwnd}")
        check("context_has_pid", bool(info.get("pid")), f"pid={info.get('pid')}")
        check("context_not_stale", info.get("stale") is False, f"stale={info.get('stale')}")
        check("context_has_content_window", bool(info.get("content_hwnd")))

        # ---- 3. locate the editable control via real UIA -----------------
        # Modern WinUI Notepad exposes the editor as a Document (RichEditD2DPT);
        # classic Win32 Notepad exposes an Edit. Try the modern type first.
        controls = None
        found = []
        for control_type in ("Document", "Edit"):
            controls = svc.execute(ctx, "uia.find", {
                "window": info.get("window_title") or "Notepad",
                "window_id": int(hwnd), "control_type": control_type,
                "limit": 40, "depth": 32})
            found = ((controls.data or {}).get("elements") or (controls.data or {}).get("controls")
                     or (controls.data or {}).get("matches") or [])
            if controls.ok and found:
                print(f"  (editor surface found as control_type={control_type})")
                break
        check("editable_control_found", controls is not None and controls.ok and len(found) > 0,
              f"ok={getattr(controls,'ok',None)} n={len(found)}")

        element_id = None
        for item in found:
            if isinstance(item, dict) and (item.get("element_id") or item.get("id")):
                element_id = item.get("element_id") or item.get("id")
                break
        check("editable_control_has_id", bool(element_id), f"element_id={element_id}")

        if element_id:
            # ---- 4. type unique text through the canonical input authority ----
            typed = svc.execute(ctx, "uia.set_value", {"element_id": element_id, "value": token})
            check("text_entered", typed.ok, str(typed.detail)[:120])

            # ---- 5. verify by independent read-back ---------------------------
            read = svc.execute(ctx, "uia.get_value", {"element_id": element_id})
            value = (read.data or {}).get("value") or ""
            check("typed_text_verified_by_readback", token in str(value),
                  f"value={str(value)[:80]!r}")

            # ---- 6. a SECOND interaction with independent verification ---------
            # Select-all then replace through the canonical input authority, and
            # verify the effect by read-back (a real interaction, not narration).
            second = token + "X"
            svc.execute(ctx, "uia.focus", {"element_id": element_id})
            hot = svc.execute(ctx, "input.hotkey", {"chord": "ctrl+a"})
            typed2 = svc.execute(ctx, "input.type_text", {"text": second})
            read2 = svc.execute(ctx, "uia.get_value", {"element_id": element_id})
            value2 = str((read2.data or {}).get("value") or "")
            check("second_interaction_verified", second in value2,
                  f"hotkey_ok={hot.ok} typed_ok={typed2.ok} value={value2[:60]!r}")

            # ---- 7. the context still describes the SAME window --------------
            status = svc.execute(ctx, "app.context.status")
            contexts = (status.data or {}).get("contexts")
            check("context_reused_not_rediscovered", contexts == 1, f"contexts={contexts}")

            again = svc.execute(ctx, "app.context.bind", {"app": "notepad", "task_id": "notepad-accept"})
            info2 = (again.data or {}).get("context") or {}
            check("rebind_returns_same_hwnd", info2.get("hwnd") == hwnd,
                  f"{info2.get('hwnd')} vs {hwnd}")
    finally:
        try:
            svc.execute(ctx, "window.close", {"title_contains": "Notepad"})
        except Exception:
            pass
        svc.executor.desktop_awareness.stop()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nTrack 4 results: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
