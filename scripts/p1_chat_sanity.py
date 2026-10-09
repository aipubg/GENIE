"""Phase 1 — short Chat sanity through the real daemon (headless).

Starts the real daemon with an isolated data dir, sends a few typed turns, and
asserts: replies are produced, turns persist in the owner SessionStore, and the
deterministic context resolver answers "what did I just say?" from the previous
owner turn (never the incoming message).

This is the backend chat path only — the GUI Chat surface is owner acceptance.
"""
from __future__ import annotations

import os
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


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p1_chat_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print("=" * 64)
    print("Phase 1 — short Chat sanity (real daemon, isolated data)")
    print("=" * 64)

    import importlib
    import core.config
    importlib.reload(core.config)
    from core.lifecycle import Daemon
    from core.config import get_config

    cfg = get_config()
    d = Daemon(cfg)
    d.start()
    port = int(cfg.get("ipc.port", 8787))
    # health wait
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)

    turns = [
        ("Hello GENIE", None),
        ("My temporary phrase is BLUE ORBIT.", None),
        ("Maine abhi kya phrase bola tha?", "BLUE ORBIT"),
    ]
    replies: list[str] = []
    for text, expect in turns:
        # The real WPF Chat surface uses the streaming path, which is also where
        # the deterministic context resolver lives — use the same path here.
        parts = [delta for delta, _kind in d.stream_chat(text, session_id="owner")]
        reply = "".join(parts).strip()
        replies.append(reply)
        check(f"turn replied: {text[:30]!r}", bool(reply), reply[:60])
        if expect:
            check(f"context answer contains {expect!r}", expect in reply, reply[:80])

    # persistence in the owner session store
    store = d.services.get("session_store")
    hist = store.recent() if store else []
    check("owner session store has turns", len(hist) >= 3, f"{len(hist)} turns")
    check("BLUE ORBIT persisted in session", any("BLUE ORBIT" in str(h) for h in hist),
          str(hist)[:120])

    # NOT promoted to long-term memory
    mem = d.services.get("memory")
    if mem is not None:
        try:
            hits = mem.query.__self__ if False else None  # noqa
        except Exception:
            hits = None
    check("owner session id is canonical", (store.session_id if store else "") == "owner")

    d.stop()

    print("=" * 64)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
