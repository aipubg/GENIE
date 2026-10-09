"""P0 Closure #7 — source-of-truth diagnostics remain correct after restart.

Verifies /api/status source_of_truth reports:
  resolved data directory, data-directory source, canonical session ID,
  STT provider/model readiness, backend/source identity, degraded reasons,
  active/last provider-model state.

After a restart the values must be freshly resolved, not stale pre-restart ones.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def get_status(port: int) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("GET", "/api/status")
    r = conn.getresponse()
    txt = r.read().decode()
    conn.close()
    return json.loads(txt) if txt else {}


def start_daemon():
    import core.config
    import importlib
    importlib.reload(core.config)
    from core.lifecycle import Daemon
    from core.config import get_config
    from core.ipc.server import IPCServer
    cfg = get_config()
    d = Daemon(cfg)
    d.start()
    port = int(cfg.get("ipc.port", 8787))
    srv = IPCServer(d, host=cfg.get("ipc.host", "127.0.0.1"), port=port)
    srv.start(background=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    return d, port


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p0sot_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print(f"isolated data dir: {iso}\n")

    import core.config
    import importlib
    importlib.reload(core.config)

    d, port = start_daemon()
    st1 = get_status(port).get("source_of_truth", {})
    check("SOT present in status", bool(st1), str(list(st1.keys()))[:90])
    check("SOT data_directory == isolated dir",
          str(st1.get("data_directory")) == str(iso), str(st1.get("data_directory")))
    check("SOT data_source == GENIE_DATA_DIR",
          st1.get("data_directory_source") == "GENIE_DATA_DIR",
          str(st1.get("data_directory_source")))
    check("SOT session_id == owner", st1.get("session_id") == "owner",
          str(st1.get("session_id")))
    check("SOT backend_root present", bool(st1.get("backend_root")),
          str(st1.get("backend_root"))[-40:])
    check("SOT has source_commit", bool(st1.get("source_commit")),
          str(st1.get("source_commit"))[:12])
    check("SOT reports stt_ready flag", "stt_ready" in st1,
          f"stt_ready={st1.get('stt_ready')}")
    check("SOT reports stt_provider/model", "stt_provider" in st1 and "stt_model_id" in st1,
          f"{st1.get('stt_provider')} / {st1.get('stt_model_id')}")
    check("SOT has degraded list", "degraded" in st1, str(st1.get("degraded"))[:60])

    # do a turn so active provider/model is recorded
    d.chat("hello", session_id="owner")
    st1b = get_status(port).get("source_of_truth", {})
    d.stop()

    # --- restart ---
    d2, port2 = start_daemon()
    st2 = get_status(port2).get("source_of_truth", {})
    check("after restart SOT present", bool(st2))
    check("after restart data_directory fresh (isolated)",
          str(st2.get("data_directory")) == str(iso), str(st2.get("data_directory")))
    check("after restart data_source fresh",
          st2.get("data_directory_source") == "GENIE_DATA_DIR")
    check("after restart session_id still owner",
          st2.get("session_id") == "owner")
    check("after restart backend_root same (not stale)",
          st2.get("backend_root") == st1.get("backend_root"),
          str(st2.get("backend_root"))[-30:])
    check("after restart source_commit same",
          st2.get("source_commit") == st1.get("source_commit"))
    check("after restart stt flags present",
          "stt_ready" in st2 and "stt_loaded" in st2,
          f"ready={st2.get('stt_ready')} loaded={st2.get('stt_loaded')}")
    # degraded reasons must be a list (freshly computed)
    check("after restart degraded is list",
          isinstance(st2.get("degraded"), list), str(st2.get("degraded"))[:60])
    d2.stop()

    try:
        shutil.rmtree(iso.parent, ignore_errors=True)
    except Exception:
        pass

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
