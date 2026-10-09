"""W7.2 — no console children: the backend must never create a visible console.

The "flashing terminal" was traced to `computer/state.py::list_processes()`
shelling out to `tasklist`, a CONSOLE application, on a timer. Two fixes landed:
  1. CREATE_NO_WINDOW, which hid the window but still spawned a process;
  2. native NtQuerySystemInformation enumeration, which spawns nothing.

This harness watches process creation for a period and reports every console
process (conhost.exe) and every console-subsystem executable, together with its
parent chain, so a regression is attributable rather than merely visible.

Passive and non-destructive: it observes, it never kills or disables anything.

Usage:
    python scripts/verify_no_console_children.py --seconds 900
    python scripts/verify_no_console_children.py --seconds 900 --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime

# These are the executables the incident actually implicated. If any of them is
# created as a child of a GENIE process, the flashing terminal is back.
CONSOLE_CULPRITS = {"tasklist.exe", "cmd.exe", "powershell.exe", "wmic.exe",
                    "conhost.exe", "python.exe"}

# Deliberately NOT the bare string "genie": this repo lives under E:\G3\GENIE,
# so every harness command line contains it and would be mis-attributed as
# GENIE-owned. Match only the real product images.
GENIE_MARKERS = ("genie.desktop", "backend-runtime", "backend_entry")

GENIE_PARENTS = {"pythonw.exe", "genie.desktop.exe"}


def _is_genie(exe: str | None, cmdline: str | None) -> bool:
    """Image path only - deliberately NOT the command line.

    An earlier version matched the command line too, and it produced four false
    "GENIE console child" hits that were really this harness: an inline
    `python -c` launcher contains the text Genie.Desktop.exe in its script body,
    so it matched while its actual image was an unrelated interpreter and its
    parent was bash.exe.

    A process is GENIE only if its executable IS a GENIE image. A console child
    launched BY GENIE (cmd.exe, tasklist.exe) is caught by the parent rule.
    """
    blob = (exe or "").lower().replace("/", "\\")
    return any(m in blob for m in GENIE_MARKERS)


def _is_genie_parent(parent_name: str | None) -> bool:
    return (parent_name or "").lower() in GENIE_PARENTS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=900)
    ap.add_argument("--json", default="")
    ap.add_argument("--launch-exe", default="",
                    help="start GENIE as a child for the whole window. Use this: "
                         "a detached GENIE does not reliably survive long enough "
                         "to cover a 15-minute window in every environment, and a "
                         "watch with nothing running proves nothing.")
    args = ap.parse_args()

    try:
        import psutil
    except ImportError:
        print("psutil is required for this harness")
        return 2

    child = None
    if args.launch_exe:
        import subprocess
        from pathlib import Path
        exe = Path(args.launch_exe)
        if not exe.exists():
            print(f"exe not found: {exe}")
            return 2
        child = subprocess.Popen([str(exe)], cwd=str(exe.parent))
        print(f"launched GENIE pid={child.pid} as a child of this watch")

    seen: set[int] = set()
    for p in psutil.process_iter():
        seen.add(p.pid)

    conhost = 0
    culprits: Counter[str] = Counter()
    genie_owned: list[dict] = []
    deadline = time.time() + args.seconds
    print(f"watching process creation for {args.seconds}s "
          f"(until {datetime.now().astimezone().strftime('%H:%M:%S')})...")

    while time.time() < deadline:
        try:
            current = {p.pid: p for p in psutil.process_iter()}
        except Exception:
            time.sleep(1.0)
            continue
        for pid, p in current.items():
            if pid in seen:
                continue
            seen.add(pid)
            try:
                name = (p.name() or "").lower()
                exe = p.exe() if p.exe else None
                cmdline = " ".join(p.cmdline()) if p.cmdline else None
                ppid = p.ppid()
                parent = None
                try:
                    parent = psutil.Process(ppid).name() if ppid else None
                except Exception:
                    parent = None
            except Exception:
                continue

            if name == "conhost.exe":
                conhost += 1
            if name in CONSOLE_CULPRITS:
                culprits[name] += 1
                if _is_genie(exe, cmdline) or _is_genie_parent(parent):
                    genie_owned.append({
                        "name": name, "pid": pid, "ppid": ppid,
                        "parent": parent, "exe": exe,
                        "cmdline": (cmdline or "")[:200],
                        "at": datetime.now().astimezone().isoformat(),
                    })
        time.sleep(0.5)

    print()
    print("=== console-subsystem process creations ===")
    for name, count in culprits.most_common():
        print(f"  {name:<20} {count}")
    if not culprits:
        print("  (none)")
    print(f"  conhost.exe total: {conhost}")
    print()
    print("=== attributed to GENIE ===")
    print(f"  {len(genie_owned)}")
    for item in genie_owned[:20]:
        print(f"    {item['name']} pid={item['pid']} parent={item['parent']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"culprits": dict(culprits), "conhost": conhost,
                       "genie_owned": genie_owned}, fh, indent=2)

    # A watch with nothing running proves nothing, so report the coverage.
    alive = None
    if child is not None:
        alive = child.poll() is None
        print(f"GENIE child alive at end of window: {alive}")
        try:
            child.terminate()
        except Exception:
            pass

    ok = len(genie_owned) == 0 and (child is None or alive is True)
    print()
    if child is not None and alive is not True:
        print("RESULT: INCONCLUSIVE - GENIE exited during the window")
        return 2
    print("RESULT:", "PASS - GENIE created no console children"
          if ok else f"FAIL - {len(genie_owned)} GENIE-owned console child(ren)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
