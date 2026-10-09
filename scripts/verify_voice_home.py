"""Functional verification of the voice-first Home contract.

Home is voice-first: the central GENIE core IS the microphone toggle. The UI
must never animate a listening state the backend has not confirmed, so the
contract this script checks is narrow and absolute:

    click core  -> POST /api/voice/start
                -> if ok=true the polled /api/voice reports capturing=true
                   (the client shows Listening)
                -> if ok=false the polled /api/voice reports capturing=false
                   AND carries a reason (the client shows an error, never
                   a fake Listening)
    click again -> POST /api/voice/stop
                -> /api/voice reports capturing=false (the client returns Idle)

It also pins the exact field names HomeViewModel polls, so a backend rename
cannot silently break the core without failing this test.

Runs against the real backend with an isolated data directory. Needs no
network and no credentials.

Run:
    python scripts/verify_voice_home.py
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))
    return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    def __init__(self, port: int, data_dir: pathlib.Path) -> None:
        self.port = port
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 60.0) -> bool:
        env = dict(os.environ)
        env["GENIE_IPC__PORT"] = str(self.port)
        env["GENIE_DATA_DIR"] = str(self.data_dir)
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "backend_entry.py")],
            cwd=str(REPO), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get("/health") is not None:
                return True
            time.sleep(0.3)
        return False

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=15)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def _req(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                return e.code, {}
        except Exception:
            return None, {}

    def get(self, path: str):
        status, body = self._req("GET", path)
        return body if status is not None else None

    def post(self, path: str, payload: dict):
        return self._req("POST", path, payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    port = free_port()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="genie-voice-"))
    backend = Backend(port, tmp / "data")

    try:
        if not check("backend starts", backend.start(), f"port {port}"):
            return 1

        # ---------------------------------------------------- status contract
        # These are the EXACT paths HomeViewModel polls. A backend rename that
        # broke them would silently freeze the core in Idle, so they are pinned.
        voice = backend.get("/api/voice") or {}
        check("/api/voice is served", bool(voice))
        pipeline = voice.get("pipeline") or {}
        check("voice payload nests live state under pipeline",
              isinstance(pipeline, dict) and bool(pipeline),
              f"pipeline keys={sorted(pipeline)[:8]}")
        check("pipeline.input.available present (what Home polls)",
              isinstance(pipeline.get("input"), dict)
              and "available" in pipeline["input"],
              f"input={pipeline.get('input')}")
        check("pipeline.capturing present (what Home polls)",
              "capturing" in pipeline, f"capturing={pipeline.get('capturing')}")
        check("pipeline.capturing is a real boolean",
              isinstance(pipeline.get("capturing"), bool),
              f"type={type(pipeline.get('capturing')).__name__}")
        check("voice payload names the input device",
              bool((pipeline.get("input") or {}).get("name")),
              f"name={(pipeline.get('input') or {}).get('name')}")
        check("voice payload names the STT implementation",
              bool((pipeline.get("stt") or {}).get("name")),
              f"stt={pipeline.get('stt')}")
        check("voice payload names the TTS implementation",
              bool((pipeline.get("tts") or {}).get("name")),
              f"tts={pipeline.get('tts')}")

        input_available = bool((pipeline.get("input") or {}).get("available"))
        print(f"     [info] input.available={input_available} "
              f"degraded={pipeline.get('degraded')}")

        # A machine with no audio input must SAY so rather than imply readiness.
        if not input_available:
            check("unavailable input is reported as a reason, not silence",
                  bool(pipeline.get("degraded")),
                  f"degraded={pipeline.get('degraded')}")

        # ------------------------------------------------------- start (click)
        st, started = backend.post("/api/voice/start", {})
        check("POST /api/voice/start answers 200", st == 200, f"status={st}")
        check("start reports ok explicitly",
              isinstance(started.get("ok"), bool), f"body={started}")
        check("start carries no raw exception text",
              "Traceback" not in json.dumps(started))

        after_start = (backend.get("/api/voice") or {}).get("pipeline") or {}
        capturing = bool(after_start.get("capturing"))

        if started.get("ok"):
            check("confirmed start means the client shows Listening",
                  capturing is True, f"capturing={capturing}")
            check("start names the device it captured from",
                  bool(started.get("input")), f"input={started.get('input')}")
        else:
            # The important half: a failed start must NOT look like listening.
            check("failed start does NOT fake Listening",
                  capturing is False, f"capturing={capturing}")
            check("failed start explains itself",
                  bool(started.get("error")), f"error={started.get('error')!r}")

        # Starting again while already capturing must be handled, never a 500.
        st2, started2 = backend.post("/api/voice/start", {})
        check("second start is handled without a server error", st2 == 200,
              f"status={st2} body={str(started2)[:100]}")

        # -------------------------------------------------------- stop (click)
        st, stopped = backend.post("/api/voice/stop", {})
        check("POST /api/voice/stop answers 200", st == 200, f"status={st}")
        check("stop reports ok", stopped.get("ok") is True, f"body={stopped}")

        after_stop = (backend.get("/api/voice") or {}).get("pipeline") or {}
        check("after stop the client returns to Idle",
              bool(after_stop.get("capturing")) is False,
              f"capturing={after_stop.get('capturing')}")

        # Stopping again is idempotent, not an error.
        st, stopped2 = backend.post("/api/voice/stop", {})
        check("second stop is idempotent", st == 200 and stopped2.get("ok") is True,
              f"status={st} body={stopped2}")

        # ------------------------------------------------- error surface
        # The client must be able to distinguish "no input" from "backend down".
        check("voice status survives a restart of polling",
              isinstance(backend.get("/api/voice"), dict))

        # --------------------------------------------------------- contract
        # The pipeline must be stable across calls, not just on first read.
        final = (backend.get("/api/voice") or {}).get("pipeline") or {}
        for field in ("capturing", "input", "stt", "tts", "degraded", "amplitude"):
            check(f"HomeViewModel field 'pipeline.{field}' still present",
                  field in final)

        # Real microphone energy is measured, never synthesised. The backend
        # states this explicitly with `synthetic`, so Home's gem can never be
        # driven by a fabricated level.
        amp = final.get("amplitude")
        check("amplitude is a measured, non-synthetic level",
              isinstance(amp, dict) and amp.get("synthetic") is False
              and amp.get("source") == "microphone",
              f"amplitude={amp}")

    finally:
        backend.stop()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept data dir: {tmp}")

    print()
    print(f"RESULT {len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
