"""Real-machine rollback verification for the Phase-14 exit gate.

Drives the ACTUAL `genie.py` CLI (not the library in isolation) against the real
GENIE data directory on this machine: stage two releases, activate one, then
roll back and confirm the previous release is restored.

Safety: `Updater.activate()` only flips a state pointer in
`<data_dir>/releases/state.json`; it never overwrites GENIE source. Staged
releases are copies under `<data_dir>/releases/<version>`. The two versions
used here are throwaway fixtures and are removed at the end.

    python scripts/verify_rollback_real.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENIE = ROOT / "genie.py"
PY = sys.executable

V1 = "0.0.0-rollbackcheck-a"
V2 = "0.0.0-rollbackcheck-b"


def cli(*args) -> dict:
    proc = subprocess.run([PY, str(GENIE), *args], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return {"_raw": out, "_rc": proc.returncode, "_err": proc.stderr[-300:]}


def main() -> int:
    print("Real-machine rollback verification")
    print("=" * 62)
    print(f"repo : {ROOT}")
    print(f"py   : {PY}")

    before = cli("update-status", "--json")
    print(f"active before: {before.get('active_version') or '-'}")

    tmp = Path(tempfile.mkdtemp(prefix="genie-rollback-"))
    ok = True
    try:
        for version, content in ((V1, "content-A"), (V2, "content-B")):
            src = tmp / version
            src.mkdir(parents=True)
            (src / "marker.txt").write_text(content, encoding="utf-8")

        for version in (V1, V2):
            staged = cli("update-stage", version, str(tmp / version))
            print(f"  staged {version}: ok={staged.get('ok')}")
            ok = ok and bool(staged.get("ok"))

        act1 = cli("update-activate", V1)
        print(f"  activate {V1}: ok={act1.get('ok')}")
        ok = ok and bool(act1.get("ok"))

        act2 = cli("update-activate", V2)
        print(f"  activate {V2}: ok={act2.get('ok')} "
              f"previous={act2.get('previous_version')}")
        ok = ok and bool(act2.get("ok")) and act2.get("previous_version") == V1

        status = cli("update-status", "--json")
        print(f"  active now: {status.get('active_version')}")
        ok = ok and status.get("active_version") == V2

        rb = cli("update-rollback")
        print(f"  rollback: ok={rb.get('ok')} active={rb.get('active_version')}")
        ok = ok and bool(rb.get("ok")) and rb.get("active_version") == V1

        final = cli("update-status", "--json")
        print(f"  active after rollback: {final.get('active_version')}")
        ok = ok and final.get("active_version") == V1

        # the rolled-back release's content is intact
        marker = ROOT / "data" / "releases" / V1 / "marker.txt"
        intact = marker.is_file() and marker.read_text(encoding="utf-8") == "content-A"
        print(f"  rolled-back release content intact: {intact}")
        ok = ok and intact
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # remove the throwaway staged releases AND the state that pointed at
        # them, so the updater is not left referencing a deleted fixture.
        for version in (V1, V2):
            shutil.rmtree(ROOT / "data" / "releases" / version, ignore_errors=True)
        state_file = ROOT / "data" / "releases" / "active.json"
        try:
            if state_file.is_file():
                cur = json.loads(state_file.read_text(encoding="utf-8"))
                if cur.get("active_version") in (V1, V2):
                    cur["active_version"] = None
                if cur.get("previous_version") in (V1, V2):
                    cur["previous_version"] = None
                state_file.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    print("=" * 62)
    print("ROLLBACK VERIFICATION:", "PASS" if ok else "FAIL")
    print()
    print("Scope note: this exercises the real CLI and the real data directory on")
    print("this machine. It is NOT a packaged-install rollback (that needs the")
    print("installer, which requires Inno Setup 6 on a Windows build host).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
