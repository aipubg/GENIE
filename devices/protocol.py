"""Device transport (devices/protocol.py).

A line-delimited, signed JSON protocol over TCP. The node dials the daemon (so no inbound port
on the phone, no router configuration), then the same connection carries commands and results in
both directions.

Security model (master spec §6):
  * **Pairing by code, TOFU + explicit confirm.** The node shows a short code; the owner enters
    it on the PC. Both sides derive the same secret with PBKDF2, so the code never crosses the
    wire and a device that never paired cannot send a valid frame.
  * **Every frame is HMAC-signed** with the pairing secret; a tampered or forged frame is
    rejected before it is parsed into anything meaningful.
  * **Replay protection**: a monotonic counter per direction plus `command_id` dedupe.
  * The transport is deliberately small. A relay (when the phone is not on the LAN) is a later
    transport behind the same interface, not a second protocol.
"""
from __future__ import annotations

import hashlib
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger
from devices.contracts import (UNPAIRED_SECRET, CommandEnvelope, CommandStatus, DeviceManifest,
                               ReplayWindow, ResultEnvelope, decode_frame, encode_frame)

log = get_logger("devices.protocol")

DEFAULT_PORT = 8765
PAIRING_ITERATIONS = 120_000
MAX_FRAME_BYTES = 4 * 1024 * 1024


def derive_pairing_secret(code: str, device_id: str) -> str:
    """Turn a short owner-entered pairing code into the shared secret.

    PBKDF2 keeps a 6-digit code from being a trivially brute-forced key if the transport is
    ever observed, and binds the secret to the device id so one code cannot pair another device.
    """
    material = f"{device_id}:{(code or '').strip()}".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, b"genie-device-pairing",
                               PAIRING_ITERATIONS).hex()


def generate_pairing_code() -> str:
    """A short, human-typable code (shown by the node, entered by the owner)."""
    import secrets
    return f"{secrets.randbelow(1_000_000):06d}"


class Connection:
    """One framed, signed socket. Writes are serialised; reads are single-consumer."""

    def __init__(self, sock: socket.socket, secret: str, *, name: str = ""):
        self.sock = sock
        self.secret = secret
        self.name = name
        self._write_lock = threading.Lock()
        self._buffer = b""
        self.counter = 0
        self.closed = False

    # ------------------------------------------------------------------ plumbing
    def _read_line(self) -> Optional[bytes]:
        while b"\n" not in self._buffer:
            try:
                chunk = self.sock.recv(65536)
            except (OSError, socket.timeout):
                return None
            if not chunk:
                return None
            self._buffer += chunk
            if len(self._buffer) > MAX_FRAME_BYTES:
                log.warning("frame from %s exceeded the size limit — dropping connection",
                            self.name)
                return None
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = rest
        return line

    def send(self, payload: Dict[str, Any]) -> bool:
        if self.closed:
            return False
        with self._write_lock:
            try:
                self.sock.sendall(encode_frame(payload, self.secret))
                return True
            except OSError as exc:
                log.debug("send to %s failed: %s", self.name, exc)
                self.closed = True
                return False

    def recv(self) -> Optional[Dict[str, Any]]:
        line = self._read_line()
        if line is None:
            return None
        try:
            return decode_frame(line, self.secret)
        except ValueError as exc:
            # an unsigned/forged frame is a protocol violation, not a retryable error
            log.warning("rejected frame from %s: %s", self.name, exc)
            return None

    def next_counter(self) -> int:
        self.counter += 1
        return self.counter

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class DeviceServer:
    """Accepts node connections and exposes `send_command()` to the rest of GENIE.

    The server owns no policy: it authenticates the channel and moves frames. Whether a
    capability may run is decided by the device service (PTE) before a command is ever sent.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, *,
                 secret_provider: Optional[Callable[[str], str]] = None,
                 on_hello: Optional[Callable[[str, DeviceManifest, Dict[str, Any]], Dict[str, Any]]] = None,
                 on_connected: Optional[Callable[[str], None]] = None,
                 on_disconnect: Optional[Callable[[str], None]] = None):
        self.host = host
        self.port = port
        self.secret_provider = secret_provider or (lambda device_id: "")
        self.on_hello = on_hello
        self.on_connected = on_connected
        self.on_disconnect = on_disconnect
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._accepting = False
        self._connections: Dict[str, Connection] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._replay = ReplayWindow()
        self._readers: Dict[str, threading.Thread] = {}

    # --------------------------------------------------------------------- lifecycle
    def start(self) -> Dict[str, Any]:
        if self._accepting:
            return {"ok": True, "host": self.host, "port": self.port, "already": True}
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self.host, self.port))
        except OSError as exc:
            return {"ok": False, "error": f"cannot bind {self.host}:{self.port}: {exc}"}
        server.listen(8)
        server.settimeout(1.0)
        self._server = server
        self._accepting = True
        self._thread = threading.Thread(target=self._accept_loop, name="device-accept",
                                        daemon=True)
        self._thread.start()
        log.info("device transport listening on %s:%s", self.host, self.port)
        return {"ok": True, "host": self.host, "port": self.port}

    def stop(self) -> None:
        self._accepting = False
        with self._lock:
            for connection in list(self._connections.values()):
                connection.close()
            self._connections.clear()
            self._pending.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        self._server = None

    # ------------------------------------------------------------------------ accept
    def _accept_loop(self) -> None:
        while self._accepting and self._server is not None:
            try:
                client, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handshake, args=(client, address),
                             name="device-handshake", daemon=True).start()

    def _handshake(self, client: socket.socket, address) -> None:
        """First frame must be a signed `hello`; nothing else is trusted."""
        client.settimeout(15.0)
        connection = Connection(client, secret="", name=str(address))
        line = connection._read_line()
        if line is None:
            connection.close()
            return
        import json
        try:
            frame = json.loads(line.decode("utf-8"))
            payload = frame["payload"]
            signature = frame["sig"]
        except Exception:
            connection.close()
            return

        if payload.get("kind") != "hello":
            connection.close()
            return

        device_id = str(payload.get("device_id", ""))
        secret = self.secret_provider(device_id)
        if not secret:
            # unknown or unpaired: tell the node to pair, then close. No commands are served.
            # Signed with the well-known handshake secret, because no shared secret exists yet.
            from devices.contracts import sign_payload
            reply = {"kind": "pair_required", "device_id": device_id,
                     "reason": "device is not paired"}
            try:
                client.sendall((json.dumps({"payload": reply,
                                            "sig": sign_payload(reply, UNPAIRED_SECRET)})
                               + "\n").encode("utf-8"))
            except OSError:
                pass
            connection.close()
            log.info("device %s asked to pair (from %s)", device_id or "?", address)
            return

        from devices.contracts import verify_signature
        if not verify_signature(payload, secret, signature):
            log.warning("rejected hello from %s: bad signature", device_id or address)
            connection.close()
            return

        connection.secret = secret
        connection.name = device_id
        manifest = DeviceManifest.from_dict(payload.get("manifest") or {})
        if not manifest.device_id:
            manifest.device_id = device_id

        decision: Dict[str, Any] = {"ok": True, "state": "online"}
        if self.on_hello is not None:
            try:
                decision = self.on_hello(device_id, manifest, payload) or decision
            except Exception as exc:
                log.warning("on_hello failed for %s: %s", device_id, exc)
                decision = {"ok": False, "state": "rejected", "reason": str(exc)}

        ack = {"kind": "hello_ack", "device_id": device_id,
               "ok": bool(decision.get("ok", True)),
               "state": decision.get("state", "online"),
               "reason": decision.get("reason", ""),
               "protocol": payload.get("protocol", "")}
        connection.send(ack)
        if not ack["ok"]:
            connection.close()
            return

        with self._lock:
            previous = self._connections.get(device_id)
            if previous is not None:
                previous.close()
            self._connections[device_id] = connection
            self._replay.reset()

        reader = threading.Thread(target=self._read_loop, args=(device_id, connection),
                                  name=f"device-read-{device_id}", daemon=True)
        self._readers[device_id] = reader
        reader.start()
        # Call only after the authenticated connection is addressable and its
        # result reader is live; queued commands sent before this point can race
        # registration, be falsely marked offline, or miss their replies.
        if self.on_connected is not None:
            try:
                self.on_connected(device_id)
            except Exception as exc:
                log.warning("on_connected failed for %s: %s", device_id, exc)
        log.info("device %s online (%s, %d capabilities)", device_id, manifest.type,
                 len(manifest.capabilities))

    def _read_loop(self, device_id: str, connection: Connection) -> None:
        while not connection.closed:
            payload = connection.recv()
            if payload is None:
                break
            kind = payload.get("kind")
            if kind == "result":
                result = ResultEnvelope.from_dict(payload)
                self._resolve(result)
            elif kind == "heartbeat":
                with self._lock:
                    slot = self._pending.setdefault("__heartbeats__", {})
                    slot[device_id] = int(time.time() * 1000)
            elif kind == "cancel":
                self._resolve(ResultEnvelope(command_id=str(payload.get("command_id", "")),
                                             device_id=device_id,
                                             status=CommandStatus.CANCELLED.value,
                                             detail="cancelled by device"))
        self._disconnect(device_id, connection)

    def _disconnect(self, device_id: str, connection: Connection) -> None:
        with self._lock:
            if self._connections.get(device_id) is connection:
                self._connections.pop(device_id, None)
                # fail any in-flight command so the caller is never left waiting
                for command_id, waiter in list(self._pending.items()):
                    if command_id == "__heartbeats__":
                        continue
                    if waiter.get("device_id") == device_id:
                        self._pending.pop(command_id, None)
                        event: threading.Event = waiter["event"]
                        waiter["result"] = ResultEnvelope(
                            command_id=command_id, device_id=device_id,
                            status=CommandStatus.FAILED.value, ok=False, verified=False,
                            detail="device disconnected before reporting a result",
                            error_code="device_offline")
                        event.set()
        log.info("device %s disconnected", device_id)
        if self.on_disconnect is not None:
            try:
                self.on_disconnect(device_id)
            except Exception as exc:
                log.debug("on_disconnect failed: %s", exc)

    def _resolve(self, result: ResultEnvelope) -> None:
        with self._lock:
            waiter = self._pending.get(result.command_id)
        if waiter is None:
            log.debug("late/unknown result for %s", result.command_id)
            return
        if result.terminal():
            with self._lock:
                self._pending.pop(result.command_id, None)
            waiter["result"] = result
            waiter["event"].set()
        else:
            # progress update (accepted/running) — remember it but keep waiting
            waiter["progress"] = result

    # ------------------------------------------------------------------------ sending
    def connected(self, device_id: str) -> bool:
        with self._lock:
            connection = self._connections.get(device_id)
        return bool(connection and not connection.closed)

    def online_devices(self) -> List[str]:
        with self._lock:
            return [d for d, c in self._connections.items() if not c.closed]

    def send_command(self, command: CommandEnvelope, *,
                     timeout_s: float = 30.0) -> ResultEnvelope:
        """Send a command and wait for a terminal result. Never raises."""
        with self._lock:
            connection = self._connections.get(command.device_id)
        if connection is None or connection.closed:
            return ResultEnvelope(command_id=command.command_id, device_id=command.device_id,
                                  status=CommandStatus.FAILED.value, ok=False,
                                  detail=f"device {command.device_id} is not connected",
                                  error_code="device_offline")

        waiter = {"event": threading.Event(), "result": None,
                  "device_id": command.device_id}
        with self._lock:
            self._pending[command.command_id] = waiter

        command.counter = connection.next_counter()
        if not connection.send(command.to_dict()):
            with self._lock:
                self._pending.pop(command.command_id, None)
            return ResultEnvelope(command_id=command.command_id, device_id=command.device_id,
                                  status=CommandStatus.FAILED.value, ok=False,
                                  detail="failed to write to the device channel",
                                  error_code="transport_error")

        if not waiter["event"].wait(max(0.1, float(timeout_s))):
            with self._lock:
                self._pending.pop(command.command_id, None)
            return ResultEnvelope(command_id=command.command_id, device_id=command.device_id,
                                  status=CommandStatus.FAILED.value, ok=False,
                                  detail=f"no result within {timeout_s}s",
                                  error_code="device_timeout")
        return waiter["result"] or ResultEnvelope(
            command_id=command.command_id, device_id=command.device_id,
            status=CommandStatus.FAILED.value, detail="no result", error_code="no_result")

    def cancel(self, device_id: str, command_id: str) -> bool:
        with self._lock:
            connection = self._connections.get(device_id)
        if connection is None:
            return False
        return connection.send({"kind": "cancel", "command_id": command_id,
                                "device_id": device_id})

    def status(self) -> Dict[str, Any]:
        return {"listening": self._accepting, "host": self.host, "port": self.port,
                "connected": self.online_devices()}
