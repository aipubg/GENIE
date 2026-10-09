"""P0.1 — Terminal-respawn incident: passive process-creation capture harness.

NON-DESTRUCTIVE. This script only *observes*. It never kills, disables or
deletes anything. It answers:

  * which executable is flashing (image path + command line)
  * who spawned it (PPID + full parent chain -> the root respawner)
  * how often it respawns (creation timestamps / inter-arrival intervals)
  * how long each instance lives and what exit code it produces

Exit codes require holding an OS handle to the process, so we open one
(SYNCHRONIZE | QUERY_LIMITED_INFORMATION) the first time a PID is seen and
read GetExitCodeProcess after it signals. Handles are capped and closed.

Usage:
    python scripts/p0_process_capture.py --seconds 600 --out artifacts/p0_capture.jsonl
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from datetime import datetime

import psutil

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x00000102
WAIT_OBJECT_0 = 0x00000000
MAX_TRACKED_HANDLES = 400

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenProcess.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_k32.GetExitCodeProcess.restype = wt.BOOL
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CloseHandle.restype = wt.BOOL


def _open_handle(pid: int):
    h = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return int(h) if h else None


class Recorder:
    def __init__(self, out_path: str):
        self.out = open(out_path, "a", encoding="utf-8")
        self.seen: dict[int, dict] = {}
        self.handles: dict[int, int] = {}
        self.closed: set[int] = set()

    def write(self, obj: dict) -> None:
        self.out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.out.flush()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _safe(fn, default=""):
        try:
            v = fn()
            return v if v is not None else default
        except Exception:
            return default

    def parent_chain(self, pid: int) -> list[dict]:
        """Walk up the parent chain (best-effort; PIDs may be recycled)."""
        chain, guard, cur = [], 0, pid
        while cur and guard < 12:
            try:
                p = psutil.Process(cur)
                with p.oneshot():
                    name = p.name()
                    exe = self._safe(p.exe)
                    cmd = self._safe(lambda: " ".join(p.cmdline()))
                    ppid = p.ppid()
                chain.append({"pid": cur, "ppid": ppid, "name": name,
                              "exe": exe, "cmd": cmd[:400]})
                if ppid in (0, cur) or ppid in [c["pid"] for c in chain]:
                    break
                cur = ppid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                chain.append({"pid": cur, "ppid": None, "name": "<gone>",
                              "exe": "", "cmd": ""})
                break
            guard += 1
        return chain

    # ------------------------------------------------------------------- events
    def on_create(self, pid: int) -> None:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                name = p.name()
                exe = self._safe(p.exe)
                cmd = self._safe(lambda: p.cmdline())
                cwd = self._safe(p.cwd)
                user = self._safe(p.username)
                ct = p.create_time()
                ppid = p.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        rec = {
            "event": "create",
            "wall": datetime.now().isoformat(timespec="milliseconds"),
            "pid": pid, "ppid": ppid, "name": name, "exe": exe,
            "cmd": cmd, "cwd": cwd, "user": user,
            "created": datetime.fromtimestamp(ct).isoformat(timespec="milliseconds"),
            "parent_chain": self.parent_chain(ppid) if ppid else [],
        }
        self.seen[pid] = rec
        if len(self.handles) < MAX_TRACKED_HANDLES:
            h = _open_handle(pid)
            if h:
                self.handles[pid] = h
        self.write(rec)

    def on_exit(self, pid: int) -> None:
        rec = self.seen.pop(pid, None)
        h = self.handles.pop(pid, None)
        code = None
        if h:
            out = wt.DWORD()
            if _k32.GetExitCodeProcess(wt.HANDLE(h), ctypes.byref(out)):
                code = int(out.value)
            _k32.CloseHandle(wt.HANDLE(h))
        if rec is None:
            return
        try:
            created = datetime.fromisoformat(rec["created"]).timestamp()
            lifetime = round(time.time() - created, 4)
        except Exception:
            lifetime = None
        rec.update({"event": "exit", "wall": datetime.now().isoformat(timespec="milliseconds"),
                    "exit_code": code, "lifetime_s": lifetime})
        self.write(rec)

    def reap(self) -> None:
        for pid in list(self.seen.keys()):
            if not psutil.pid_exists(pid):
                self.on_exit(pid)
                continue
            h = self.handles.get(pid)
            if h is None:
                continue
            if _k32.WaitForSingleObject(wt.HANDLE(h), 0) == WAIT_OBJECT_0:
                self.on_exit(pid)

    def close(self) -> None:
        for pid, h in list(self.handles.items()):
            try:
                _k32.CloseHandle(wt.HANDLE(h))
            except Exception:
                pass
        self.handles.clear()
        try:
            self.out.close()
        except Exception:
            pass


# Paths used by the agent/tooling that runs this investigation. Their own shell
# and interpreter traffic would otherwise bury the one process we care about.
AGENT_MARKERS = (
    "portablegit", ".workbuddy-ai", "workbuddyai", "binaries\\python",
    "binaries\\node", "python312", "appdata\\local\\temp\\genie", "codebuddy",
)

# A visible console window is hosted by one of these. Counted and reported,
# never filtered away - the flashing window would be one of them.
CONSOLE_HOSTS = ("conhost.exe", "openconsole.exe", "windowsterminal.exe")


def _is_agent_noise(rec: dict) -> bool:
    blob = " ".join([str(rec.get("exe") or "")] +
                    [str((c or {}).get("exe") or "") for c in rec.get("parent_chain") or []])
    low = blob.lower()
    return any(marker in low for marker in AGENT_MARKERS)


def summarize(path: str) -> str:
    """Human-readable summary: what ran, what survived filtering, what flashed."""
    import json as _json
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(_json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return f"no capture file at {path}"

    creates = [r for r in rows if r.get("event") == "create"]
    exits = {r.get("pid"): r for r in rows if r.get("event") == "exit"}

    # A flashing console window would appear as one of these. They are counted
    # separately rather than dropped: on a machine running an agent they are
    # usually the agent's own shells, but they are never silently discarded.
    def _is_console_host(rec: dict) -> bool:
        return str(rec.get("name") or "").lower() in CONSOLE_HOSTS

    consoles = [r for r in creates if _is_console_host(r)]
    interesting = [r for r in creates
                   if not _is_agent_noise(r) and not _is_console_host(r)]

    out = []
    out.append(f"capture file      : {path}")
    out.append(f"process creations : {len(creates)}")
    out.append(f"candidates        : {len(interesting)} (tooling noise removed)")
    out.append(f"console hosts     : {len(consoles)} "
               f"(conhost/OpenConsole/WindowsTerminal - a flashing window would "
               f"show up here)")
    if consoles:
        by_name = {}
        for rec in consoles:
            key = f"{rec.get('name')} <- {(rec.get('parent_chain') or [{}])[0].get('name') or '?'}"
            by_name[key] = by_name.get(key, 0) + 1
        out.append("  console host breakdown (name <- parent):")
        for key, count in sorted(by_name.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"    {count:5d}  {key}")
    out.append("")

    if not interesting:
        out.append("No non-tooling process was created during the capture window.")
        out.append("The flashing window did NOT recur while this was running.")
    else:
        out.append("Non-tooling processes created (candidates):")
        for rec in interesting:
            life = None
            code = None
            ex = exits.get(rec.get("pid"))
            if ex:
                life = ex.get("lifetime_s")
                code = ex.get("exit_code")
            chain = " <- ".join(
                f"{(c or {}).get('name') or '?'}({(c or {}).get('pid') or '?'})"
                for c in (rec.get("parent_chain") or [])[:4]
            )
            out.append("")
            out.append(f"  time     : {rec.get('created')}")
            out.append(f"  image    : {rec.get('exe') or rec.get('name')}")
            out.append(f"  pid/ppid : {rec.get('pid')}/{rec.get('ppid')}")
            out.append(f"  cmd      : {' '.join(rec.get('cmd') or [])[:200]}")
            out.append(f"  lifetime : {life}s   exit code: {code}")
            out.append(f"  parents  : {chain or '<none>'}")

    # Short-lived processes are the flashing-window signature. Console hosts are
    # included here on purpose: that is exactly what a flashing window is.
    flashy = []
    for rec in interesting + consoles:
        ex = exits.get(rec.get("pid"))
        if ex and ex.get("lifetime_s") is not None and ex["lifetime_s"] < 2.0:
            flashy.append((rec, ex))
    out.append("")
    out.append(f"short-lived (<2s) candidates incl. console hosts: {len(flashy)}")
    out.append("(many will be an agent's own shells if one was running)")
    for rec, ex in flashy[:20]:
        out.append(f"  {rec.get('created')}  {rec.get('name')}  "
                   f"pid={rec.get('pid')}  life={ex.get('lifetime_s')}s  "
                   f"exit={ex.get('exit_code')}  {rec.get('exe') or ''}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--interval", type=float, default=0.12)
    ap.add_argument("--out", default="artifacts/p0_process_capture.jsonl")
    ap.add_argument("--no-summary", action="store_true",
                    help="skip the end-of-run summary")
    ap.add_argument("--summary-only", metavar="JSONL",
                    help="do not capture; just summarize an existing file")
    args = ap.parse_args()

    if args.summary_only:
        print(summarize(args.summary_only))
        return 0

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    rec = Recorder(args.out)

    # prime the baseline so we only report NEW processes
    baseline = set(psutil.pids())
    print(f"[capture] baseline={len(baseline)} procs, watching {args.seconds}s "
          f"@ {args.interval}s -> {args.out}", flush=True)

    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            try:
                current = set(psutil.pids())
            except Exception:
                time.sleep(args.interval)
                continue
            for pid in current - baseline - set(rec.seen):
                rec.on_create(pid)
            baseline = current
            rec.reap()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        rec.reap()
        rec.close()

    print(f"[capture] done. events written to {args.out}", flush=True)
    if not args.no_summary:
        text = summarize(args.out)
        print()
        print(text)
        summary_path = args.out + ".summary.txt"
        try:
            with open(summary_path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            print(f"\n[capture] summary written to {summary_path}", flush=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
