"""Track A tests: stable ApplicationInteractionContext across a task."""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from computer import app_context as ac  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class FakeWindow:
    def __init__(self, hwnd, pid, title, process, class_name="", visible=True, minimized=False):
        self.hwnd, self.pid, self.title, self.process = hwnd, pid, title, process
        self.class_name, self.visible, self.minimized = class_name, visible, minimized


class FakeWin:
    def __init__(self, windows):
        self._windows = windows

    def list_windows(self, visible_only=False):
        return list(self._windows)


def main():
    host = FakeWindow(100, 555, "WhatsApp", "whatsapp.exe")
    content = FakeWindow(101, 555, "WhatsApp", "whatsapp.exe", class_name="Chrome_WidgetWin_1")
    other = FakeWindow(200, 777, "Notepad", "notepad.exe")

    ac.win = FakeWin([host, content, other])

    # ---- 1. bind captures identity -----------------------------------------
    ctx = ac.ApplicationInteractionContext.bind(host, task_id="t1", app="whatsapp")
    check("bind_hwnd", ctx.hwnd == 100)
    check("bind_pid", ctx.pid == 555)
    check("bind_title", ctx.window_title == "WhatsApp")
    check("bind_task_id", ctx.task_id == "t1")

    # ---- 2. host/content resolution ----------------------------------------
    ctx.resolve_content_window()
    check("content_window_resolved", ctx.content_hwnd == 101,
          f"content_hwnd={ctx.content_hwnd}")

    # ---- 3. stability: same task+app returns the SAME context ---------------
    registry = ac.ApplicationContextRegistry()
    first = registry.bind_window(host, task_id="t1", app="whatsapp")
    second = registry.get("t1", "whatsapp")
    check("registry_reuses_context", second is first)
    check("no_rediscovery_identity_preserved",
          second.hwnd == first.hwnd and second.pid == first.pid)

    # ---- 4. staleness -------------------------------------------------------
    check("not_stale_while_window_present", ctx.is_stale() is False)
    ac.win = FakeWin([content, other])          # host window disappeared
    check("stale_when_window_gone", ctx.is_stale() is True)
    ac.win = FakeWin([FakeWindow(100, 999, "WhatsApp", "whatsapp.exe"), content, other])
    check("stale_when_pid_changed", ctx.is_stale() is True)

    # a stale context is not handed back by the registry
    ac.win = FakeWin([content, other])
    check("registry_drops_stale", registry.get("t1", "whatsapp") is None)

    # ---- 5. expiry ----------------------------------------------------------
    ac.win = FakeWin([host, content, other])
    short = ac.ApplicationContextRegistry(ttl_s=1)
    short.bind_window(host, task_id="t2", app="whatsapp")
    time.sleep(1.1)          # deterministic: the TTL must actually elapse
    check("registry_expires_context", short.get("t2", "whatsapp") is None)

    # ---- 6. ONE authority: run() delegates to ComputerService --------------
    class FakeService:
        def __init__(self):
            self.calls = []

        def execute(self, ctx, capability, params, **kw):
            self.calls.append((capability, params))

            class R:
                ok = True
                verified = True

                def to_dict(self):
                    return {"ok": True, "verified": True, "capability": capability}
            return R()

    service = FakeService()
    ctx.run(service, None, "uia.find", {"name": "Search"})
    check("delegates_to_service", len(service.calls) == 1)
    cap, params = service.calls[0]
    check("delegated_capability", cap == "uia.find")
    check("bound_window_injected", params.get("window_id") == 100)
    check("content_window_injected", params.get("content_window_id") == 101)
    check("task_id_injected", params.get("task_id") == "t1")
    check("no_second_controller", not hasattr(ctx, "click") and not hasattr(ctx, "type_text"))

    # explicit override wins
    ctx.run(service, None, "uia.find", {"window_id": 999})
    check("explicit_window_override_wins", service.calls[1][1].get("window_id") == 999)

    # ---- 7. serialisation + fallback bookkeeping ---------------------------
    d = ctx.to_dict()
    for key in ("task_id", "app", "pid", "hwnd", "content_hwnd", "window_title",
                "accessibility_root", "focus", "frame", "transaction_id",
                "strategy", "fallbacks", "stale"):
        check(f"to_dict_has_{key}", key in d)
    ctx.note_fallback("grounded-vision")
    ctx.note_fallback("grounded-vision")
    check("fallback_deduplicated", ctx.fallbacks == ["grounded-vision"])

    # ---- 8. observe refreshes frame identity -------------------------------
    ac.win = FakeWin([host, content, other])
    frame = ctx.observe()
    check("observe_records_hwnd", frame.get("hwnd") == 100)
    check("observe_records_stale_flag", "stale" in frame)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Track A: Application Interaction Context ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
