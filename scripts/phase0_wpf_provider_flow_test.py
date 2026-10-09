"""P0 Closure #1 — prove the ACTUAL WPF provider-edit API path.

Mimics SettingsViewModel.SaveProviderEditAsync exactly:
  * AddProviderAsync   -> POST /api/providers
  * SetProviderKeyAsync -> POST /api/providers/key   (only when key dirty)
  * UpdateProviderAsync -> POST /api/providers/update (base_url + Advanced)
  * AddModelAsync      -> POST /api/providers/models

Runs against a REAL daemon HTTP server on isolated GENIE_DATA_DIR so the
restart really reloads from disk.

Never prints secrets.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class Daemon:
    def __init__(self, port: int):
        self.port = port

    def _post(self, path: str, body: dict) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body).encode()
        conn.request("POST", path, body=data,
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        txt = r.read().decode()
        conn.close()
        return json.loads(txt) if txt else {}

    def add_provider(self, pid, name, url, headers, auth, disc, timeout, custom):
        return self._post("/api/providers", {
            "id": pid, "display_name": name, "base_url": url,
            "protocol": "openai_chat", "custom": custom,
            "headers": headers, "auth_scheme": auth,
            "discovery_url": disc, "timeout": timeout,
        })

    def set_key(self, pid, key):
        return self._post("/api/providers/key", {"id": pid, "key": key})

    def update(self, pid, patch):
        return self._post("/api/providers/update", {"id": pid, "patch": patch})

    def add_model(self, pid, mid):
        return self._post(f"/api/providers/{pid}/models",
                          {"model_id": mid, "capabilities": ["general"]})

    def get(self, pid):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", "/api/providers")
        r = conn.getresponse()
        txt = r.read().decode()
        conn.close()
        data = json.loads(txt) if txt else {}
        for p in data.get("providers", []):
            if p.get("id") == pid:
                return {"provider": p, "all": data.get("providers", [])}
        return {"provider": None, "all": data.get("providers", [])}


def start_daemon(iso: Path):
    from core.lifecycle import Daemon as _D
    from core.config import get_config
    from core.ipc.server import IPCServer
    cfg = get_config()
    d = _D(cfg)
    d.start()
    port = int(cfg.get("ipc.port", 8787))
    srv = IPCServer(d, host=cfg.get("ipc.host", "127.0.0.1"), port=port)
    srv.start(background=True)
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    return d, port


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p0wpf_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print(f"isolated data dir: {iso}\n")

    import core.config
    import importlib
    importlib.reload(core.config)

    PID = "wpfedit"
    URL_A = "https://example-a.test/v1"
    URL_B = "https://example-b.test/v1"

    # --- first daemon lifecycle (create + edit) ---
    d, port = start_daemon(iso)
    api = Daemon(port)

    # 1. AddProviderAsync (WPF SaveProviderAsync create branch)
    api.add_provider(PID, "WPF Edit", URL_A, {"X-Test": "a"}, "bearer", "", 20.0, True)
    api.set_key(PID, "not-a-real-secret")
    for m in ("m1", "m2", "m3"):
        api.add_model(PID, m)
    check("created with URL A", api.get(PID)["provider"]["base_url"] == URL_A)
    check("3 models saved", len(api.get(PID)["provider"]["models"]) == 3)

    # 2. WPF SaveProviderEditAsync — full Advanced patch, NO key in patch
    api.update(PID, {
        "display_name": "WPF Edit", "base_url": URL_B,
        "auth_scheme": "bearer", "discovery_url": "",
        "headers": {"X-Test": "b"}, "timeout": 45.0,
    })
    check("edited to URL B", api.get(PID)["provider"]["base_url"] == URL_B)
    d.stop()

    # 3. RESTART — new daemon reloads from disk
    d2, port2 = start_daemon(iso)
    api2 = Daemon(port2)
    p = api2.get(PID)["provider"]
    check("after restart URL B (not stale A)", p["base_url"] == URL_B, p["base_url"])
    check("models preserved after restart", len(p["models"]) == 3,
          str([m["model_id"] for m in p["models"]]))
    check("headers persisted", p.get("headers") == {"X-Test": "b"})
    check("timeout persisted", p.get("timeout") == 45.0)
    check("secret_ref present (key never in patch)",
          bool(p.get("has_secret_ref")))
    all_providers = api2.get(PID).get("all", [])
    check("no duplicate provider",
          sum(1 for x in all_providers if x.get("id") == PID) == 1,
          f"{sum(1 for x in all_providers if x.get('id') == PID)} instance(s)")
    d2.stop()

    shutil.rmtree(iso.parent, ignore_errors=True)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
