"""Channel ingress — the OpenClaw gap's transport half.

`ChannelService` normalizes messages from any transport into one inbound shape,
persists them through `SessionStore`, and hands the text to the SAME GENIE
request path the desktop UI and voice already use. A channel is a transport,
never an authority: it cannot grant permissions, choose a model, or skip PTE.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.logging_setup import get_logger

from .sessions import SessionStore

log = get_logger("channels.service")


@dataclass
class InboundMessage:
    """A normalized inbound message from any channel."""

    channel: str
    external_key: str                       # stable identity within the channel
    text: str
    author: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=lambda: int(time.time()))

    def validate(self) -> Optional[str]:
        if not self.channel:
            return "channel is required"
        if not self.external_key:
            return "external_key is required (who is speaking)"
        if not (self.text or "").strip():
            return "text is required"
        return None


class ChannelAdapter(ABC):
    """Transport contract. Implementations move bytes; they decide nothing."""

    name: str = "unnamed"

    @abstractmethod
    def send(self, external_key: str, text: str) -> bool:
        """Deliver a reply to this identity on this channel."""

    def poll(self) -> List[InboundMessage]:
        """Optional pull-based receive. Default: nothing pending."""
        return []


class ChannelService:
    """Registry + ingress for channels, with session continuity."""

    def __init__(self, db=None, sessions: Optional[SessionStore] = None,
                 handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None):
        self.db = db
        self.sessions = sessions or (SessionStore(db) if db else None)
        self._adapters: Dict[str, ChannelAdapter] = {}
        # handler(text, context) -> GENIE response. Injected so a channel never
        # owns orchestration logic.
        self._handler = handler

    # ------------------------------------------------------------ adapters
    def register(self, adapter: ChannelAdapter) -> None:
        if not adapter.name:
            raise ValueError("adapter must have a name")
        self._adapters[adapter.name] = adapter
        log.info("channel registered: %s", adapter.name)

    def adapters(self) -> List[str]:
        return sorted(self._adapters)

    # ------------------------------------------------------------- ingress
    def ingest(self, message: InboundMessage) -> Dict[str, Any]:
        """Normalize, persist, and (optionally) dispatch one inbound message.

        Returns the session it belongs to and whether that session was resumed.
        Unknown channels are rejected rather than silently trusted.
        """
        err = message.validate()
        if err:
            return {"ok": False, "error": err}

        if message.channel not in self._adapters and message.channel not in ("ui", "voice"):
            return {"ok": False,
                    "error": f"unknown channel {message.channel!r}; "
                             f"register an adapter first (known: {self.adapters()})"}

        if self.sessions is None:
            return {"ok": False, "error": "no session store configured"}

        existing = self.sessions.get_by_key(message.channel, message.external_key)
        session = existing or self.sessions.create(
            message.channel, message.external_key,
            title=(message.text or "")[:60])
        resumed = existing is not None

        self.sessions.append(session["session_id"], "user", message.text,
                             {"author": message.author, **message.metadata})

        out: Dict[str, Any] = {"ok": True, "session_id": session["session_id"],
                               "resumed": resumed}

        if self._handler is not None:
            ctx = {"session_id": session["session_id"], "channel": message.channel,
                   "external_key": message.external_key, "author": message.author,
                   "history": self.context(session["session_id"])}
            try:
                result = self._handler(message.text, ctx) or {}
                reply = result.get("reply") or result.get("text") or ""
                if reply:
                    self.reply(session["session_id"], reply)
                out["result"] = result
            except Exception as exc:  # noqa: BLE001
                log.warning("channel handler failed: %s", exc)
                out["ok"] = False
                out["error"] = f"handler failed: {exc}"
        return out

    # -------------------------------------------------------------- egress
    def reply(self, session_id: str, text: str) -> Dict[str, Any]:
        """Persist GENIE's reply and, if possible, deliver it on the channel."""
        if self.sessions is None:
            return {"ok": False, "error": "no session store configured"}
        session = self.sessions.get(session_id)
        if not session:
            return {"ok": False, "error": f"unknown session {session_id}"}
        self.sessions.append(session_id, "genie", text)

        adapter = self._adapters.get(session["channel"])
        delivered = None
        if adapter is not None:
            try:
                delivered = bool(adapter.send(session["external_key"], text))
            except Exception as exc:  # noqa: BLE001
                log.warning("channel %s delivery failed: %s", session["channel"], exc)
                delivered = False
        return {"ok": True, "delivered": delivered, "session_id": session_id}

    # ------------------------------------------------------------ context
    def context(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if self.sessions is None:
            return []
        return self.sessions.history(session_id, limit=limit)

    def poll_all(self) -> List[Dict[str, Any]]:
        """Pull from every registered adapter and ingest what arrived."""
        out = []
        for name, adapter in self._adapters.items():
            try:
                for msg in adapter.poll() or []:
                    out.append(self.ingest(msg))
            except Exception as exc:  # noqa: BLE001
                log.warning("poll failed for channel %s: %s", name, exc)
        return out
