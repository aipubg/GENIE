"""Minimal Chrome DevTools Protocol client (browser/cdp).

Implements the small slice of CDP GENIE needs — page navigation, DOM query, DOM actions,
evaluation and screenshots — over a stdlib-only WebSocket client. No Node, no puppeteer,
no third-party Python packages.

Why not reuse PinchTab (supplied repo, MIT)? It is a Bun/TypeScript monorepo with a
dashboard; embedding it would add a Node toolchain and a second runtime to a daemon whose
whole point is being light on low-end Windows machines. PinchTab stays available as an
*optional external provider* behind the same BrowserProvider contract (D-040).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("browser.cdp")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

# Named Chromium-family browsers GENIE may operate over CDP. Brave and Edge are
# Chromium-based, so the same authorized integration drives them. An explicitly
# requested browser is honoured from here; if it is not installed we report the
# limitation instead of silently substituting another browser.
BROWSER_EXES: Dict[str, list] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
    "edge": [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
    "chromium": [
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ],
}


class CDPError(RuntimeError):
    pass


def find_browser() -> Optional[str]:
    for path in CHROME_CANDIDATES:
        if path and Path(path).exists():
            return path
    return None


def find_browser_for(name: str) -> Optional[str]:
    """Resolve a named browser to its executable, or None when not installed."""
    key = (name or "").strip().lower()
    if not key:
        return find_browser()
    for path in BROWSER_EXES.get(key, []):
        if path and Path(path).exists():
            return path
    return None


# --------------------------------------------------------------------- websocket
class WebSocket:
    """Just enough RFC 6455 for CDP: text frames, masking, ping/pong, fragmentation."""

    def __init__(self, url: str, timeout: float = 20.0):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise CDPError(f"unsupported websocket scheme: {url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.timeout = timeout
        self._buffer = b""
        self._lock = threading.Lock()
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake closed early")
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise CDPError(f"websocket handshake failed: {head.split(b'\r\n')[0]!r}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if expected.encode() not in head:
            log.debug("websocket accept header not verified (continuing)")
        self._buffer = rest

    # ------------------------------------------------------------------ frames
    def send(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])                     # FIN + text
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("websocket closed")
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def recv(self) -> str:
        """Receive one complete message (assembling fragments)."""
        message = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:                       # close
                raise CDPError("websocket closed by peer")
            if opcode == 0x9:                       # ping -> pong
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:                       # pong
                continue
            message += payload
            if fin:
                return message.decode("utf-8", errors="replace")

    def _send_control(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        header.append(0x80 | len(payload))
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def close(self) -> None:
        try:
            self._send_control(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ------------------------------------------------------------------------- HTTP
def http_json(port: int, path: str, timeout: float = 10.0) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    # Loopback CDP must NEVER go through a system/HTTP proxy: a proxy turns
    # http://127.0.0.1:9222/json/version into an HTTP 502 and the browser then
    # looks "not reachable" even though it is running normally.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise CDPError(f"CDP HTTP {path} failed: {exc}") from exc


def browser_ready(port: int, timeout_s: float = 20.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            return http_json(port, "/json/version", timeout=3.0)
        except Exception as exc:
            last = str(exc)
            time.sleep(0.4)
    raise CDPError(f"browser debug endpoint not reachable on port {port}: {last}")


# Point 7 — GENIE-owned browser processes. Only browsers GENIE itself launched
# are tracked here, so a controlled shutdown can never touch the owner's own
# Chrome/Edge. Keyed by pid; the value is the live Popen handle.
_LAUNCHED: dict = {}


def shutdown_owned(pid: int) -> bool:
    """Gracefully close ONLY a browser launched by this process.

    A Windows Popen.terminate() is a hard kill for Chromium and can cause the
    next start to show Restore pages. A failed graceful close leaves the
    profile/process intact and visible as a conflict to the next launch.
    """
    try:
        proc = _LAUNCHED.get(int(pid))
    except (TypeError, ValueError):
        return False
    if proc is None:
        return False
    if proc.poll() is not None:
        _LAUNCHED.pop(int(pid), None)
        return True
    try:
        port_arg = next((str(arg).split("=", 1)[1] for arg in proc.args
                         if str(arg).startswith("--remote-debugging-port=")), "")
        if not port_arg.isdigit():
            return False
        endpoint = browser_ready(int(port_arg), timeout_s=2).get("webSocketDebuggerUrl")
        if not endpoint:
            return False
        client = CDPClient(endpoint)
        try:
            client.send("Browser.close", timeout=2)
        except Exception:
            # Chromium may close the socket before it acknowledges Browser.close.
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
        proc.wait(timeout=8)
        _LAUNCHED.pop(int(pid), None)
        return True
    except Exception:
        return False


def owned_pids() -> list:
    """Pids of browsers GENIE currently owns (for status/reporting)."""
    return list(_LAUNCHED.keys())


def profile_in_use(profile: str | Path) -> list:
    """Pids of running Chromium-family browsers already holding *profile*.

    Chromium enforces a per-profile process singleton: a second launch with the
    same --user-data-dir hands the request to the first process and exits
    WITHOUT ever opening the remote-debugging port, so the caller would wait on
    an endpoint that will never appear. We therefore detect the holder up front
    and fail fast so the caller can rotate to a fresh profile.

    Strictly read-only: it enumerates processes. It never attaches to, signals
    or closes the other browser.
    """
    try:
        target = str(Path(profile).expanduser().resolve()).lower()
    except Exception:
        return []
    try:
        from .mode import _list_via_powershell_cim, _first_arg
    except Exception:
        return []
    try:
        rows = _list_via_powershell_cim()
    except Exception:
        return []
    if not rows:
        return []
    holders = []
    for row in rows:
        value = _first_arg(str(row.get("cmdline") or ""), "--user-data-dir")
        if not value:
            continue
        try:
            if str(Path(value).expanduser().resolve()).lower() == target:
                pid = int(row.get("pid") or 0)
                if pid:
                    holders.append(pid)
        except Exception:
            continue
    return holders


def launch(port: int = 9222, user_data_dir: str | Path | None = None,
           url: str = "about:blank", headless: bool = False,
           browser_path: str | None = None) -> Dict[str, Any]:
    exe = browser_path or find_browser()
    if not exe:
        raise CDPError("no Chrome/Edge installation found")
    # ALWAYS an absolute profile path. A RELATIVE --user-data-dir makes Chromium
    # exit immediately with code 0 and never open the remote-debugging port
    # (measured on Edge 154: relative = exit 0 after 4.4 s with no CDP endpoint,
    # absolute = CDP reachable in ~0.5 s). This was the real cause of "the
    # GENIE-owned browser launches but no CDP endpoint is obtained".
    if user_data_dir:
        profile = Path(user_data_dir).expanduser().resolve()
    else:
        from core.paths import data_dir
        profile = (data_dir() / "workspace" / "browser-profile").resolve()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise CDPError(f"debug port {port} is already in use")
    holders = profile_in_use(profile)
    if holders:
        raise CDPError(f"profile {profile} is already in use by pid(s) {holders}")
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--remote-allow-origins=*",
        # GENIE drives this browser over CDP, which never produces a real user
        # activation gesture (navigator.userActivation.isActive stayed false), so
        # YouTube's own autoplay policy kept media paused with currentTime at 0.
        # This flag applies ONLY to the GENIE-owned profile/process launched
        # here - the owner's normal Chrome profile and settings are untouched.
        "--autoplay-policy=no-user-gesture-required",
    ]
    if headless:
        args.append("--headless=new")
    if url:
        args.append(url)
    creation = 0
    if sys.platform.startswith("win"):
        creation = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW
    proc = subprocess.Popen(args, creationflags=creation,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Record ownership so a controlled shutdown stops exactly this browser.
    _LAUNCHED[proc.pid] = proc
    try:
        info = browser_ready(port)
        targets = page_targets(port)
        initial = next((t for t in targets if t.get("url") == url), {})
    except Exception:
        shutdown_owned(proc.pid)
        raise
    return {"ok": True, "pid": proc.pid, "exe": exe, "port": port,
            "profile": str(profile), "browser": info.get("Browser", ""),
            "webSocketDebuggerUrl": info.get("webSocketDebuggerUrl", ""),
            "initial_target": initial}


# ------------------------------------------------------------------------- client
class CDPClient:
    """One CDP connection (browser-level or page-level)."""

    def __init__(self, ws_url: str, timeout: float = 20.0):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._id = 0
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []

    def send(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = 20.0) -> Dict[str, Any]:
        with self._lock:
            self._id += 1
            msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise CDPError(f"{method} failed: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self._events.append(msg)
        raise CDPError(f"{method} timed out after {timeout}s")

    def events(self, clear: bool = False) -> List[Dict[str, Any]]:
        out = list(self._events)
        if clear:
            self._events.clear()
        return out

    def evaluate(self, expression: str, await_promise: bool = False,
                 timeout: float = 20.0) -> Any:
        res = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            detail = res["exceptionDetails"].get("exception", {}).get("description")
            raise CDPError(f"evaluate failed: {detail}")
        return (res.get("result") or {}).get("value")

    def close(self) -> None:
        self.ws.close()


def page_targets(port: int) -> List[Dict[str, Any]]:
    targets = http_json(port, "/json/list")
    return [t for t in targets if t.get("type") == "page"]


def new_page(port: int, url: str = "about:blank") -> Dict[str, Any]:
    """Open a new tab.

    Modern Chrome only accepts PUT on /json/new (GET returns 405), so try PUT first and fall
    back to GET for older builds.
    """
    from urllib.parse import quote
    path = f"/json/new?{quote(url, safe='')}"
    try:
        return _http_request(port, path, method="PUT", timeout=15)
    except CDPError:
        return http_json(port, path, timeout=15)


def _http_request(port: int, path: str, method: str = "GET",
                  timeout: float = 10.0) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"ok": True, "raw": body[:200]}
    except urllib.error.URLError as exc:
        raise CDPError(f"CDP HTTP {method} {path} failed: {exc}") from exc


def activate_page(port: int, target_id: str) -> None:
    try:
        http_json(port, f"/json/activate/{target_id}", timeout=10)
    except Exception:
        pass


def close_page(port: int, target_id: str) -> None:
    try:
        http_json(port, f"/json/close/{target_id}", timeout=10)
    except Exception:
        pass
