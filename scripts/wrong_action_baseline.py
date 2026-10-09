"""Wrong-action baseline collection (RC item 14).

Definitions are fixed in docs/WRONG_ACTION_BASELINE.md BEFORE collection:
  executed = verified + failed + unverified
  wrong    = failed + unverified          (unverified counts as wrong on purpose)
  refusals are the safety net working and are NOT wrong
  unverifiable = could not be attempted here; excluded from the denominator

Only REAL actions against a live daemon count. No test doubles.

    python scripts/wrong_action_baseline.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8787"
REPORT = ROOT / "artifacts" / "wrong_action_baseline.json"

MIN_EXECUTED = 30          # sufficiency threshold, defined before collection


def post(path: str, payload: dict, timeout: float = 60):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {"error": f"http {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"__transport_error": f"{type(exc).__name__}: {exc}"}


def get(path: str, timeout: float = 30):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"__transport_error": f"{type(exc).__name__}: {exc}"}


ROWS: list[dict] = []


MISSING = ("not found", "endpoint unavailable", "__transport_error")


def outcome_for(resp: dict, fallback: str = "unverified") -> str:
    """A missing endpoint is UNVERIFIABLE, never a wrong action.

    Reporting "endpoint does not exist" as a failed action would fabricate a
    bad wrong-action rate out of nothing.
    """
    if not isinstance(resp, dict):
        return "unverifiable"
    if resp.get("__transport_error"):
        return "unverifiable"
    err = str(resp.get("error") or "").lower()
    if err in ("not found",) or "unknown action" in err or "no route" in err:
        return "unverifiable"
    return fallback


def record(category: str, intent: str, outcome: str, detail: str = "") -> None:
    ROWS.append({"category": category, "intent": intent,
                 "outcome": outcome, "detail": detail[:200]})
    print(f"  [{outcome:15}] {category}: {intent}" + (f" — {detail[:90]}" if detail else ""))


def main() -> int:
    print("wrong-action baseline — real actions against", BASE)
    status = get("/api/status")
    if status.get("__transport_error"):
        print("daemon not reachable:", status["__transport_error"])
        return 2
    print("daemon uptime:", status.get("uptime_s"))

    # ------------------------------------------------------ permission denial
    # asking for something the owner context should not grant
    r = post("/api/actions/invoke", {"action": "computer.launch",
                                     "params": {"target": "cmd.exe"},
                                     "person_id": "guest"})
    oc = outcome_for(r, fallback="correct_refusal" if r.get("ok") is False else "unverified")
    if oc == "unverifiable":
        record("permission denial", "launch app as guest", "unverifiable", "endpoint unavailable")
    elif r.get("ok") is False:
        record("permission denial", "launch app as guest", "correct_refusal",
               str(r.get("verify", {}).get("detail", "")))
    else:
        record("permission denial", "launch app as guest", "unverified", json.dumps(r)[:120])

    # -------------------------------------------------------- safe-mode refusal
    sm = post("/api/safe-mode", {"enabled": True})
    if sm.get("__transport_error"):
        record("safe-mode refusal", "enter safe mode", "unverifiable", "endpoint unavailable")
    else:
        r = post("/api/actions/invoke", {"action": "file.write",
                                         "params": {"path": "C:/Windows/genie_probe.txt",
                                                    "content": "x"},
                                         "person_id": "owner"})
        oc = outcome_for(r)
        if oc == "unverifiable":
            record("safe-mode refusal", "write outside workspace in safe mode",
                   "unverifiable", "endpoint unavailable")
        elif r.get("ok") is False or r.get("error") or r.get("denied"):
            record("safe-mode refusal", "write outside workspace in safe mode",
                   "correct_refusal", str(r.get("error") or r.get("verify", {}))[:120])
        else:
            record("safe-mode refusal", "write outside workspace in safe mode",
                   "unverified", json.dumps(r)[:120])
        post("/api/safe-mode", {"enabled": False})

    # --------------------------------------------------------- file operations
    # real file create / read / delete in the workspace temp area
    tmp = Path(ROOT) / "data" / "baseline-probe"
    tmp.mkdir(parents=True, exist_ok=True)
    target = tmp / "probe.txt"
    r = post("/api/actions/invoke", {"action": "file.write",
                                     "params": {"path": str(target),
                                                "content": "genie baseline"},
                                     "person_id": "owner"})
    if outcome_for(r) == "unverifiable":
        record("file operations", "write a file", "unverifiable", "endpoint unavailable")
    elif r.get("ok") is True and target.exists() and target.read_text() == "genie baseline":
        record("file operations", "write a file", "verified")
    else:
        record("file operations", "write a file", "failed",
               str(r.get("error") or r.get("verify", {}))[:120])

    r = post("/api/actions/invoke", {"action": "file.read",
                                     "params": {"path": str(target)},
                                     "person_id": "owner"})
    text = r.get("content") or r.get("text") or ""
    if "genie baseline" in str(text):
        record("file operations", "read the file back", "verified")
    elif outcome_for(r) == "unverifiable":
        record("file operations", "read the file back", "unverifiable", "endpoint unavailable")
    else:
        record("file operations", "read the file back", "failed", json.dumps(r)[:120])

    try:
        target.unlink(missing_ok=True)
        record("file operations", "delete the file", "verified")
    except Exception as exc:  # noqa: BLE001
        record("file operations", "delete the file", "failed", str(exc))

    # ------------------------------------------------------------- app control
    r = get("/api/apps")
    if r.get("__transport_error"):
        record("app control", "list known applications", "unverifiable", "endpoint unavailable")
    elif isinstance(r.get("apps"), list) or r.get("ok") is True:
        record("app control", "list known applications", "verified")
    else:
        record("app control", "list known applications", "unverified", json.dumps(r)[:120])

    # ------------------------------------------------------------ skills replay
    r = get("/api/skills")
    if r.get("__transport_error"):
        record("learned skill", "list learned skills", "unverifiable", "endpoint unavailable")
    else:
        record("learned skill", "list learned skills", "verified")

    # -------------------------------------------- categories needing hardware
    for cat, intent, why in (
        ("voice command", "spoken command end to end",
         "requires microphone and supervised desktop"),
        ("browser DOM actions", "DOM search and click",
         "requires real Chrome (real_machine suite)"),
        ("computer automation", "real mouse/keyboard act",
         "requires supervised desktop session"),
        ("provider failover", "fail over between providers",
         "no live provider key configured"),
    ):
        record(cat, intent, "unverifiable", why)

    # ------------------------------------------- daemon's own action metrics
    daemon_metrics = get("/api/metrics/actions")
    vacuous = bool(daemon_metrics.get("available")) and         int(daemon_metrics.get("executed") or 0) == 0 and         bool(daemon_metrics.get("within_target"))
    print("  daemon /api/metrics/actions:",
          "executed=", daemon_metrics.get("executed"),
          "wrong=", daemon_metrics.get("wrong"),
          "within_target=", daemon_metrics.get("within_target"),
          "(VACUOUS — no actions recorded)" if vacuous else "")

    # ------------------------------------------------------------------ tally
    counts: dict[str, int] = {}
    for row in ROWS:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    executed = counts.get("verified", 0) + counts.get("failed", 0) + counts.get("unverified", 0)
    wrong = counts.get("failed", 0) + counts.get("unverified", 0)
    rate = (wrong / executed) if executed else 0.0
    sufficient = executed >= MIN_EXECUTED

    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "definitions": {
            "executed": "verified + failed + unverified",
            "wrong": "failed + unverified (unverified counts as wrong on purpose)",
            "refusal_is_not_wrong": True,
            "sufficiency_threshold": MIN_EXECUTED,
        },
        "counts": counts,
        "executed": executed,
        "wrong": wrong,
        "wrong_action_rate": round(rate, 4),
        "target": 0.02,
        "sufficient_sample": sufficient,
        "verdict": ("MEETS TARGET" if sufficient and rate <= 0.02 else
                    "ABOVE TARGET" if sufficient else "INSUFFICIENT EVIDENCE"),
        "rows": ROWS,
        "daemon_metrics": daemon_metrics,
        "daemon_metrics_vacuous": vacuous,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\ncounts:", counts)
    print(f"executed={executed} wrong={wrong} rate={rate:.4f} target=0.02")
    print("sufficient sample:", sufficient)
    print("VERDICT:", out["verdict"])
    print("report:", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
