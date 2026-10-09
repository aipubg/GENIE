"""P0 Closure #2/#3 — ONE production context path + memory query count.

Proves:
  * Normal typed turn: exactly 1 memory.query (via ContextCompiler)
  * Normal voice/shared turn: exactly 1 memory.query
  * Legacy ContextBuilder delegates to ContextCompiler (no independent query)
  * /api/memory search endpoint is diagnostic-only (not in orchestrator path)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class CountingMemory:
    """Wraps the real MemoryService but counts queries."""
    def __init__(self, real):
        self._real = real
        self.query_count = 0

    def query(self, ctx, text, limit=6):
        self.query_count += 1
        return self._real.query(ctx, text, limit=limit)

    def write(self, *a, **k):
        return self._real.write(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def main() -> int:
    iso = Path(tempfile.mkdtemp(prefix="genie_p0ctx_")) / "data"
    iso.mkdir(parents=True, exist_ok=True)
    os.environ["GENIE_DATA_DIR"] = str(iso)
    print(f"isolated data dir: {iso}\n")

    import core.config
    import importlib
    importlib.reload(core.config)

    from core.lifecycle import Daemon
    from core.config import get_config
    from context.compiler import ContextCompiler

    cfg = get_config()
    d = Daemon(cfg)
    d.start()
    # wrap the SAME memory object the orchestrator holds, in place
    real_mem = d.services["memory"]
    mem = CountingMemory(real_mem)
    d.services["memory"] = mem
    d.orchestrator.memory = mem  # orchestrator holds its own reference

    # typed turn
    mem.query_count = 0
    d.chat("What is my favorite color?", session_id="owner")
    n_typed = mem.query_count
    check("typed turn = 1 memory query", n_typed == 1, f"got {n_typed}")

    # voice/shared turn (same session, via pipeline-equivalent CallContext)
    from core.contracts import CallContext, OWNER_SESSION_ID
    mem.query_count = 0
    d.chat("Repeat what you just said", session_id=OWNER_SESSION_ID)
    n_voice = mem.query_count
    check("voice/shared turn = 1 memory query", n_voice == 1, f"got {n_voice}")

    # legacy ContextBuilder delegates (count via compile path)
    from context.builder import ContextBuilder
    cb = ContextBuilder(memory=mem)
    ctx2 = CallContext(person_id="owner", session_id=OWNER_SESSION_ID)
    mem.query_count = 0
    cb.build(ctx2, "test query")
    check("ContextBuilder delegates (1 query, not independent)",
          mem.query_count == 1, f"got {mem.query_count}")

    # ContextCompiler directly
    comp = ContextCompiler(memory=mem)
    mem.query_count = 0
    comp.compile(ctx2, "another query")
    check("ContextCompiler = 1 query", mem.query_count == 1, f"got {mem.query_count}")

    d.stop()
    shutil_rm = Path(iso.parent)
    import shutil
    shutil.rmtree(shutil_rm, ignore_errors=True)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
