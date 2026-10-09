"""Device node (devices/node.py).

A node is any machine that can carry out GENIE capabilities: a phone, a laptop, a Raspberry Pi,
or the reference node below. The node owns no GENIE policy — it executes what it is told, and
reports whether the effect was **verified**.

Run a real node:

    python -m devices.node --device-id phone_main --name "Pixel" --type android --port 8765

On first run the node prints a pairing code. The owner enters it on the PC:

    python genie.py devices-pair phone_main --code 123456

The code is never sent over the wire: both sides derive the same secret with PBKDF2, so an
unpaired device cannot produce a valid frame (see devices/protocol.py).

`ReferenceHandler` backs the node with *real* local operations (files, audio, clipboard) so the
mesh can be exercised end to end without an Android device. It is a genuine node — not a stub:
the protocol is the contract, and Android is one implementation of it.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger
from devices.contracts import (DEVICE_PROTOCOL_VERSION, UNPAIRED_SECRET, CommandEnvelope,
                               CommandStatus, DeviceManifest, DeviceType, ReplayWindow,
                               ResultEnvelope, decode_frame, encode_frame)

log = get_logger("devices.node")

HEARTBEAT_INTERVAL_S = 20.0
RECONNECT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 20.0)

Handler = Callable[[str, Dict[str, Any]], Dict[str, Any]]


# --------------------------------------------------------------------------- reference node
class ReferenceHandler:
    """Real local operations, exposed as device capabilities.

    Deliberately small and honest: each capability performs a real effect and reports whether
    it could be observed afterwards.
    """

    CAPABILITIES = [
        "files.write", "files.read", "files.exists", "files.list",
        "system.volume.set", "system.volume.up", "system.volume.down",
        "system.audio.state", "clipboard.get", "clipboard.set", "device.info",
    ]

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else None

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        if self.root is not None and not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate

    def __call__(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if capability == "files.write":
                target = self._resolve(str(params.get("path", "")))
                text = str(params.get("text", ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                written = target.read_text(encoding="utf-8")
                return {"ok": written == text, "verified": written == text,
                        "detail": f"{target} written ({len(text)} chars)",
                        "data": {"path": str(target), "bytes": len(text.encode('utf-8'))}}
            if capability == "files.read":
                target = self._resolve(str(params.get("path", "")))
                if not target.exists():
                    return {"ok": False, "verified": False, "detail": f"{target} missing",
                            "error_code": "not_found"}
                text = target.read_text(encoding="utf-8", errors="replace")
                return {"ok": True, "verified": True, "detail": f"{len(text)} chars",
                        "data": {"path": str(target), "text": text}}
            if capability == "files.exists":
                target = self._resolve(str(params.get("path", "")))
                return {"ok": target.exists(), "verified": True,
                        "detail": f"{target} exists={target.exists()}",
                        "data": {"exists": target.exists()}}
            if capability == "files.list":
                target = self._resolve(str(params.get("path", ".")))
                entries = sorted(p.name for p in target.iterdir()) if target.is_dir() else []
                return {"ok": True, "verified": True, "detail": f"{len(entries)} entries",
                        "data": {"entries": entries[:200]}}
            if capability == "system.volume.set":
                from computer import audio
                level = int(params.get("level", 50))
                out = audio.set_volume(level)
                state = audio.get_state()
                verified = int(state.get("volume", -1)) == level
                return {"ok": bool(out.get("ok")) and verified, "verified": verified,
                        "detail": f"volume set to {level} (now {state.get('volume')})",
                        "data": {"volume": state.get("volume")}}
            if capability == "system.volume.up":
                from computer import audio
                out = audio.volume_up()
                return {"ok": bool(out.get("ok")), "verified": bool(out.get("ok")),
                        "detail": out.get("detail", "volume up")}
            if capability == "system.volume.down":
                from computer import audio
                out = audio.volume_down()
                return {"ok": bool(out.get("ok")), "verified": bool(out.get("ok")),
                        "detail": out.get("detail", "volume down")}
            if capability == "system.audio.state":
                from computer import audio
                state = audio.get_state()
                return {"ok": True, "verified": True, "detail": f"volume={state.get('volume')}",
                        "data": state}
            if capability == "clipboard.get":
                from computer import windows_api as win
                return {"ok": True, "verified": True, "detail": "clipboard read",
                        "data": {"text": win.clipboard_get_text() or ""}}
            if capability == "clipboard.set":
                from computer import windows_api as win
                text = str(params.get("text", ""))
                ok = bool(win.clipboard_set_text(text))
                return {"ok": ok, "verified": ok, "detail": "clipboard written",
                        "data": {"text": text}}
            if capability == "device.info":
                import platform
                return {"ok": True, "verified": True, "detail": platform.platform(),
                        "data": {"platform": platform.platform(),
                                 "python": sys.version.split()[0]}}
        except Exception as exc:                       # a node must never die on one bad call
            return {"ok": False, "verified": False, "detail": str(exc),
                    "error_code": "handler_error"}
        return {"ok": False, "verified": False, "error_code": "unsupported_capability",
                "detail": f"this node does not implement {capability}"}


# --------------------------------------------------------------------- peripheral node
class PeripheralHandler:
    """A node backed by GPIO / relay / sensors (a Raspberry Pi, or a USB relay board).

    The capabilities it declares are exactly the ones its provider can honestly serve — a node
    with no backend declares nothing rather than advertising capabilities it cannot perform.
    """

    def __init__(self, provider: Optional[Any] = None):
        from devices.peripherals import PeripheralService, peripheral_capabilities
        self.service = PeripheralService(provider) if provider is not None else PeripheralService()
        self.CAPABILITIES = peripheral_capabilities(self.service.provider) + ["device.info"]

    def __call__(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if capability == "device.info":
            import platform
            return {"ok": True, "verified": True,
                    "detail": f"peripheral node on {platform.system().lower()}",
                    "data": {"platform": platform.system().lower(),
                             "backend": self.service.provider.backend,
                             "peripherals": len(self.service.provider.list())}}
        return self.service.handle(capability, params)


# --------------------------------------------------------------------------------- node
@dataclass
class DeviceNode:
    device_id: str
    name: str = ""
    type: str = DeviceType.VIRTUAL.value
    handler: Handler = field(default_factory=ReferenceHandler)
    host: str = "127.0.0.1"
    port: int = 8765
    secret: str = ""
    code: str = ""
    capabilities: List[str] = field(default_factory=list)
    platform_name: str = ""
    app_version: str = "0.1.0"

    _sock: Optional[socket.socket] = field(default=None, repr=False)
    _replay: ReplayWindow = field(default_factory=ReplayWindow, repr=False)
    _running: bool = field(default=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _paired: bool = field(default=True, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.device_id
        if not self.capabilities:
            self.capabilities = list(getattr(self.handler, "CAPABILITIES", []))
        if not self.platform_name:
            import platform
            self.platform_name = platform.system().lower()
        if not self.secret and self.code:
            from devices.protocol import derive_pairing_secret
            self.secret = derive_pairing_secret(self.code, self.device_id)

    # --------------------------------------------------------------------- manifest
    def manifest(self) -> DeviceManifest:
        return DeviceManifest(
            device_id=self.device_id, name=self.name, type=self.type,
            capabilities=list(self.capabilities), platform=self.platform_name,
            app_version=self.app_version, node_id=self.device_id,
            transport="tcp").normalised()

    # ------------------------------------------------------------------------ client
    def _connect(self) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as exc:
            log.debug("node %s cannot reach %s:%s (%s)", self.device_id, self.host,
                      self.port, exc)
            return False
        sock.settimeout(1.0)
        self._sock = sock
        return True

    def _frame_secret(self) -> str:
        """The secret to sign with: the pairing secret once paired, else the handshake value."""
        return self.secret or UNPAIRED_SECRET

    def _send(self, payload: Dict[str, Any]) -> bool:
        if self._sock is None:
            return False
        try:
            self._sock.sendall(encode_frame(payload, self._frame_secret()))
            return True
        except OSError:
            return False

    def _hello(self) -> bool:
        return self._send({"kind": "hello", "protocol": DEVICE_PROTOCOL_VERSION,
                           "device_id": self.device_id, "manifest": self.manifest().to_dict(),
                           "code_hint": bool(self.code), "app_version": self.app_version})

    def _read_frame(self) -> Optional[Dict[str, Any]]:
        if self._sock is None:
            return None
        buffer = b""
        try:
            while b"\n" not in buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    return None
                buffer += chunk
        except socket.timeout:
            return {}
        except OSError:
            return None
        line, _, _ = buffer.partition(b"\n")
        # Before pairing the daemon answers with the well-known handshake secret; afterwards it
        # uses the pairing secret. Try both so the transition needs no extra state.
        for secret in dict.fromkeys([self._frame_secret(), UNPAIRED_SECRET]):
            try:
                return decode_frame(line, secret)
            except ValueError:
                continue
        log.warning("node %s rejected a frame: signature mismatch", self.device_id)
        return {}

    # --------------------------------------------------------------------- execution
    def handle_command(self, command: CommandEnvelope) -> ResultEnvelope:
        """Execute one command. Idempotent: a repeated command_id returns the same result."""
        cached = self._replay.seen(command.command_id)
        if cached is not None:
            log.info("node %s: duplicate command %s — returning the cached result",
                     self.device_id, command.command_id)
            return ResultEnvelope.from_dict(cached)

        if command.expired():
            result = ResultEnvelope(command_id=command.command_id, device_id=self.device_id,
                                    status=CommandStatus.EXPIRED.value, ok=False,
                                    detail="command TTL elapsed before execution",
                                    error_code="expired")
            self._replay.remember(command.command_id, result.to_dict())
            return result

        if command.cancel:
            result = ResultEnvelope(command_id=command.command_id, device_id=self.device_id,
                                    status=CommandStatus.CANCELLED.value, ok=False,
                                    detail="cancelled", error_code="cancelled")
            self._replay.remember(command.command_id, result.to_dict())
            return result

        if command.capability not in self.capabilities:
            result = ResultEnvelope(command_id=command.command_id, device_id=self.device_id,
                                    status=CommandStatus.REJECTED.value, ok=False,
                                    detail=f"{command.capability} is not offered by "
                                           f"{self.device_id}",
                                    error_code="unsupported_capability")
            self._replay.remember(command.command_id, result.to_dict())
            return result

        started = time.time()
        outcome = self.handler(command.capability, dict(command.params or {})) or {}
        result = ResultEnvelope(
            command_id=command.command_id, device_id=self.device_id,
            status=(CommandStatus.COMPLETED.value if outcome.get("ok")
                    else CommandStatus.FAILED.value),
            ok=bool(outcome.get("ok")), verified=bool(outcome.get("verified", False)),
            detail=str(outcome.get("detail", "")), data=dict(outcome.get("data") or {}),
            error_code=str(outcome.get("error_code", "")),
            latency_ms=int((time.time() - started) * 1000))
        self._replay.remember(command.command_id, result.to_dict())
        return result

    # ------------------------------------------------------------------------ serving
    def serve_forever(self, *, max_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Connect, authenticate, then serve commands until stopped."""
        self._running = True
        self._stop.clear()
        deadline = time.time() + max_seconds if max_seconds else None
        attempt = 0
        served = 0
        last_heartbeat = 0.0
        while not self._stop.is_set():
            if deadline and time.time() > deadline:
                break
            if self._sock is None and not self._connect():
                delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
                attempt += 1
                self._stop.wait(delay)
                continue
            if not self._hello():
                self._close()
                continue
            attempt = 0
            while not self._stop.is_set():
                if deadline and time.time() > deadline:
                    break
                payload = self._read_frame()
                if payload is None:
                    break                       # disconnected
                if not payload:
                    now = time.time()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                        self._send({"kind": "heartbeat", "device_id": self.device_id,
                                    "at_ms": int(now * 1000)})
                        last_heartbeat = now
                    continue
                kind = payload.get("kind")
                if kind == "pair_required":
                    self._paired = False
                    log.warning("node %s is not paired yet — owner must run: "
                                "python genie.py devices-pair %s --code <code>",
                                self.device_id, self.device_id)
                    self._close()
                    self._stop.wait(3.0)
                    break
                if kind == "hello_ack":
                    if not payload.get("ok", True):
                        log.warning("node %s was rejected: %s", self.device_id,
                                    payload.get("reason"))
                        self._close()
                        self._stop.wait(3.0)
                        break
                    log.info("node %s accepted by the daemon", self.device_id)
                    continue
                if kind == "command":
                    result = self.handle_command(CommandEnvelope.from_dict(payload))
                    served += 1
                    self._send(result.to_dict())
                elif kind == "cancel":
                    self._send(ResultEnvelope(
                        command_id=str(payload.get("command_id", "")),
                        device_id=self.device_id, status=CommandStatus.CANCELLED.value,
                        detail="cancelled").to_dict())
            self._close()
        self._running = False
        return {"ok": True, "device_id": self.device_id, "served": served,
                "paired": self._paired}

    def start_background(self, **kwargs) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, kwargs=kwargs,
                                  name=f"device-node-{self.device_id}", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def _close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def status(self) -> Dict[str, Any]:
        return {"device_id": self.device_id, "connected": self._sock is not None,
                "paired": self._paired, "capabilities": self.capabilities,
                "running": self._running, "highest_counter": self._replay.highest_counter}


# ---------------------------------------------------------------------------------- cli
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="devices.node",
                                     description="Run a GENIE device node")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--type", default=DeviceType.VIRTUAL.value,
                        choices=[t.value for t in DeviceType])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--code", default="", help="pairing code shown by the owner")
    parser.add_argument("--root", default="", help="sandbox root for file capabilities")
    parser.add_argument("--peripherals", action="store_true",
                        help="expose GPIO/relay/sensor capabilities (Pi / USB relay node)")
    parser.add_argument("--peripheral-backend", default="",
                        help="sysfs | serial | memory (memory is a TEST backend, never auto-selected)")
    parser.add_argument("--seconds", type=float, default=0, help="0 = run until stopped")
    args = parser.parse_args(argv)

    from devices.protocol import generate_pairing_code
    code = args.code
    if not code:
        code = generate_pairing_code()
        print(f"pairing code for {args.device_id}: {code}")
        print(f"on the PC run:  python genie.py devices-pair {args.device_id} --code {code}")

    handler: Any
    if args.peripherals or args.peripheral_backend:
        from devices.peripherals import select_provider
        handler = PeripheralHandler(select_provider({"backend": args.peripheral_backend}
                                                   if args.peripheral_backend else {}))
        if not handler.CAPABILITIES:
            print("no peripheral backend is available on this host — the node would declare "
                  "nothing; refusing to start rather than pretending", file=sys.stderr)
            return 1
    else:
        handler = ReferenceHandler(Path(args.root) if args.root else None)

    node = DeviceNode(
        device_id=args.device_id, name=args.name, type=args.type,
        handler=handler, host=args.host, port=args.port, code=code)
    print(f"node {args.device_id} connecting to {args.host}:{args.port} "
          f"with {len(node.capabilities)} capabilities")
    report = node.serve_forever(max_seconds=args.seconds or None)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
