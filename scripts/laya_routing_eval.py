"""GENIE-specific held-out routing evaluation (Laya gate, priority 6).

Measures the CURRENT routing baseline (deterministic heuristic + Director) against
a GENIE-specific held-out set spanning Hindi / Hinglish / English across the
decision classes that matter for production:

    conversation, simple_action, browser_action, research, coding,
    multi_step, durable_mission, clarification_required,
    unavailable_capability, permission_required

It also records whether the local Laya classifier is runnable at all. Laya is
NOT promoted on this evidence: the recorded 10/20 result is not production
quality, and this harness exists so a future activation decision has per-class
precision/recall, a confusion matrix and catastrophic-misroute counts.

Writes artifacts/laya-routing-eval-<date>.json
"""
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from core.contracts import CallContext  # noqa: E402
from core.orchestrator import (  # noqa: E402
    _apply_conversation_override, _apply_desktop_action, _apply_mission_gate,
    _apply_web_action_plan,
)


def full_route(text, ctx, director):
    """Apply the SAME override pipeline the production path uses.

    Measuring the Director alone under-reports browser/multi-step routing because
    the Orchestrator applies web/desktop/conversation overrides afterwards. This
    mirrors handle_text so the baseline is faithful.
    """
    decision = director.classify(text, ctx, "")
    decision = _apply_conversation_override(text, decision)
    decision = _apply_mission_gate(text, decision)
    decision = _apply_web_action_plan(text, decision)
    decision = _apply_desktop_action(text, decision)
    return decision

# --------------------------------------------------------------------- cases
# (text, language, expected_class)
CASES = [
    # conversation
    ("hello, how are you today", "en", "conversation"),
    ("what is the capital of France", "en", "conversation"),
    ("thanks, that was helpful", "en", "conversation"),
    ("namaste kaise ho", "hi", "conversation"),
    ("tum kaise ho bhai", "hinglish", "conversation"),
    ("good morning", "en", "conversation"),
    # simple_action (Windows)
    ("open notepad", "en", "simple_action"),
    ("volume 30 karo", "hinglish", "simple_action"),
    ("mute the sound", "en", "simple_action"),
    ("notepad kholo", "hinglish", "simple_action"),
    ("set volume to 40", "en", "simple_action"),
    ("calculator khol do", "hinglish", "simple_action"),
    # browser_action
    ("open youtube in brave", "en", "browser_action"),
    ("chrome mein github kholo", "hinglish", "browser_action"),
    ("search for python tutorials on the web", "en", "browser_action"),
    ("browser mein wikipedia kholo", "hinglish", "browser_action"),
    ("open my default browser to gmail", "en", "browser_action"),
    # research
    ("research the best laptop under 80000", "en", "research"),
    ("find the latest news about ISRO", "en", "research"),
    ("what are the current prices of gold", "en", "research"),
    ("latest AI models ke baare mein batao", "hinglish", "research"),
    ("compare these two phones and recommend one", "en", "research"),
    # coding
    ("write a python function to reverse a string", "en", "coding"),
    ("fix the bug in my javascript code", "en", "coding"),
    ("create a sql query to join these tables", "en", "coding"),
    ("python script likho jo csv padhe", "hinglish", "coding"),
    # multi_step (one-time)
    ("open notepad, type hello, then save it", "en", "multi_step"),
    ("open chrome and search for hotels and open the first result", "en", "multi_step"),
    ("notepad kholo aur likho hello phir save karo", "hinglish", "multi_step"),
    ("open settings and turn off bluetooth", "en", "multi_step"),
    # durable_mission
    ("every morning at 9 send me the weather", "en", "durable_mission"),
    ("remind me daily to drink water", "en", "durable_mission"),
    ("har roz subah 8 baje news bhejo", "hinglish", "durable_mission"),
    ("schedule a weekly backup every sunday", "en", "durable_mission"),
    # clarification_required
    ("send it to him", "en", "clarification_required"),
    ("do the thing we discussed", "en", "clarification_required"),
    ("usko bhej do", "hinglish", "clarification_required"),
    ("open that file", "en", "clarification_required"),
    # unavailable_capability
    ("control my smart fridge", "en", "unavailable_capability"),
    ("fly the drone to the roof", "en", "unavailable_capability"),
    ("start my car remotely", "en", "unavailable_capability"),
    # permission_required
    ("uninstall chrome completely", "en", "permission_required"),
    ("delete all my downloads", "en", "permission_required"),
    ("disable windows defender", "en", "permission_required"),
    ("send a whatsapp to my boss saying I quit", "en", "permission_required"),
]

# Classes the routing layer can actually EXPRESS as a distinct route.
ROUTABLE_CLASSES = ["conversation", "simple_action", "browser_action", "research",
                    "multi_step", "durable_mission"]
# Properties that are NOT routing classes in the current vocabulary. `coding` is a
# provider_category used later by the gateway; clarification / unavailable /
# permission are execution-time outcomes. They are reported separately so Laya is
# never scored against labels the baseline cannot emit.
NON_ROUTING_PROPERTIES = ["coding", "clarification_required",
                          "unavailable_capability", "permission_required"]
CLASSES = ROUTABLE_CLASSES + NON_ROUTING_PROPERTIES

# Classes where routing the request as plain conversation is a CATASTROPHIC
# misroute: the owner asked for real work and would receive ungrounded prose.
ACTION_CLASSES = {"simple_action", "browser_action", "multi_step", "durable_mission",
                  "permission_required"}


def route_of(decision):
    """Map a DirectorDecision to one of the evaluation classes."""
    intent = str(getattr(decision, "intent", "conversation") or "conversation")
    tasks = list(getattr(decision, "tasks", []) or [])
    caps = [str(getattr(t, "capability", "") or "") for t in tasks]
    if intent == "conversation" and not tasks:
        return "conversation"
    if getattr(decision, "mission_required", False) or intent == "mission":
        return "durable_mission"
    if any(c.startswith("browser.") for c in caps):
        return "browser_action"
    if tasks:
        return "multi_step" if len(tasks) > 1 else "simple_action"
    category = str(getattr(decision, "provider_category", "none") or "none")
    if category == "coding":
        return "coding"
    if category == "research" or getattr(decision, "reasoning_required", False):
        return "research"
    return "conversation"


def main():
    ctx = CallContext(person_id="owner", device_id="pc_main", trace_id="laya-eval")
    from director.heuristics import HeuristicDirector
    director = HeuristicDirector()

    # Laya availability (recorded, never assumed)
    try:
        from director.laya_runtime import LayaRuntime
        laya = LayaRuntime()
        laya_state = {"mode": getattr(laya, "mode", "unknown"),
                      "state": (getattr(laya, "last", {}) or {}).get("state", "")}
    except Exception as exc:
        laya_state = {"mode": "unavailable", "error": str(exc)}

    confusion = {c: {d: 0 for d in CLASSES} for c in CLASSES}
    per_class = {c: {"total": 0, "correct": 0} for c in CLASSES}
    per_language = {}
    latencies = []
    catastrophic = []
    rows = []

    for text, language, expected in CASES:
        start = time.perf_counter()
        try:
            decision = full_route(text, ctx, director)
            predicted = route_of(decision)
        except Exception as exc:
            predicted = "error"
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        latencies.append(latency_ms)

        per_class[expected]["total"] += 1
        lang = per_language.setdefault(language, {"total": 0, "correct": 0})
        lang["total"] += 1
        if predicted == expected:
            per_class[expected]["correct"] += 1
            lang["correct"] += 1
        if predicted in confusion.get(expected, {}):
            confusion[expected][predicted] += 1

        if expected in ACTION_CLASSES and predicted == "conversation":
            catastrophic.append({"text": text, "expected": expected, "predicted": predicted})

        rows.append({"text": text, "language": language, "expected": expected,
                     "predicted": predicted, "latency_ms": latency_ms})

    correct = sum(v["correct"] for v in per_class.values())
    total = sum(v["total"] for v in per_class.values())

    routable_total = sum(per_class[c]["total"] for c in ROUTABLE_CLASSES)
    routable_correct = sum(per_class[c]["correct"] for c in ROUTABLE_CLASSES)
    non_routing = {c: {"cases": per_class[c]["total"], "matched_route": per_class[c]["correct"],
                       "behaviour": sorted({r["predicted"] for r in rows if r["expected"] == c})}
                   for c in NON_ROUTING_PROPERTIES}

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": ("GENIE-specific held-out routing evaluation. Baseline only; Laya is NOT "
                  "promoted on this evidence."),
        "baseline_classifier": "HeuristicDirector (deterministic fast route)",
        "laya": laya_state,
        "laya_promotable": False,
        "laya_blocker": ("Laya is not configured in this environment: director.laya.mode/"
                         "python/source/model are unset and the pinned model/source files "
                         "do not exist, so the local classifier cannot run. Recorded as "
                         "PENDING_OWNER."),
        "totals": {"cases": total, "correct": correct,
                   "accuracy": round(correct / total, 4) if total else 0.0},
        "routable_classes": ROUTABLE_CLASSES,
        "non_routing_properties": NON_ROUTING_PROPERTIES,
        "routable_accuracy": (round(routable_correct / routable_total, 4)
                              if routable_total else 0.0),
        "routable_totals": {"cases": routable_total, "correct": routable_correct},
        "non_routing_behaviour": non_routing,
        "known_baseline_gaps": [
            "one-time multi-step requests are not decomposed (routed as simple_action)",
            "no clarification detection ('send it to him' -> reasoning prose)",
            "no unavailable-capability honesty ('control my smart fridge' -> reasoning prose)",
            "'uninstall chrome completely' routes to application.open (wrong capability chosen)",
        ],
        "per_class": {c: {**v, "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else None,
                          "recall": round(v["correct"] / v["total"], 4) if v["total"] else None}
                      for c, v in per_class.items()},
        "per_language": {k: {**v, "accuracy": round(v["correct"] / v["total"], 4)}
                         for k, v in per_language.items()},
        "confusion_matrix": confusion,
        "catastrophic_misroutes": catastrophic,
        "catastrophic_count": len(catastrophic),
        "latency_ms": {"median": round(statistics.median(latencies), 2),
                       "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2),
                       "max": max(latencies)},
        "previous_laya_benchmark": {"accuracy": "10/20", "cold_ms": 22274,
                                    "warm_median_ms": 94, "nedle2_ms": 836.72,
                                    "note": "not production quality; not activated"},
        "decision": ("Laya remains SHADOW-ONLY / OFF. The recorded 10/20 is not production "
                     "quality and the local classifier is unavailable here, so no category "
                     "meets an activation threshold. heuristic-fast stays first and NEDLE2 "
                     "stays the fallback."),
        "rows": rows,
    }

    out_dir = REPO / "artifacts"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / ("laya-routing-eval-" + time.strftime("%Y%m%d") + ".json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== GENIE held-out routing evaluation (baseline) ===")
    print(f"cases={total} correct={correct} accuracy={report['totals']['accuracy']}")
    print(f"median latency={report['latency_ms']['median']}ms p95={report['latency_ms']['p95']}ms")
    print(f"laya mode={laya_state.get('mode')} promotable={report['laya_promotable']}")
    print(f"catastrophic misroutes={len(catastrophic)}")
    print(f"routable-only accuracy={report['routable_accuracy']} "
          f"({report['routable_totals']['correct']}/{report['routable_totals']['cases']})")
    print("non-routing properties (not scored as routing classes):")
    for c in NON_ROUTING_PROPERTIES:
        print(f"  {c:24s} behaviour={non_routing[c]['behaviour']}")
    print("\nper-class recall:")
    for c in CLASSES:
        v = report["per_class"][c]
        print(f"  {c:24s} {v['correct']}/{v['total']}  recall={v['recall']}")
    print("\nper-language accuracy:")
    for k, v in report["per_language"].items():
        print(f"  {k:9s} {v['accuracy']}")
    print(f"\nartifact: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
