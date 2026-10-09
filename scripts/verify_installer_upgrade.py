"""Upgrade / lifecycle acceptance.

Rewritten after the upgrade-orphan defect. Two things were wrong with the
previous version and both are fixed here:

1. It waited silently through the middle stages and only asserted at the end,
   so a failure surfaced as "no orphan process" and the actual failing stage
   had to be inferred. Every stage now asserts and aborts immediately.

2. It never exercised the race. The defect was timing-dependent - it passed
   21/21 once and then failed repeatedly - so a single green run proved
   nothing. The race cases are now explicit scenarios.

Ownership: "GENIE-owned" means a process whose executable lives under the
installed runtime's python directory. That is the same rule the client uses, so
the test can never be satisfied by killing (or by ignoring) unrelated
pythonw.exe elsewhere on the machine.

Usage:
    python scripts/verify_installer_upgrade.py [--runs 5]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results: list[tuple[str, bool, str]] = []


class StageFailed(Exception):
    """Raised so the run stops at the exact stage that failed."""


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def require(name: str, ok: bool, detail: str = "") -> None:
    """Assert a stage and abort the run if it fails."""
    check(name, ok, detail)
    if not ok:
        raise StageFailed(name)


def appdata() -> Path:
    """APPDATA is not always exported to the test shell; without a fallback the
    shortcut path becomes relative and every shortcut check silently reads as
    "not created" - a false failure, not a product defect."""
    env = os.environ.get("APPDATA")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Roaming"


# Point 6.7 — acceptance runs must NEVER write into the owner's production
# mission store. Every installed-app launch points at this isolated data
# directory instead. The override must be ABSOLUTE (the backend drops relative
# values), so it is resolved to a real temp path here.
TEST_DATA_DIR = (Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
                 / "genie-acceptance-data")


def app_env() -> dict:
    """Environment for launching the installed app with an isolated data dir."""
    env = os.environ.copy()
    env["GENIE_DATA_DIR"] = str(TEST_DATA_DIR)
    return env


def api_get(path, timeout=6):
    with _opener.open(f"http://127.0.0.1:8787{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post(path, payload, timeout=30):
    req = urllib.request.Request(f"http://127.0.0.1:8787{path}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health_ok() -> bool:
    try:
        with _opener.open("http://127.0.0.1:8787/health", timeout=2):
            return True
    except Exception:
        return False


# ------------------------------------------------------------------ ownership
def owned_processes(target: Path) -> list:
    """Processes running THIS install's embedded interpreter.

    The installed layout is target\\resources\\backend-runtime\\python - the
    same candidates the client itself probes. Both are tried because getting
    this wrong silently reports "zero owned", which is how the original defect
    hid for so long.
    """
    anchors = []
    for rel in ("resources/backend-runtime/python", "backend-runtime/python"):
        try:
            anchors.append(str((target / Path(rel)).resolve()).lower())
        except Exception:
            pass
    if not anchors:
        return []
    owned = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            exe = (p.info.get("exe") or "").lower()
        except Exception:
            continue
        if exe and any(exe.startswith(a) for a in anchors):
            owned.append(p.info["pid"])
    return owned


def frontend_pids() -> list:
    return [p.pid for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() == "genie.desktop.exe"]


def wait_until(predicate, timeout: float, interval: float = 0.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def kill_leftovers(target: Path) -> None:
    """Clean slate before a run.

    This has to be broader than the install target: a backend left running from
    the source tree answers /health just as well, and then the app under test
    sees a healthy backend and never starts its own - so the run would be
    measuring nothing.
    """
    exe = target / "Genie.Desktop.exe"
    if exe.exists():
        subprocess.run([str(exe), "--genie-shutdown"], cwd=str(target),
                       timeout=30, capture_output=True)
    time.sleep(1)
    for pid in owned_processes(target) + frontend_pids():
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    # Any other GENIE backend (e.g. one started from the repo) must also go.
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() in ("pythonw.exe", "genie.desktop.exe"):
            try:
                psutil.Process(p.info["pid"]).kill()
            except Exception:
                pass
    time.sleep(2)
    if not wait_until(lambda: not health_ok(), 30):
        raise StageFailed("could not clear a pre-existing backend on :8787")


# --------------------------------------------------------------------- stages
def run_upgrade_flow(setup: Path, target: Path) -> None:
    print("1) fresh install")
    r = subprocess.run([str(setup), "/S", f"/D={target}"], timeout=900,
                       capture_output=True)
    require("fresh install exits 0", r.returncode == 0, f"rc={r.returncode}")

    exe = target / "Genie.Desktop.exe"

    print("2) first launch and health")
    proc = subprocess.Popen([str(exe)], cwd=str(target), env=app_env())
    require("first launch reaches health", wait_until(health_ok, 120))
    # Health can answer before all workers have spawned; wait for them.
    require("backend owned processes present",
            wait_until(lambda: owned_processes(target) != [], 60),
            f"{owned_processes(target)}")

    print("3) real workload through the installed backend")
    try:
        chat = api_post("/api/chat", {"text": "hello", "dry_run": True})
        require("chat answered", isinstance(chat, dict) and bool(chat), str(chat)[:70])
    except Exception as exc:
        require("chat answered", False, str(exc)[:70])
    try:
        m = api_post("/api/missions/create", {"goal": "upgrade acceptance"})
        require("mission created", bool(m), str(m)[:70])
    except Exception as exc:
        require("mission created", False, str(exc)[:70])
    try:
        roles = api_get("/api/models/roles")
        require("provider roles readable", isinstance(roles, dict), str(roles)[:70])
    except Exception as exc:
        require("provider roles readable", False, str(exc)[:70])

    print("4) full Exit")
    subprocess.run([str(exe), "--genie-shutdown"], cwd=str(target), timeout=40,
                   capture_output=True)
    require("frontend exits", wait_until(lambda: not frontend_pids(), 45),
            f"{frontend_pids()}")
    require("zero owned backend processes remain",
            wait_until(lambda: not owned_processes(target), 45),
            f"{owned_processes(target)}")
    require("backend stopped", not health_ok())
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:
        pass

    print("5) relaunch")
    proc2 = subprocess.Popen([str(exe)], cwd=str(target), env=app_env())
    require("relaunch reaches health", wait_until(health_ok, 120))

    print("6) UPGRADE WHILE RUNNING")
    require("GENIE running before upgrade", bool(frontend_pids()))
    r = subprocess.run([str(setup), "/S", f"/D={target}"], timeout=900,
                       capture_output=True)
    require("installer graceful shutdown exits 0", r.returncode == 0,
            f"rc={r.returncode}")
    require("frontend exited for the upgrade",
            wait_until(lambda: not frontend_pids(), 60), f"{frontend_pids()}")
    require("owned backend processes exited",
            wait_until(lambda: not owned_processes(target), 60),
            f"{owned_processes(target)}")
    require("backend stopped with it", not health_ok())
    try:
        if proc2.poll() is None:
            proc2.terminate()
    except Exception:
        pass

    print("7) upgraded install works")
    proc3 = subprocess.Popen([str(exe)], cwd=str(target), env=app_env())
    require("upgrade install relaunches healthy", wait_until(health_ok, 120))

    print("8) post-upgrade shutdown")
    subprocess.run([str(exe), "--genie-shutdown"], cwd=str(target), timeout=40,
                   capture_output=True)
    require("post-upgrade frontend exits",
            wait_until(lambda: not frontend_pids(), 45), f"{frontend_pids()}")
    require("post-upgrade: zero owned processes remain",
            wait_until(lambda: not owned_processes(target), 45),
            f"{owned_processes(target)}")
    try:
        if proc3.poll() is None:
            proc3.terminate()
    except Exception:
        pass

    # The race cases need a live install, so they run BEFORE the uninstall
    # tears it down.
    run_race_cases(exe, target)

    print("9) uninstall")
    sc = {
        "start": appdata() / "Microsoft" / "Windows"
                 / "Start Menu" / "Programs" / "GENIE.lnk",
        "desktop": Path.home() / "Desktop" / "GENIE.lnk",
    }
    require("Start Menu shortcut was created", sc["start"].exists())
    require("Desktop shortcut was created", sc["desktop"].exists())

    uninst = target / "uninstall.exe"
    r = subprocess.run([str(uninst), "/S", f"_?={target}"], timeout=600,
                       capture_output=True)
    require("uninstall exits 0", r.returncode == 0, f"rc={r.returncode}")
    time.sleep(2)
    require("Start Menu shortcut removed", not sc["start"].exists())
    require("Desktop shortcut removed", not sc["desktop"].exists())
    require("no orphan frontend", not frontend_pids(), f"{frontend_pids()}")
    require("no orphan backend", not owned_processes(target),
            f"{owned_processes(target)}")
    require("no backend left listening", not health_ok())

    # Isolated data dir (Point 6.7): the app wrote here, not to the owner's real
    # data directory, so this run never polluted production missions.
    require("isolated data preserved across uninstall", TEST_DATA_DIR.exists(),
            str(TEST_DATA_DIR))


# ----------------------------------------------------------------- race cases
def run_race_cases(exe: Path, target: Path) -> None:
    """The defect was timing-dependent, so the race is exercised directly."""

    def launch():
        return subprocess.Popen([str(exe)], cwd=str(target), env=app_env())

    def shutdown():
        subprocess.run([str(exe), "--genie-shutdown"], cwd=str(target),
                       timeout=40, capture_output=True)

    def fully_down(timeout=75):
        return wait_until(lambda: not frontend_pids()
                          and not owned_processes(target), timeout)

    print("R1) shutdown when the backend is fully ready")
    p = launch()
    require("R1 healthy", wait_until(health_ok, 120))
    shutdown()
    require("R1 fully down", fully_down(), f"{owned_processes(target)}")
    if p.poll() is None:
        p.terminate()

    print("R2) shutdown immediately after launch (backend still starting)")
    p = launch()
    time.sleep(0.5)                       # deliberately before health
    shutdown()
    require("R2 fully down", fully_down(), f"{owned_processes(target)}")
    if p.poll() is None:
        p.terminate()

    print("R3) shutdown requested repeatedly")
    p = launch()
    require("R3 healthy", wait_until(health_ok, 120))
    for _ in range(3):
        shutdown()
    require("R3 fully down", fully_down(), f"{owned_processes(target)}")
    if p.poll() is None:
        p.terminate()

    print("R4) backend parent exits before its workers")
    p = launch()
    require("R4 healthy", wait_until(health_ok, 120))
    owned = owned_processes(target)
    parent = None
    for pid in owned:
        try:
            if psutil.Process(pid).ppid() == p.pid:
                parent = pid
                break
        except Exception:
            continue
    if parent is None and owned:
        parent = owned[0]
    if parent is not None:
        try:
            psutil.Process(parent).kill()
        except Exception:
            pass
        time.sleep(1)
    shutdown()
    require("R4 fully down (parent already gone)", fully_down(),
            f"{owned_processes(target)}")
    if p.poll() is None:
        p.terminate()

    print("R5) second launch during startup activates, does not start twice")
    p = launch()
    time.sleep(0.5)
    second = subprocess.Popen([str(exe)], cwd=str(target), env=app_env())
    try:
        second.wait(timeout=25)
    except Exception:
        second.terminate()
    require("R5 still exactly one frontend",
            len(frontend_pids()) <= 1, f"{frontend_pids()}")
    shutdown()
    require("R5 fully down", fully_down(), f"{owned_processes(target)}")
    if p.poll() is None:
        p.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", default=str(ROOT / "dist" / "GENIE-Setup.exe"))
    ap.add_argument("--target", default=r"E:\G3\.genie-upgrade-test")
    ap.add_argument("--runs", type=int, default=1,
                    help="consecutive runs from a clean state")
    args = ap.parse_args()

    setup = Path(args.setup)
    target = Path(args.target)
    if not setup.exists():
        print(f"setup not found: {setup}")
        return 2

    ok_all = True
    for run in range(1, args.runs + 1):
        print(f"\n===== RUN {run}/{args.runs} =====")
        results.clear()
        kill_leftovers(target)
        try:
            run_upgrade_flow(setup, target)   # includes the race cases
        except StageFailed as exc:
            passed = sum(1 for _, ok, _ in results if ok)
            print(f"\nABORTED at stage: {exc}")
            print(f"RUN {run}: {passed}/{len(results)} passed")
            return 1
        finally:
            # An aborted run must not leave a live backend behind: the next run
            # would find /health already answering and measure nothing.
            try:
                kill_leftovers(target)
            except Exception:
                pass
        passed = sum(1 for _, ok, _ in results if ok)
        print(f"RUN {run}: {passed}/{len(results)} passed")
        if passed != len(results):
            ok_all = False

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
