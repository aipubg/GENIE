"""Optional Laya shadow routing. Suggestions cannot execute or change live routing.

An external Python process isolates Torch from the packaged voice runtime. Only
one sample can be in flight, with a hard process timeout and bounded input.
"""
import json
from pathlib import Path
import subprocess
import threading
import time

SOURCE_REVISION = "1e28ac20c0896b1c37a744cd11f740eb98f8b178"
MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
QUESTIONS = {"route": {
    "type": "choice",
    "instructions": "Classify the user's request. A one-time action is not a durable mission.",
    "criteria": {
        "conversation": "Greeting, ordinary conversation or a question about prior conversation",
        "simple_action": "One-time computer, browser, audio or application action",
        "research": "Look up information or read and compare online sources",
        "mission": "Explicit scheduled, recurring or durable multi-step autonomous project",
    }}}


class LayaShadow:
    def __init__(self, config):
        self.enabled = bool(config.get("director.laya.shadow", False)) if config else False
        self.python = str(config.get("director.laya.python", "")) if config else ""
        self.source = str(config.get("director.laya.source", "")) if config else ""
        self.model = str(config.get("director.laya.model", "")) if config else ""
        self._busy = threading.Lock()
        self.last = {"state": "disabled" if not self.enabled else "awaiting_sample"}

    def observe(self, text, baseline):
        if not self.enabled or not self._busy.acquire(blocking=False):
            return
        threading.Thread(target=self._run, args=(text[:2000], baseline), daemon=True,
                         name="laya-shadow").start()

    def _run(self, text, baseline):
        started = time.monotonic()
        try:
            if not all(Path(p).exists() for p in (self.python, self.source, self.model)) or not all((self.python, self.source, self.model)):
                raise ValueError("Configure local Python, pinned Laya source and checkpoint paths first.")
            process = subprocess.run(
                [self.python, str(Path(__file__).resolve()), self.source, self.model],
                input=json.dumps({"text": text}), encoding="utf-8", capture_output=True,
                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                raise ValueError("Laya worker failed; verify its isolated dependencies/checkpoint.")
            result = json.loads(process.stdout.strip().splitlines()[-1])
            self.last = {"state": "shadow_only", "baseline": baseline, **result,
                         "total_ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:
            self.last = {"state": "unavailable", "error": str(exc)[:200]}
        finally:
            self._busy.release()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, sys.argv[1])
    import torch
    torch.set_num_threads(2)
    from laya import Agent
    agent = Agent(sys.argv[2], device="cpu")
    payload = json.loads(sys.stdin.read())
    start = time.perf_counter()
    result = agent.system_one(payload["text"], QUESTIONS)
    answer = result["answers"]["route"]
    print(json.dumps({"choice": answer["choice"], "confidence": answer.get("confidence"),
                      "inference_ms": round((time.perf_counter() - start) * 1000, 1)}))
