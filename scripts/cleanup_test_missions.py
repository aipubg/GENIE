"""Remove ONLY known test artifacts from a mission store (Point 6.7).

The acceptance harnesses no longer write into the owner's database (they now
run against an isolated GENIE_DATA_DIR), but earlier runs may have left test
rows behind. This helper removes those — and nothing else.

Safety:
  * DRY-RUN by default. Nothing is deleted unless --apply is passed.
  * Only an explicit ALLOW-LIST is matched:
        - goal == "upgrade acceptance"        (installer/upgrade harness)
        - goal starts with "answer: "          (old conversation-as-mission bug)
  * Legitimate owner missions are NEVER matched and never deleted.
  * --db is explicit; there is no "guess and delete" path.

Usage:
    python scripts/cleanup_test_missions.py                 # list only
    python scripts/cleanup_test_missions.py --apply         # delete matches
    python scripts/cleanup_test_missions.py --db PATH --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Exact / prefix matches that are unambiguously harness output.
EXACT_GOALS = {"upgrade acceptance"}
PREFIX_GOALS = ("answer: ",)


def is_test_artifact(goal: str) -> bool:
    g = (goal or "").strip()
    if g in EXACT_GOALS:
        return True
    return any(g.startswith(p) for p in PREFIX_GOALS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="path to genie.db (default: owner data dir)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete the matched rows (default is dry-run)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else None
    if db_path is None:
        from core.paths import data_dir
        db_path = data_dir() / "genie.db"

    if not db_path.exists():
        print(f"no database at {db_path}")
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT mission_id, goal, state FROM missions").fetchall()
        matches = [r for r in rows if is_test_artifact(r["goal"])]

        print(f"database: {db_path}")
        print(f"missions: {len(rows)} total, {len(matches)} matched test artifacts")
        for r in matches:
            print(f"  [{r['state']}] {r['mission_id']}  {r['goal'][:70]}")

        if not matches:
            print("nothing to do.")
            return 0
        if not args.apply:
            print("\nDRY-RUN — nothing deleted. Re-run with --apply to remove the "
                  f"{len(matches)} row(s) above.")
            return 0

        ids = [r["mission_id"] for r in matches]
        conn.executemany("DELETE FROM mission_steps WHERE mission_id=?", [(i,) for i in ids])
        conn.executemany("DELETE FROM missions WHERE mission_id=?", [(i,) for i in ids])
        conn.commit()
        print(f"\nDELETED {len(ids)} test-artifact mission(s). Owner missions untouched.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
