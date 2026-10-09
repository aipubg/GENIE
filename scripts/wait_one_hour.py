"""Verify the one-hour owner-like lifetime run at its due time.

Started detached from any harness and left to age. This only observes; it never
starts, stops or kills anything, so it cannot manufacture a pass.

Usage: python scripts/wait_one_hour.py
"""
from __future__ import annotations

import datetime
import json
import pathlib
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "artifacts" / "one_hour_run.json"
OUT = ROOT / "artifacts" / "one_hour_result.txt"
VERDICT = ROOT / "artifacts" / "one_hour_verdict.txt"


def health() -> bool:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:8787/health", timeout=5):
            return True
    except Exception:
        return False


def main() -> int:
    rec = json.loads(REC.read_text(encoding="utf-8"))
    started = datetime.datetime.fromisoformat(rec["started"])
    target = datetime.datetime.fromisoformat(rec["verify_after"])
    pid0 = rec["frontend_pids"][0] if rec.get("frontend_pids") else None

    lines = [f"one-hour lifetime: started {started}, verify at {target}, pid {pid0}"]

    # Watch for an early death instead of only checking the end state.
    while datetime.datetime.now() < target:
        alive = [p.pid for p in psutil.process_iter(["pid", "name"])
                 if (p.info["name"] or "").lower() == "genie.desktop.exe"]
        if not alive:
            el = (datetime.datetime.now() - started).total_seconds() / 60
            lines.append(f"DIED EARLY at {el:.1f} min - no frontend process")
            OUT.write_text("\n".join(lines), encoding="utf-8")
            VERDICT.write_text("FAIL", encoding="utf-8")
            print("\n".join(lines))
            return 1
        time.sleep(30)

    alive = [p.pid for p in psutil.process_iter(["pid", "name"])
             if (p.info["name"] or "").lower() == "genie.desktop.exe"]
    h = health()
    el = (datetime.datetime.now() - started).total_seconds() / 60

    lines.append(f"elapsed_min={el:.1f}")
    lines.append(f"frontend_pids={alive}")
    lines.append(f"original_pid_still_alive={pid0 in alive if pid0 else False}")
    lines.append(f"health={h}")
    verdict = "PASS" if (alive and h) else "FAIL"
    lines.append(f"RESULT: {verdict} - one-hour owner-like lifetime")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    VERDICT.write_text(verdict, encoding="utf-8")
    print("\n".join(lines))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
