"""Line-delimited local worker, deliberately independent of packaged dependencies."""
import contextlib
import json
from pathlib import Path
import sys
import time


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from director.laya_shadow import QUESTIONS
    sys.path.insert(0, sys.argv[1])
    started = time.perf_counter()
    # Library progress is not part of the worker protocol.
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        torch.set_num_threads(2)
        from laya import Agent
        agent = Agent(sys.argv[2], device="cpu")
    print(json.dumps({"ready": True, "load_ms": round((time.perf_counter()-started)*1000, 1)}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        started = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            result = agent.system_one(request["text"][:2000], QUESTIONS)["answers"]["route"]
        print(json.dumps({"choice": result["choice"], "confidence": result.get("confidence"),
                          "inference_ms": round((time.perf_counter()-started)*1000, 2)}), flush=True)


if __name__ == "__main__":
    main()
