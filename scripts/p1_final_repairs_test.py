"""Focused regression coverage for the final non-owner repair pass.

Covers, without launching a browser (except where a real one is required):
  * window.close verification (closed vs minimized vs hidden vs still-open)
  * the GENIE-owned browser profile MUST be an absolute path (the CDP root cause)
  * cdp.profile_in_use never attaches to anything and reports no false holders
  * browser.upload canonical `files` contract + empty/missing path rejection
  * browser_select reachability through the canonical manifest and the bridge
  * act() re-activates the target and keeps a verified DOM fallback
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" -- {detail}" if detail and not cond else ""), flush=True)


def main():
    from computer.verifier import verify, has_verifier, REGISTRY
    from browser.service import BrowserService
    from browser import cdp

    # ---------------------------------------------------------- window.close
    check("window_close_has_verifier", has_verifier("window.close"))
    check("window_close_in_registry", "window.close" in REGISTRY)

    W = {"hwnd": 4242, "process": "notepad.exe", "class_name": "Notepad", "visible": True}
    r_closed = verify("window.close", {"hwnd": 4242}, {"ok": True, "hwnd": 4242},
                      {"windows": [W]}, {"windows": []})
    check("window_close_accepts_destroyed", r_closed.verified, r_closed.detail)

    r_min = verify("window.close", {"hwnd": 4242}, {"ok": True, "hwnd": 4242},
                   {"windows": [W]}, {"windows": [dict(W, minimized=True)]})
    check("window_close_rejects_minimized", not r_min.verified and "minimized" in r_min.detail,
          r_min.detail)

    r_hidden = verify("window.close", {"hwnd": 4242}, {"ok": True, "hwnd": 4242},
                      {"windows": [W]}, {"windows": [dict(W, visible=False)]})
    check("window_close_rejects_hidden", not r_hidden.verified and "hidden" in r_hidden.detail,
          r_hidden.detail)

    r_open = verify("window.close", {"hwnd": 4242}, {"ok": True, "hwnd": 4242},
                    {"windows": [W]}, {"windows": [W]})
    check("window_close_rejects_still_open", not r_open.verified, r_open.detail)

    r_none = verify("window.close", {}, {"ok": True}, {"windows": [W]}, {"windows": []})
    check("window_close_rejects_missing_hwnd", not r_none.verified, r_none.detail)

    # -------------------------------------------- browser profile is absolute
    svc = BrowserService()
    from core.paths import data_dir
    check("default_profile_is_absolute", svc.profile_dir.is_absolute(), str(svc.profile_dir))
    check("default_profile_under_user_data", svc.profile_dir == data_dir() / "workspace" / "browser-profile",
          str(svc.profile_dir))

    rel = BrowserService(profile_dir="relative-profile")
    check("explicit_relative_profile_made_absolute", rel.profile_dir.is_absolute(),
          str(rel.profile_dir))

    from browser import service as bservice
    bservice.reset_browser()
    got = bservice.get_browser(workspace_root=tempfile.mkdtemp())
    check("rebind_profile_is_absolute", got.profile_dir.is_absolute(), str(got.profile_dir))
    bservice.reset_browser()

    # ------------------------------------------------- profile_in_use is safe
    check("profile_in_use_callable", callable(cdp.profile_in_use))
    holders = cdp.profile_in_use(tempfile.mkdtemp())
    check("profile_in_use_empty_for_unused_profile", holders == [], str(holders))

    # ------------------------------------------------------- upload contract
    no_files = svc.upload_file({"selector": "#file"})
    check("upload_rejects_missing_path", not no_files.get("ok"), str(no_files)[:160])
    empty_list = svc.upload_file({"files": [], "selector": "#file"})
    check("upload_rejects_empty_list", not empty_list.get("ok"), str(empty_list)[:160])
    blank = svc.upload_file({"files": ["   "], "selector": "#file"})
    check("upload_rejects_blank_path", not blank.get("ok"), str(blank)[:160])
    missing = svc.upload_file({"files": [str(Path(tempfile.gettempdir()) / "definitely-absent-xyz.bin")],
                               "selector": "#file"})
    check("upload_rejects_missing_file", not missing.get("ok") and "not found" in missing.get("error", ""),
          str(missing)[:160])
    # a directory must never be accepted as a file
    directory = svc.upload_file({"files": [tempfile.gettempdir()], "selector": "#file"})
    check("upload_rejects_directory", not directory.get("ok"), str(directory)[:160])

    # ------------------------------------------------------ select exposure
    from computer.capability_manifest import validate, model_tools
    from computer.tool_bridge import NAMES, BRIDGE_CAPABILITY
    from computer.service import SCOPE_BY_CAPABILITY
    from computer.planner import CHAINS

    check("manifest_consistent", len(validate()) == 0, str(validate())[:200])
    names = {t["name"] for t in model_tools("chat")}
    check("browser_select_exposed", "browser_select" in names, str(sorted(names))[:200])
    check("browser_select_in_bridge", "browser_select" in NAMES)
    check("browser_select_maps_to_capability",
          BRIDGE_CAPABILITY.get("browser_select") == "browser.select",
          str(BRIDGE_CAPABILITY.get("browser_select")))
    check("browser_select_registered",
          "browser.select" in SCOPE_BY_CAPABILITY and "browser.select" in CHAINS
          and has_verifier("browser.select"))

    # ------------------------------------------------- act() hardening shape
    src = io.open(Path(__file__).resolve().parents[1] / "browser/service.py", encoding="utf-8").read()
    check("act_reactivates_target", "cdp.activate_page(self.port, self._active_target_id())" in src)
    check("act_has_dom_fallback", "data-genie-click-target" in src)
    check("act_reports_dispatch_path", '"dispatch": dispatch' in src)
    check("act_guards_owner_browser_activation",
          "if not self._owner_browser:" in src and "activate_page" in src)

    # ------------------------------------------------- upload uses `files`
    check("upload_reads_files_contract", 'params.get("files")' in src)

    # ------------------------------------------------- B02 intent source fix
    # The guard must judge the OWNER'S UTTERANCE, not the packed context prompt.
    # `messages[-1]` is the assembled packet, which contains capability/context
    # words, so scanning it made every question look like an action.
    from core.tool_dialogue import action_requested, run as tool_run

    packed = ("GENIE context packet: available capabilities include read, search, "
              "open, send, download, volume, settings. User said: what is the capital of France")
    check("packed_prompt_would_look_like_action", action_requested(packed))
    check("question_is_not_action_intent", not action_requested("what is the capital of France"))

    class _Completion:
        text = "Paris is the capital of France."
        tool_calls = []
        tool_error = ""
        provider_id = "stub"
        model = "stub-1"
        raw = {}
        tool_protocol = ""

    class _Gateway:
        def complete(self, *a, **k):
            return _Completion()

    class _Req:
        pass

    ctx = type("C", (), {"trace_id": "t", "dry_run": False})()
    cancel = __import__("threading").Event()

    answer = tool_run(_Gateway(), None, ctx, _Req(),
                      [{"role": "system", "content": "s"},
                       {"role": "user", "content": packed}],
                      cancel, user_text="what is the capital of France")
    check("conversation_answered_not_blocked",
          "Paris" in answer and "could not complete that action" not in answer, str(answer)[:160])

    blocked = tool_run(_Gateway(), None, ctx, _Req(),
                       [{"role": "system", "content": "s"},
                        {"role": "user", "content": "open notepad"}],
                       cancel, user_text="open notepad")
    check("action_without_receipt_still_blocked",
          "could not complete that action" in blocked, str(blocked)[:160])

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nFinal repairs: {total - len(failed)}/{total} passed", flush=True)
    for name, _, detail in failed:
        print(f"  FAILED: {name} -- {detail}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
