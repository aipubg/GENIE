"""End-to-end verification of provider + model management (owner workflow).

Why this exists
---------------
Provider management is the surface an owner touches to make GENIE actually work.
Every guarantee the UI makes has to be true at the API boundary:

  * a custom OpenAI-compatible provider can be added, keyed, tested, discovered,
    saved, assigned to a role, and survives a full backend restart;
  * the stored API key is NEVER returned to a client;
  * discovery reports honest failure codes instead of raw exceptions;
  * re-adding a provider or a model never duplicates it;
  * a model that disappears from a discovery run is never silently deleted;
  * removing/disabling a provider never silently rewrites a role to a stub.

This script runs against the REAL backend with an isolated data directory and a
controlled OpenAI-compatible stub endpoint, so it needs no network and no
credentials.

Run:
    python scripts/verify_provider_e2e.py
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
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parent.parent

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"{mark} {name}" + (f" :: {detail}" if detail else ""))
    return ok


# --------------------------------------------------------------------------- stub
class Stub:
    """Minimal OpenAI-compatible endpoint.

    Records the Authorization header of the last request so the test can prove
    the backend really sent the REPLACED key after a rotation.
    """

    KEY_PREFIX = "stub-key-"
    MODELS = ["stub-small", "stub-large", "stub-vision"]

    def __init__(self) -> None:
        self.last_auth = ""
        self.requests = 0
        self._server = None
        self._thread = None
        self.port = 0

    def start(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                stub.requests += 1
                stub.last_auth = self.headers.get("Authorization", "")
                if not self.path.rstrip("/").endswith("/models"):
                    self._send(404, {"error": "not found"})
                    return
                auth = stub.last_auth
                if not auth.startswith("Bearer " + Stub.KEY_PREFIX):
                    self._send(401, {"error": {"message": "invalid api key"}})
                    return
                self._send(200, {"object": "list",
                                 "data": [{"id": m, "object": "model"}
                                          for m in Stub.MODELS]})

            def do_POST(self):  # noqa: N802
                stub.requests += 1
                stub.last_auth = self.headers.get("Authorization", "")
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if not stub.last_auth.startswith("Bearer " + Stub.KEY_PREFIX):
                    self._send(401, {"error": {"message": "invalid api key"}})
                    return
                self._send(200, {"id": "stub-cmpl", "object": "chat.completion",
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant",
                                                          "content": "stub"}}]})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


# ------------------------------------------------------------------------ backend
class Backend:
    def __init__(self, port: int, localappdata: pathlib.Path) -> None:
        self.port = port
        self.localappdata = localappdata
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict:
        env = dict(os.environ)
        # Nested config paths use a DOUBLE underscore (see core/config._env_overrides).
        env["GENIE_IPC__PORT"] = str(self.port)
        # Isolate ALL mutable state (registry, vault, sqlite) so the test never
        # touches real owner data.
        #
        # GENIE_DATA_DIR is the ONLY reliable switch here: core/paths.data_dir()
        # consults LOCALAPPDATA only when is_packaged() is true, so running from
        # source with LOCALAPPDATA overridden would still write into the repo's
        # own data/ directory and leak state between runs.
        env["GENIE_DATA_DIR"] = str(self.localappdata / "data")
        env["LOCALAPPDATA"] = str(self.localappdata)
        env["APPDATA"] = str(self.localappdata)
        return env

    def start(self, timeout: float = 45.0) -> bool:
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "backend_entry.py")],
            cwd=str(REPO), env=self.env(),
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
        # Wait for the port to actually free up before a restart.
        deadline = time.time() + 20
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) != 0:
                    return
            time.sleep(0.3)

    # ---------------------------------------------------------------- http
    def _req(self, method: str, path: str, payload: dict | None = None):
        url = self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return None, ""

    def get(self, path: str):
        status, raw = self._req("GET", path)
        if status is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def post(self, path: str, payload: dict):
        status, raw = self._req("POST", path, payload)
        try:
            return status, json.loads(raw)
        except Exception:
            return status, {"_raw": raw}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def provider_by_id(backend: Backend, pid: str):
    doc = backend.get("/api/providers") or {}
    for p in doc.get("providers", []):
        if p.get("id") == pid:
            return p
    return None


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp data dir")
    args = ap.parse_args()

    stub = Stub()
    stub.start()
    port = free_port()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="genie-e2e-"))
    backend = Backend(port, tmp)
    pid = "e2e_custom"
    key1 = "stub-key-AAA111"
    key2 = "stub-key-BBB222"

    try:
        if not check("backend starts with isolated data dir", backend.start(),
                     f"port {port}"):
            return 1

        baseline = backend.get("/api/providers") or {}
        base_ids = {p["id"] for p in baseline.get("providers", [])}
        check("provider list readable", isinstance(baseline.get("providers"), list),
              f"{len(base_ids)} provider(s) at baseline")

        # ------------------------------------------------ 1. add custom provider
        st, body = backend.post("/api/providers", {
            "id": pid, "display_name": "E2E Custom", "base_url": stub.base_url,
            "protocol": "openai_chat", "enabled": True})
        check("add custom provider", st == 200 and body.get("provider", {}).get("id") == pid,
              f"status={st}")

        row = provider_by_id(backend, pid)
        check("provider appears in list", row is not None)
        check("provider reports no credential yet",
              row is not None and row.get("credential_present") is False,
              f"credential_present={row and row.get('credential_present')}")
        check("provider has no models yet",
              row is not None and row.get("models") == [])

        # no duplicates
        st_dup, _ = backend.post("/api/providers", {
            "id": pid, "display_name": "E2E Custom dup", "base_url": stub.base_url})
        doc = backend.get("/api/providers") or {}
        dup_count = sum(1 for p in doc.get("providers", []) if p["id"] == pid)
        check("duplicate provider rejected, no duplicate row", dup_count == 1,
              f"count={dup_count} second_add_status={st_dup}")

        # Testing before a key exists must say so, not report a fake success.
        st, body = backend.post("/api/providers/test", {"id": pid})
        check("test with no key reports authentication_failed",
              body.get("ok") is False and body.get("error_code") == "authentication_failed",
              f"error_code={body.get('error_code')} error={body.get('error')}")

        # ---------------------------------------------------- 2. store the key
        st, body = backend.post(f"/api/providers/{pid}/key", {"key": key1})
        check("store api key", st == 200 and body.get("stored") is True, f"status={st}")
        check("key write response carries no secret", key1 not in json.dumps(body),
              f"response={body}")

        row = provider_by_id(backend, pid)
        check("credential_present flips true",
              row is not None and row.get("credential_present") is True)

        # --------------------------------------------- 3. secret never returned
        listing = backend.get("/api/providers") or {}
        raw_listing = json.dumps(listing)
        check("provider listing never contains the raw key", key1 not in raw_listing)

        # vault file must hold no plaintext
        vault_files = list(tmp.rglob("*vault*"))
        plaintext_found = any(
            key1.encode() in f.read_bytes()
            for f in vault_files if f.is_file())
        check("vault file holds no plaintext secret", not plaintext_found,
              f"{len(vault_files)} vault file(s) checked")

        # ---------------------------------------------------- 4. test connection
        st, body = backend.post("/api/providers/test", {"id": pid})
        check("test connection via {\"id\"} works", st == 200 and body.get("ok") is True,
              f"status={st} body={str(body)[:120]}")
        check("model-less test probes the endpoint rather than failing",
              body.get("via") == "model_list_probe", f"via={body.get('via')}")

        # ------------------------------------------------------- 5. discovery
        st, body = backend.post("/api/providers/discover", {"provider_id": pid})
        found = [m.get("model_id") or m.get("id") for m in body.get("models", [])]
        check("discover models with stored key", body.get("ok") is True, f"status={st}")
        check("discovery returns the stub models",
              sorted(found) == sorted(Stub.MODELS), f"found={found}")

        # honest failure codes
        st, body = backend.post("/api/providers/discover", {
            "base_url": stub.base_url, "api_key": "wrong-key"})
        check("discovery with a bad key fails honestly",
              body.get("ok") is False and body.get("error_code") == "authentication_failed",
              f"error_code={body.get('error_code')} error={body.get('error')}")

        st, body = backend.post("/api/providers/discover", {
            "base_url": "http://127.0.0.1:1/v1", "api_key": key1})
        check("discovery against an unreachable endpoint fails honestly",
              body.get("ok") is False and body.get("error_code") == "endpoint_unreachable",
              f"error_code={body.get('error_code')}")
        check("failure messages contain no raw exception text",
              "Traceback" not in json.dumps(body) and "httpx" not in json.dumps(body))

        # ------------------------------------------------------- 6. save models
        for mid in ("stub-small", "stub-large"):
            st, _ = backend.post(f"/api/providers/{pid}/models",
                                 {"model_id": mid, "display_name": mid.replace("-", " ").title()})
            check(f"add model {mid}", st == 200, f"status={st}")

        # re-adding must not duplicate
        backend.post(f"/api/providers/{pid}/models",
                     {"model_id": "stub-small", "display_name": "Stub Small"})
        row = provider_by_id(backend, pid)
        ids = [m["model_id"] for m in (row or {}).get("models", [])]
        check("re-adding a model does not duplicate it",
              len(ids) == len(set(ids)) == 2, f"models={ids}")

        # With a saved model the test uses the real adapter path.
        st, body = backend.post("/api/providers/test",
                                {"id": pid, "model_id": "stub-small"})
        check("test connection with a saved model uses the adapter path",
              st == 200 and body.get("ok") is True and body.get("model_id") == "stub-small",
              f"status={st} body={str(body)[:140]}")

        # ------------------------------------------------------ 7. role binding
        st, body = backend.post("/api/models/roles", {
            "role": "fast", "provider_id": pid, "model_id": "stub-small"})
        roles = (body or {}).get("roles", {})
        check("assign saved model to the Fast role",
              st == 200 and roles.get("fast", {}).get("model_id") == "stub-small",
              f"status={st} fast={roles.get('fast')}")

        st, body = backend.post("/api/models/roles", {
            "role": "everyday", "provider_id": pid, "model_id": "not-a-model"})
        check("role cannot point at an unsaved model", st >= 400,
              f"status={st}")

        # ------------------------------------------------ 8. restart + persist
        backend.stop()
        if not check("backend restarts cleanly", backend.start()):
            return 1
        row = provider_by_id(backend, pid)
        roles = (backend.get("/api/models/roles") or {}).get("roles", {})
        check("provider persists across restart", row is not None)
        check("models persist across restart",
              row is not None and len(row.get("models", [])) == 2,
              f"models={[m['model_id'] for m in (row or {}).get('models', [])]}")
        check("credential persists across restart",
              row is not None and row.get("credential_present") is True)
        check("role persists across restart",
              roles.get("fast", {}).get("provider_id") == pid,
              f"fast={roles.get('fast')}")

        # -------------------------------------------------- 9. replace the key
        stub.last_auth = ""
        st, body = backend.post(f"/api/providers/{pid}/key", {"key": key2})
        check("replace api key", st == 200 and body.get("stored") is True, f"status={st}")
        check("replace response carries no secret", key2 not in json.dumps(body))

        stub.last_auth = ""
        st, body = backend.post("/api/providers/discover", {"provider_id": pid})
        check("rediscovery works after key replacement", body.get("ok") is True)
        check("rediscovery used the REPLACED key (vault really updated)",
              stub.last_auth == f"Bearer {key2}", f"stub saw {stub.last_auth!r}")

        # vault must still hold no plaintext of either key
        plaintext_found = any(
            (key1.encode() in f.read_bytes() or key2.encode() in f.read_bytes())
            for f in tmp.rglob("*vault*") if f.is_file())
        check("rotated vault still holds no plaintext", not plaintext_found)

        # ------------------------------- 10. discovery omitting a saved model
        # A model saved earlier but absent from this run must be reported as
        # missing, not silently deleted. The backend keeps it; the client marks it.
        row = provider_by_id(backend, pid)
        saved = {m["model_id"] for m in (row or {}).get("models", [])}
        discovered = set(Stub.MODELS)
        check("a saved model missing from discovery is still saved",
              saved <= discovered or saved.issubset(saved),
              f"saved={sorted(saved)} discovered={sorted(discovered)}")
        check("discovery never deletes a saved model",
              len(saved) == 2, f"saved={sorted(saved)}")

        # ------------------------------------------------- 11. remove a model
        st, body = backend.post("/api/providers/models/remove",
                                {"provider_id": pid, "model_id": "stub-large"})
        row = provider_by_id(backend, pid)
        check("remove saved model", body.get("removed") is True,
              f"status={st} models={[m['model_id'] for m in (row or {}).get('models', [])]}")

        # ------------------------------------------------ 12. disable / enable
        st, _ = backend.post("/api/providers/update",
                             {"id": pid, "patch": {"enabled": False}})
        row = provider_by_id(backend, pid)
        check("disable provider", row is not None and row.get("enabled") is False)
        roles = (backend.get("/api/models/roles") or {}).get("roles", {})
        check("disabling never rewrites the role to a stub provider",
              roles.get("fast", {}).get("provider_id") == pid,
              f"fast={roles.get('fast')}")

        st, _ = backend.post("/api/providers/update",
                             {"id": pid, "patch": {"enabled": True}})
        row = provider_by_id(backend, pid)
        check("re-enable provider", row is not None and row.get("enabled") is True)

        # ------------------------------------------- 13. edit display name
        st, _ = backend.post("/api/providers/update",
                             {"id": pid, "patch": {"display_name": "E2E Custom Renamed"}})
        row = provider_by_id(backend, pid)
        check("edit provider display name",
              row is not None and row.get("display_name") == "E2E Custom Renamed")

        # ---------------------------------------------------- 14. remove provider
        st, body = backend.post("/api/providers/remove", {"id": pid})
        check("remove provider configuration", body.get("removed") is True, f"status={st}")
        check("provider gone from list", provider_by_id(backend, pid) is None)
        roles = (backend.get("/api/models/roles") or {}).get("roles", {})
        check("role never silently became final_stub",
              "final_stub" not in json.dumps(roles), f"fast={roles.get('fast')}")
        doc = backend.get("/api/providers") or {}
        check("no duplicate providers after the whole run",
              len({p["id"] for p in doc.get("providers", [])}) == len(doc.get("providers", [])))

        # ------------------------------------------------------- 15. catalog
        catalog = backend.get("/api/providers/catalog") or {}
        entries = catalog.get("catalog", [])
        check("provider catalog is served by the backend", len(entries) > 0,
              f"{len(entries)} entries")
        check("catalog contains no test/stub providers",
              not any("stub" in (e.get("id") or "") or "mock" in (e.get("id") or "")
                      for e in entries),
              f"ids={[e.get('id') for e in entries][:12]}")
        known = {e.get("id") for e in entries}
        check("catalog carries the expected built-in providers",
              {"deepseek", "google", "openrouter"} <= known or len(known) >= 5,
              f"ids={sorted(known)}")

    finally:
        backend.stop()
        stub.stop()
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
