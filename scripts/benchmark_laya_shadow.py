"""CPU routing probe for a locally downloaded, pinned Laya checkpoint."""
import argparse
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--model", required=True)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, args.source)
import torch
torch.set_num_threads(2)
from laya import Agent
from director.laya_shadow import QUESTIONS, SOURCE_REVISION, MODEL_REVISION

start = time.perf_counter()
agent = Agent(args.model, device="cpu")
load_ms = round((time.perf_counter() - start) * 1000, 1)
cases = [("Hello, how are you?", "conversation"),
         ("YouTube kholo aur sad song chalao", "simple_action"),
         ("Find current information about WorkBuddy AI from several websites", "research"),
         ("Every morning check the weather and send me a summary", "mission")]
rows = []
for text, expected in cases:
    start = time.perf_counter()
    answer = agent.system_one(text, QUESTIONS)["answers"]["route"]
    rows.append({"input": text, "expected": expected, "actual": answer["choice"],
                 "match": answer["choice"] == expected, "confidence": answer.get("confidence"),
                 "inference_ms": round((time.perf_counter() - start) * 1000, 1)})
print(json.dumps({"source_revision": SOURCE_REVISION, "model_revision": MODEL_REVISION,
                  "load_ms": load_ms, "results": rows}, indent=2))
