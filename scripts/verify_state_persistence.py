"""W7.1 — prove the native client is not maintaining fake local state.

    1. launch the native client and wait for the backend
    2. change a durable backend item (a model role) through the backend API
    3. exit GENIE completely (a real WM_CLOSE, not a kill)
    4. verify zero owned processes remain
    5. relaunch
    6. confirm the change survived

If the value survives a full exit and relaunch, it lived in the backend, not in
the WPF process.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8787"
HEALTH = BASE + "/health"
ROLES = BASE + "/api/models/roles"
MEMORY_WRITE = BASE + "/api/memory/write"
WM_CLOSE = 0x0010

# This machine has HTTP_PROXY set, and urllib would happily route localhost
# through it - which returns 502 and looks exactly like a broken backend.
# Every call here must bypass any proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url: str, timeout: float = 5.0):
    with _opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: float = 15.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health_ok(timeout: float = 2.0) -> bool:
    try:
        with _opener.open(HEALTH, timeout=timeout):
            return True
    except Exception:
        return False


def genie_pids() -> set:
    import psutil
    return {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() in ("genie.desktop.exe", "pythonw.exe")}


def close_windows_of(pid: int) -> int:
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    found = []

    def cb(h, _l):
        proc = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(proc))
        if proc.value == pid and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    for h in found:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return len(found)


def wait_health(timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if health_ok():
            return True
        time.sleep(1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--role", default="fast")
    args = ap.parse_args()

    exe = pathlib.Path(args.exe)
    if not exe.exists():
        print(f"SKIP: {exe} not found")
        return 2

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))

    # Start from a clean slate. If an instance is already running, the launch
    # below would be the SECOND copy: it correctly hands off and exits, leaving
    # this script holding a pid with no window to close - which reads as a
    # lifecycle failure but is actually the single-instance guard working.
    existing = genie_pids()
    if existing:
        print(f"closing {len(existing)} pre-existing GENIE process(es) first")
        # Ask the app to close itself: it then stops the backend it owns.
        # A hard kill cannot run OnExit, which orphans pythonw.exe processes
        # that hold the database and have no window to send WM_CLOSE to.
        try:
            subprocess.run([str(exe), "--genie-shutdown"], cwd=str(exe.parent),
                           timeout=25, capture_output=True)
        except Exception as exc:
            print(f"   graceful close request failed: {exc}")
        for pid in existing:
            close_windows_of(pid)
        end = time.time() + 40
        while time.time() < end and genie_pids():
            time.sleep(1)
        # Anything still here has no window (an orphaned backend from a previous
        # hard kill). Terminate it so the counts below mean something.
        left = genie_pids()
        if left:
            print(f"   terminating {len(left)} windowless leftover process(es)")
            for pid in left:
                try:
                    psutil.Process(pid).terminate()
                except Exception:
                    pass
            end = time.time() + 20
            while time.time() < end and genie_pids():
                time.sleep(1)
        if genie_pids():
            print("could not close the existing instance")
            return 1

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        check("first launch reaches backend", wait_health(60))

        before = get_json(ROLES)
        roles_before = (before.get("roles") or {})
        print("   roles before:", json.dumps(roles_before))

        # Pick a provider+model the backend actually knows, so set_role validates.
        providers = get_json(BASE + "/api/providers")
        items = providers if isinstance(providers, list) else (providers.get("providers") or [])
        current = (roles_before.get(args.role) or {})
        current_model = current.get("model_id") or ""

        # Pick a DIFFERENT model than the role currently has, otherwise the
        # "change" is a no-op reassignment and proves nothing about persistence.
        chosen = None
        for p in items:
            for m in (p.get("models") or []):
                mid = m.get("model_id") or ""
                if mid and mid != current_model:
                    chosen = (p.get("id"), mid)
                    break
            if chosen:
                break
        if not chosen:
            print("SKIP: backend exposes no alternative provider+model to assign")
            return 2
        provider_id, model_id = chosen
        print(f"   assigning {args.role}: {current_model!r} -> {provider_id} / {model_id}")

        post_json(ROLES, {"role": args.role, "provider_id": provider_id, "model_id": model_id})
        after_set = get_json(ROLES)
        set_ok = (after_set.get("roles") or {}).get(args.role, {}).get("model_id") == model_id
        check("role change accepted by backend", set_ok, json.dumps(after_set.get("roles")))

        # Second durable item, deliberately a different subsystem: a memory
        # record. Proving two independent stores survive rules out the
        # alternative explanation that the role merely lived in a config file
        # the client happens to re-read.
        marker = f"persistence-probe-{int(time.time())}"
        post_json(MEMORY_WRITE, {"type": "semantic", "entity": marker,
                                 "value": f"durable {marker}", "confidence": 0.9})
        check("memory write accepted by backend", True, marker)

        # The backend can report healthy before the WPF window exists, so wait
        # for the window rather than posting WM_CLOSE into a race.
        closed = 0
        end = time.time() + 30
        while time.time() < end and closed == 0:
            closed = close_windows_of(proc.pid)
            if closed:
                break
            time.sleep(1)
        check("graceful close issued", closed > 0, f"{closed} window(s)")

        end = time.time() + 30
        while time.time() < end and genie_pids():
            time.sleep(1)
        check("zero owned processes after exit", len(genie_pids()) == 0, genie_pids())
        check("backend stopped after exit", not health_ok())
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    # --- relaunch and confirm the value survived ---
    proc2 = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        check("relaunch reaches backend", wait_health(60))
        after = get_json(ROLES)
        survived = (after.get("roles") or {}).get(args.role, {}).get("model_id") == model_id
        check("role survived full exit + relaunch", survived, json.dumps(after.get("roles")))

        hits = get_json(BASE + "/api/memory?q=" + urllib.parse.quote(marker))
        items = hits.get("hits") or []
        found = any(marker in json.dumps(h) for h in items)
        check("memory record survived full exit + relaunch", found,
              f"{len(items)} hit(s)")
    finally:
        try:
            if proc2.poll() is None:
                close_windows_of(proc2.pid)
                time.sleep(5)
                proc2.kill()
        except Exception:
            pass

    passed = sum(1 for _, ok in results if ok)
    print(f"\nRESULT {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
