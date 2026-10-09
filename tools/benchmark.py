#!/usr/bin/env python3
"""GENIE benchmark: NEDLE2 routing latency, command-to-action latency, verification cost,
and process RAM/CPU.

    python tools/benchmark.py [--actions N]

Records exactly the numbers the roadmap asks for:
  * cold Needle latency (first decision after engine init)
  * warm Needle latency (steady state)
  * complete command-to-action latency (director -> capability -> verified)
  * action verification latency
  * process RAM / CPU
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def process_stats() -> dict:
    """RAM and CPU of this process via GetProcessMemoryInfo / GetProcessTimes."""
    try:
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        # K32GetProcessMemoryInfo lives in kernel32 on modern Windows; psapi is the legacy
        # location. Both need explicit prototypes or the call fails silently.
        ok = 0
        for lib, name in ((ctypes.windll.kernel32, "K32GetProcessMemoryInfo"),
                          (ctypes.windll.psapi, "GetProcessMemoryInfo")):
            fn = getattr(lib, name, None)
            if fn is None:
                continue
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            fn.restype = ctypes.c_int
            ok = fn(ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb)
            if ok:
                break
        if not ok:
            return {"error": "GetProcessMemoryInfo failed"}

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong),
                        ("dwHighDateTime", ctypes.c_ulong)]

        creation, exit_, kernel, user = (FILETIME(), FILETIME(), FILETIME(), FILETIME())
        ctypes.windll.kernel32.GetProcessTimes(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(creation), ctypes.byref(exit_),
            ctypes.byref(kernel), ctypes.byref(user))

        def _ft(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        cpu_s = (_ft(kernel) + _ft(user)) / 1e7
        return {"working_set_mb": round(counters.WorkingSetSize / 1e6, 1),
                "peak_working_set_mb": round(counters.PeakWorkingSetSize / 1e6, 1),
                "cpu_seconds": round(cpu_s, 2)}
    except Exception as exc:
        return {"error": str(exc)}


def bench_needle(rounds: int) -> dict:
    from core.contracts import CallContext
    from director.nedle2 import NeedleDirector

    d = NeedleDirector()
    if not d.available():
        return {"available": False}
    ctx = CallContext()
    queries = ["volume 30", "Chrome kholo", "phone ka next song", "open notepad",
               "mute", "next song"]
    t0 = time.time()
    d.classify(queries[0], ctx)                  # cold: engine init + first inference
    cold_ms = int((time.time() - t0) * 1000)

    samples = []
    for i in range(rounds):
        q = queries[i % len(queries)]
        started = time.time()
        d.classify(q, ctx)
        samples.append((time.time() - started) * 1000)
    status = d.status()
    d.close()
    return {
        "available": True,
        "engine": status.get("engine"),
        "engine_version": status.get("engine_version"),
        "cold_ms": cold_ms,
        "warm_avg_ms": round(statistics.mean(samples), 1),
        "warm_p50_ms": round(statistics.median(samples), 1),
        "warm_min_ms": round(min(samples), 1),
        "warm_max_ms": round(max(samples), 1),
        "samples": len(samples),
    }


def bench_actions(rounds: int) -> dict:
    from core.config import get_config
    from core.contracts import CallContext
    from core.db import get_db
    from computer.service import ComputerService
    from missions.service import LockService
    from security.audit import AuditLog
    from security.trust import TrustService

    cfg = get_config()
    db = get_db(cfg.db_path)
    audit = AuditLog(db)
    svc = ComputerService(trust=TrustService(db, audit=audit), audit=audit, db=db,
                          locks_service=LockService(db))
    ctx = CallContext(person_id="owner")
    out = {}

    # volume set (real, verified, restored)
    state = svc.execute(ctx, "system.audio.state", {})
    original = (state.data or {}).get("volume")
    if original is not None:
        lat, ver = [], []
        for _ in range(rounds):
            t0 = time.time()
            res = svc.execute(ctx, "system.volume.set", {"level": 20 if original != 20 else 30})
            lat.append((time.time() - t0) * 1000)
            ver.append((res.data or {}).get("verify_ms", 0))
        svc.execute(ctx, "system.volume.set", {"level": original})
        out["volume_set_ms"] = {"avg": round(statistics.mean(lat), 1),
                                "verification_avg": round(statistics.mean(ver), 1)}

    # clipboard set (real, verified)
    t0 = time.time()
    res = svc.execute(ctx, "clipboard.set", {"text": "genie-bench"})
    out["clipboard_set_ms"] = {"total": int((time.time() - t0) * 1000),
                               "verification": (res.data or {}).get("verify_ms", 0)}

    # app open (real, verified) — the full command-to-action loop
    t0 = time.time()
    res = svc.execute(ctx, "application.open", {"target": "notepad", "wait_s": 20})
    out["app_open_ms"] = {"total": int((time.time() - t0) * 1000),
                          "verification": (res.data or {}).get("verify_ms", 0),
                          "strategy": (res.data or {}).get("strategy_used"),
                          "verified": res.verified}
    t0 = time.time()
    res = svc.execute(ctx, "application.close", {"target": "notepad"})
    out["app_close_ms"] = {"total": int((time.time() - t0) * 1000),
                           "verification": (res.data or {}).get("verify_ms", 0)}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = {
        "ts": int(time.time()),
        "process_before": process_stats(),
        "needle": bench_needle(args.rounds),
        "actions": bench_actions(min(args.rounds, 5)),
        "process_after": process_stats(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        n = report["needle"]
        print("NEDLE2 routing")
        if n.get("available"):
            print(f"  cold (init+first decision) : {n['cold_ms']} ms")
            print(f"  warm avg / p50 / max       : {n['warm_avg_ms']} / {n['warm_p50_ms']} / "
                  f"{n['warm_max_ms']} ms   ({n['samples']} samples, engine {n['engine_version']})")
        else:
            print("  runtime unavailable")
        print("\nReal actions (command -> verified)")
        for name, value in report["actions"].items():
            print(f"  {name:18s}: {value}")
        print("\nProcess")
        print(f"  before: {report['process_before']}")
        print(f"  after : {report['process_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
