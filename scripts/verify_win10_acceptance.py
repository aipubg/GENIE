"""Windows 10 x64 acceptance pre-flight.

STATUS: Windows 10 support is NOT claimed. It is an open release gate. This
script exists so that whoever has a Windows 10 machine can produce evidence
instead of an opinion, and so the answer is recorded rather than assumed.

It runs the same checks the Windows 11 acceptance uses, reports the host it ran
on, and writes a result file. It never claims success on an OS it did not run
on: the result file records the actual OS version alongside every check.

    python scripts/verify_win10_acceptance.py --exe <path-to-Genie.Desktop.exe>
                                              [--out artifacts/win10_acceptance.json]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def host_report() -> dict:
    """What this machine actually is. Reported, never assumed."""
    rel = platform.release()
    ver = platform.version()
    build = ver.split(".")[-1] if ver.count(".") >= 2 else ""
    info = {
        "os": platform.system(),
        "release": rel,
        "version": ver,
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    try:
        build_int = int(build)
        # Windows 11 is build 22000+; Windows 10 is 10240-19045.
        info["is_windows_10"] = platform.system() == "Windows" and 10240 <= build_int < 22000
        info["is_windows_11"] = platform.system() == "Windows" and build_int >= 22000
    except (ValueError, IndexError):
        info["is_windows_10"] = None
        info["is_windows_11"] = None
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", default="artifacts/win10_acceptance.json")
    args = ap.parse_args()

    exe = Path(args.exe)
    host = host_report()

    print("host:")
    for k, v in host.items():
        print(f"  {k}: {v}")

    if host["os"] != "Windows":
        print("\nnot a Windows host - nothing to accept")
        return 2
    if host["is_windows_10"] is False:
        print("\nNOTE: this host is not Windows 10. Results below do NOT "
              "constitute Windows 10 acceptance.")

    if not exe.exists():
        print(f"\nexe not found: {exe}")
        return 2

    print("\nchecks:")

    # 1. launches and the backend answers
    # Point 6.7 — isolate the data directory so this acceptance run never writes
    # test state into the owner's production GENIE data.
    env = os.environ.copy()
    env["GENIE_DATA_DIR"] = str(
        Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        / "genie-acceptance-data")
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    healthy = False
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        end = time.time() + 60
        while time.time() < end:
            try:
                with opener.open("http://127.0.0.1:8787/health", timeout=2):
                    healthy = True
                    break
            except Exception:
                time.sleep(1)
        check("launches and backend answers /health", healthy)

        if healthy:
            # 2. the new W6 surfaces respond
            for path, must in (("/api/competition", "commit_policy"),
                               ("/api/experience", "available"),
                               ("/api/knowledge", "available"),
                               ("/api/media", "available")):
                try:
                    with opener.open(f"http://127.0.0.1:8787{path}", timeout=5) as r:
                        body = json.loads(r.read().decode("utf-8"))
                    check(f"{path} responds with '{must}'",
                          isinstance(body, dict) and must in body)
                except Exception as exc:
                    check(f"{path} responds with '{must}'", False, str(exc))
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

    time.sleep(3)
    try:
        import psutil
        left = [p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower() in ("genie.desktop.exe", "pythonw.exe")]
        check("no GENIE processes left after terminate", not left, f"pids={left}")
    except ImportError:
        check("no GENIE processes left after terminate", False, "psutil not installed")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nRESULT {passed}/{len(results)} passed on {host['release']} "
          f"build {host['version']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"host": host, "results": results,
                               "passed": passed, "total": len(results),
                               "claim": "NONE - this is evidence, not a support claim"},
                              indent=2), encoding="utf-8")
    print(f"written to {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
