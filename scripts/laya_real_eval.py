"""GENIE held-out routing evaluation of the REAL Laya checkpoint.

Runs the same 45-case held-out set used by scripts/laya_routing_eval.py (parsed
from source via AST so this script needs only the Laya venv, not GENIE's deps)
through the locally provisioned Laya Agent, and measures per-class accuracy,
a confusion matrix, catastrophic misroutes, latency and memory.

Laya is a typed-question decision model: it scores the options it is given, it
does not generate text. The 6 GENIE ROUTABLE classes are offered as a `choice`
question. The 4 non-routing properties are reported separately, because the
routing layer cannot express them as a route.
"""
import ast
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODEL_ROOT = REPO / "data" / "models" / "laya"
WEIGHTS = MODEL_ROOT / "weights"
OUT = REPO / "artifacts" / f"laya-routing-eval-{time.strftime('%Y%m%d')}-real.json"

ROUTABLE = ["conversation", "simple_action", "browser_action", "research",
            "multi_step", "durable_mission"]
NON_ROUTING = ["coding", "clarification_required", "unavailable_capability",
               "permission_required"]

CRITERIA = {
    "conversation": "greetings, small talk, factual questions, explanations, "
                    "reasoning, opinions; no device or web action is requested",
    "simple_action": "exactly one direct Windows/system action: open or close an "
                     "app, change or mute volume, list audio devices, network status",
    "browser_action": "open a website or search the web, click, type or fill on a "
                      "web page, upload a file to a page",
    "research": "find current information on the web and summarise it with sources",
    "multi_step": "one request containing several actions to perform once, in order",
    "durable_mission": "a recurring or scheduled task that must persist and run "
                       "again later, e.g. every morning at 9",
}


def load_cases():
    src = (REPO / "scripts" / "laya_routing_eval.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "CASES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("CASES not found in scripts/laya_routing_eval.py")


def main():
    cases = load_cases()
    print(f"held-out cases: {len(cases)}", flush=True)

    t0 = time.perf_counter()
    from laya import Agent
    print("import: %.2f s" % (time.perf_counter() - t0), flush=True)

    t0 = time.perf_counter()
    agent = Agent(model_id_or_path=WEIGHTS)
    cold = time.perf_counter() - t0
    print("cold load: %.2f s" % cold, flush=True)

    t0 = time.perf_counter()
    agent.warmup()
    print("warmup: %.2f s" % (time.perf_counter() - t0), flush=True)

    try:
        import psutil
        rss = psutil.Process().memory_info().rss / 1e6
    except Exception:
        rss = None
    print("RSS after load: %s MB" % (round(rss) if rss else "n/a"), flush=True)

    question = {
        "route": {
            "type": "choice",
            "instructions": "Classify the user's request into the single most "
                            "appropriate GENIE route.",
            "criteria": CRITERIA,
        }
    }

    rows = []
    for text, language, expected in cases:
        start = time.perf_counter()
        try:
            result = agent.predict(text, question)
            ms = (time.perf_counter() - start) * 1000
            answers = (result or {}).get("answers") or {}
            route = (answers.get("route") or {})
            predicted = route.get("choice")
            conf = route.get("probability") or route.get("confidence") or route.get("p")
            rows.append({"text": text, "language": language, "expected": expected,
                         "predicted": predicted, "confidence": conf,
                         "latency_ms": round(ms, 1),
                         "model": ((result or {}).get("routing") or {}).get("model")})
        except Exception as exc:
            rows.append({"text": text, "language": language, "expected": expected,
                         "predicted": None, "error": f"{type(exc).__name__}: {exc}",
                         "latency_ms": round((time.perf_counter() - start) * 1000, 1)})

    per_class = {c: {"total": 0, "correct": 0} for c in ROUTABLE + NON_ROUTING}
    confusion = {}
    catastrophic = []
    for r in rows:
        exp = r["expected"]
        pred = r["predicted"]
        if exp in per_class:
            per_class[exp]["total"] += 1
            if pred == exp:
                per_class[exp]["correct"] += 1
        confusion.setdefault(exp, {})
        confusion[exp][str(pred)] = confusion[exp].get(str(pred), 0) + 1
        # catastrophic: a real-world action request predicted as pure conversation,
        # or a conversation predicted as a durable/scheduled mission
        if exp in ("simple_action", "browser_action", "multi_step", "durable_mission") \
                and pred == "conversation":
            catastrophic.append(r)
        if exp == "conversation" and pred in ("durable_mission", "multi_step"):
            catastrophic.append(r)

    routable_total = sum(per_class[c]["total"] for c in ROUTABLE)
    routable_correct = sum(per_class[c]["correct"] for c in ROUTABLE)
    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "engine": "laya",
        "laya_version": getattr(__import__("laya"), "__version__", "?"),
        "checkpoint": "convaiinnovations/laya",
        "revision": "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851",
        "license": "apache-2.0",
        "weights_dir": str(WEIGHTS),
        "cold_load_s": round(cold, 2),
        "rss_mb": round(rss) if rss else None,
        "latency_ms": {"min": lat[0] if lat else None,
                       "median": lat[len(lat) // 2] if lat else None,
                       "max": lat[-1] if lat else None,
                       "n": len(lat)},
        "routable_classes": ROUTABLE,
        "non_routing_properties": NON_ROUTING,
        "routable_accuracy": round(routable_correct / routable_total, 4) if routable_total else 0.0,
        "routable_totals": {"cases": routable_total, "correct": routable_correct},
        "per_class": {c: {**v, "recall": round(v["correct"] / v["total"], 4) if v["total"] else None}
                      for c, v in per_class.items()},
        "confusion": confusion,
        "catastrophic_misroutes": len(catastrophic),
        "catastrophic_examples": [{"text": r["text"], "expected": r["expected"],
                                   "predicted": r["predicted"]} for r in catastrophic[:10]],
        "rows": rows,
        "note": ("Laya is a typed-question decision model; the 4 non-routing "
                 "properties are reported separately because the routing layer "
                 "cannot express them as a route. The checkpoint also emits its own "
                 "warning that shipped temperatures are out of range, so its "
                 "confidence values are uncalibrated."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== Laya GENIE routing evaluation ===", flush=True)
    for c in ROUTABLE + NON_ROUTING:
        v = report["per_class"][c]
        print(f"  {c:24s} {v['correct']}/{v['total']}  recall={v['recall']}", flush=True)
    print(f"routable-only accuracy: {report['routable_accuracy']} "
          f"({routable_correct}/{routable_total})", flush=True)
    print(f"catastrophic misroutes: {report['catastrophic_misroutes']}", flush=True)
    print(f"latency median: {report['latency_ms']['median']} ms "
          f"(min {report['latency_ms']['min']}, max {report['latency_ms']['max']})", flush=True)
    print(f"artifact: {OUT}", flush=True)


if __name__ == "__main__":
    main()
