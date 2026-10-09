"""Optional isolated, warm original-Laya worker. No execution authority."""
import atexit
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .base import DirectorDecision
from .laya_shadow import SOURCE_REVISION


class LayaRuntime:
    def __init__(self, config=None):
        get = config.get if config else lambda key, default=None: default
        self.mode = get("director.laya.mode", "shadow" if get("director.laya.shadow", False) else "off")
        # The evaluated model uses about 2.1 GB of RAM. Keep it out of normal
        # Chat/Live sessions unless shadow sampling is explicitly enabled after
        # an owner has accepted that resource cost.
        self.shadow_sampling = bool(get("director.laya.shadow_sampling", False))
        self.shadow_sample_interval_s = max(30.0, min(3600.0, float(
            get("director.laya.shadow_sample_interval_s", 120.0))))
        self._last_shadow_sample = 0.0
        self.python = str(get("director.laya.python", ""))
        self.source = str(get("director.laya.source", ""))
        self.model = str(get("director.laya.model", ""))
        self.timeout = max(.05, min(5., float(get("director.laya.timeout_s", .75))))
        self.threshold = max(.5, min(1., float(get("director.laya.confidence_threshold", .9))))
        self.last = ({"state": "disabled"} if self.mode == "off" else
                     {"state": "shadow_only", "sampling": "disabled_by_default"}
                     if self.mode == "shadow" and not self.shadow_sampling else
                     {"state": "not_started"})
        self._process = None
        self._responses = queue.Queue(maxsize=2)
        self._lock = threading.Lock()
        self._ready = False
        self._retry_after = 0.
        atexit.register(self.close)

    def start(self):
        """Never load Torch on a Chat/Voice thread. Calls fall back during warmup."""
        if self.mode == "shadow" and not self.shadow_sampling:
            self.last = {"state": "shadow_only", "sampling": "disabled_by_default"}
            return
        if self.mode not in ("shadow", "assist") or self._process or time.monotonic() < self._retry_after:
            return
        if not all(p and Path(p).exists() for p in (self.python, self.source, self.model)):
            self.last = {"state": "unavailable", "reason": "Configure isolated Python, pinned source and local checkpoint paths."}
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            revision = subprocess.run(["git", "-C", self.source, "rev-parse", "HEAD"],
                                      capture_output=True, text=True, timeout=2, creationflags=flags)
            if revision.returncode or revision.stdout.strip() != SOURCE_REVISION:
                raise ValueError("Laya source revision is not the pinned, evaluated revision.")
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
            self._responses = queue.Queue(maxsize=2)
            self._process = subprocess.Popen([self.python, str(Path(__file__).with_name("laya_worker.py")),
                                              self.source, self.model], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True, encoding="utf-8", bufsize=1, env=env, creationflags=flags)
            self.last = {"state": "warming"}
            threading.Thread(target=self._read, args=(self._process, self._responses), daemon=True,
                             name="laya-reader").start()
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            self.last = {"state": "unavailable", "reason": str(exc)[:200]}
            self._retry_after = time.monotonic() + 60

    def _read(self, process, responses):
        try:
            for line in process.stdout:
                payload = json.loads(line)
                if process is not self._process:
                    return
                if payload.get("ready"):
                    self._ready = True
                    self.last = {"state": "ready", "load_ms": payload.get("load_ms")}
                else:
                    responses.put_nowait(payload)
        except (ValueError, queue.Full):
            pass
        finally:
            if process is self._process:
                self._ready = False

    def predict(self, text):
        if self.mode == "off" or not self._lock.acquire(blocking=False):
            return None
        started = time.monotonic()
        try:
            self.start()
            if not self._ready or not self._process or self._process.poll() is not None:
                return None
            self._process.stdin.write(json.dumps({"text": str(text)[:2000]}, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            response = self._responses.get(timeout=self.timeout)
            choice, confidence = response.get("choice"), response.get("confidence")
            if (choice not in ("conversation", "simple_action", "research", "mission")
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1):
                raise ValueError("Invalid typed routing decision.")
            self.last = {"state": "predicted", **response,
                         "roundtrip_ms": round((time.monotonic() - started) * 1000, 2)}
            return self.last.copy()
        except (ValueError, OSError, queue.Empty) as exc:
            self.close()
            self._retry_after = time.monotonic() + 60
            self.last = {"state": "fallback", "reason": "timeout" if isinstance(exc, queue.Empty) else str(exc)[:200]}
            return None
        finally:
            self._lock.release()

    def route(self, text):
        if self.mode != "assist":
            return None
        result = self.predict(text)
        if not result or result["confidence"] < self.threshold:
            return None
        # Classification never synthesizes tool arguments, memory writes or missions.
        if result["choice"] not in ("simple_action", "research"):
            return None
        return DirectorDecision(source="laya", intent="simple_action", reasoning_required=True,
                                provider_category="research" if result["choice"] == "research" else "reasoning",
                                confidence=result["confidence"], raw={"engine": "laya", "runtime": "isolated-cpu",
                                    "latency_ms": result["roundtrip_ms"], "reason": "bounded-capability-reasoning"})

    def observe(self, text, baseline):
        if self.mode != "shadow" or not self.shadow_sampling:
            return
        now = time.monotonic()
        if now - self._last_shadow_sample < self.shadow_sample_interval_s:
            return
        self._last_shadow_sample = now
        def sample():
            result = self.predict(text)
            if result:
                self.last = {**result, "baseline": baseline, "match": result["choice"] == baseline}
        threading.Thread(target=sample, daemon=True, name="laya-shadow").start()

    def close(self):
        process, self._process = self._process, None
        self._ready = False
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
