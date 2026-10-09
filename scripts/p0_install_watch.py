"""P0 release-blocker: observe install directories for file disappearance.

Non-destructive. It only reads. For each watched directory it records, on every
tick: existence, recursive file count, total bytes, and the presence of specific
canary paths. When a count DROPS it emits a `disappearance` event with the exact
wall-clock second and the list of paths that vanished, so it can be correlated
with the process capture (scripts/p0_process_capture.py) to answer WHO DELETED.

Usage:
    python scripts/p0_install_watch.py --seconds 1800 --interval 2 \
        --watch A=<dir> --watch B=<dir> --out artifacts/p0_install_watch.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def scan(root: Path) -> Dict[str, object]:
    """Cheap recursive census. Returns counts and canary presence."""
    files = 0
    dirs = 0
    total = 0
    seen: List[str] = []
    if not root.exists():
        return {"exists": False, "files": 0, "dirs": 0, "bytes": 0, "sample": []}
    for dirpath, dirnames, filenames in os.walk(root):
        dirs += len(dirnames)
        files += len(filenames)
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
            if len(seen) < 400:
                seen.append(os.path.relpath(os.path.join(dirpath, name), root))
    return {"exists": True, "files": files, "dirs": dirs, "bytes": total, "sample": seen}


def canaries(root: Path, names: List[str]) -> Dict[str, bool]:
    out = {}
    for n in names:
        p = root / n
        out[n] = p.exists()
    return out


CANARY_NAMES = [
    "GENIE.exe",
    "Uninstall GENIE.exe",
    "resources",
    "resources/backend-runtime",
    "resources/app.asar",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1800.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--watch", action="append", default=[],
                    help="LABEL=PATH (repeatable)")
    ap.add_argument("--out", default="artifacts/p0_install_watch.jsonl")
    args = ap.parse_args()

    targets: Dict[str, Path] = {}
    for spec in args.watch:
        label, _, path = spec.partition("=")
        targets[label.strip()] = Path(path.strip())

    if not targets:
        print("nothing to watch")
        return 2

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out = open(args.out, "a", encoding="utf-8")
    prev: Dict[str, Dict[str, object]] = {}

    print(f"[watch] watching {list(targets)} for {args.seconds}s "
          f"@ {args.interval}s -> {args.out}", flush=True)

    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            now = datetime.now().isoformat(timespec="seconds")
            for label, path in targets.items():
                cur = scan(path)
                can = canaries(path, CANARY_NAMES)
                old = prev.get(label)
                event = {"wall": now, "case": label, "path": str(path),
                         "files": cur["files"], "dirs": cur["dirs"],
                         "bytes": cur["bytes"], "canaries": can}
                if old is not None:
                    of = int(old.get("files") or 0)
                    nf = int(cur["files"])
                    if nf < of:
                        old_set = set(old.get("sample") or [])
                        new_set = set(cur.get("sample") or [])
                        vanished = sorted(old_set - new_set)[:25]
                        event["disappearance"] = {
                            "lost_files": of - nf,
                            "lost_bytes": int(old.get("bytes") or 0) - int(cur["bytes"]),
                            "vanished_sample": vanished,
                        }
                        print(f"[watch] !! {label}: {of} -> {nf} files at {now}", flush=True)
                        print(f"[watch]    vanished: {vanished[:6]}", flush=True)
                    if old.get("exists") and not cur.get("exists"):
                        event["disappearance"] = {"root_gone": True}
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
                out.flush()
                prev[label] = cur
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
    print("[watch] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
