"""Reproducible local routing comparison. No OS actions or provider calls."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from core.contracts import CallContext
from core.orchestrator import _fast_deterministic_decision
from director.laya_runtime import LayaRuntime
from director.laya_shadow import SOURCE_REVISION, MODEL_REVISION
from director.nedle2 import get_director


CASES = [
    ("Hello, how are you?", "conversation"),
    ("Namaste, kaise ho?", "conversation"),
    ("\u0928\u092e\u0938\u094d\u0924\u0947", "conversation"),
    ("What did we discuss yesterday?", "conversation"),
    ("YouTube kholo aur sad song chalao", "simple_action"),
    ("Set volume to 20 percent", "simple_action"),
    ("Brave mein YouTube kholo", "simple_action"),
    ("WhatsApp mein meri existing chat dikhao", "simple_action"),
    ("Desktop par naya folder banao", "simple_action"),
    ("\u0906\u0935\u093e\u091c \u0915\u092e \u0915\u0930\u094b", "simple_action"),
    ("Find current information about WorkBuddy AI from several websites", "research"),
    ("Internet par ChatGPT ke baare mein jaankari dhoondo", "research"),
    ("Compare current public pricing for these two products", "research"),
    ("\u0907\u0902\u091f\u0930\u0928\u0947\u091f \u092a\u0930 \u0928\u0908 \u091c\u093e\u0928\u0915\u093e\u0930\u0940 \u0916\u094b\u091c\u094b", "research"),
    ("Every morning check the weather and send me a summary", "mission"),
    ("Har subah nau baje weather check karna", "mission"),
    ("Remind me tomorrow at 8 am to call the dentist", "mission"),
    ("Roz shaam mere folder ka backup karna", "mission"),
    ("Open the website once, do not schedule anything", "simple_action"),
    ("Tell me what a recurring mission means", "conversation"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = Config({"director": {"laya": {"mode": "shadow", "python": sys.executable,
                    "source": args.source, "model": args.model, "timeout_s": 5}}})
    runtime = LayaRuntime(config)
    started = time.perf_counter()
    runtime.start()
    while not runtime._ready and time.perf_counter() - started < 60:
        if runtime.last["state"] == "unavailable":
            raise RuntimeError(runtime.last)
        time.sleep(.05)
    cold_ms = round((time.perf_counter() - started) * 1000, 2)
    cold_state = runtime.last.copy()
    baseline = get_director(Config.load())
    rows = []
    try:
        for text, expected in CASES:
            start = time.perf_counter()
            fast = _fast_deterministic_decision(text, CallContext(), {})
            fast_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            decision = baseline.classify(text, CallContext(), {})
            baseline_ms = (time.perf_counter() - start) * 1000
            result = runtime.predict(text)
            rows.append({"input": text, "expected": expected, "fast_path": fast.source if fast else None,
                         "fast_ms": round(fast_ms, 3), "baseline_source": decision.source,
                         "baseline_intent": decision.intent, "baseline_ms": round(baseline_ms, 3),
                         "laya": result, "match": bool(result and result["choice"] == expected)})
    finally:
        runtime.close()
        close = getattr(baseline, "close", None)
        if close:
            close()
    times = [r["laya"]["roundtrip_ms"] for r in rows if r["laya"]]
    report = {"source_revision": SOURCE_REVISION, "model_revision": MODEL_REVISION,
              "cold_process_ready_ms": cold_ms, "cold_state": cold_state,
              "warm_roundtrip_median_ms": statistics.median(times) if times else None,
              "accuracy": sum(r["match"] for r in rows) / len(rows),
              "baseline_median_ms": statistics.median(r["baseline_ms"] for r in rows),
              "results": rows, "scope": "real local inference; no GUI, Voice or tool execution"}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
