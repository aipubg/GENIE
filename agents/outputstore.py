"""Oversized tool-output handling (agents/outputstore.py) — re-audit 14.5.

Donors: **DeerFlow** (tool-output elision) and **Strix** (`tools/output_store.py`).

Not a security feature — a *cost and context* feature that happens to be generic. A 50k-character
pytest log or a 500k-character page extraction must not be carried in model context for the next
thirty turns. The full output stays retrievable; only a bounded, useful view goes to the model.

    small result  -> straight to the model
    large result  -> persisted to the artifact store; the model gets
                     head + tail + a reference + the true byte count

Head and tail are preserved deliberately: failures and summaries usually live at the *end* of
output (a traceback, an assertion summary), while context lives at the start. Truncating to head
alone is how you hide the error.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from core.logging_setup import get_logger

log = get_logger("agents.outputstore")


class ToolOutputStore:
    """Spill oversized tool output to disk and hand back a bounded view."""

    def __init__(self, root: Optional[Path | str] = None, *,
                 threshold: int = 4000, head: int = 1500, tail: int = 800):
        self.root = Path(root) if root else None
        self.threshold = int(threshold)
        self.head = int(head)
        self.tail = int(tail)
        self._inline: Dict[str, str] = {}   # used when no filesystem root is configured

    # ----------------------------------------------------------------- store
    def store(self, content: str, *, key: str = "") -> str:
        """Persist full output and return a reference."""
        ref = key or f"out-{uuid.uuid4().hex[:12]}"
        if self.root is None:
            self._inline[ref] = content
            return ref
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{ref}.txt"
        target.write_text(content, encoding="utf-8")
        return ref

    def fetch(self, ref: str) -> Optional[str]:
        """Retrieve the full output by reference."""
        if self.root is None:
            return self._inline.get(ref)
        target = self.root / f"{ref}.txt"
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return self._inline.get(ref)

    # ----------------------------------------------------------------- elide
    def maybe_elide(self, content: str, *, key: str = "") -> Dict[str, Any]:
        """Return a model-safe view of ``content``, spilling it if oversized."""
        size = len(content or "")
        if size <= self.threshold:
            return {"content": content or "", "elided": False, "ref": "",
                    "bytes": size, "shown_bytes": size}

        ref = self.store(content or "", key=key)
        view = self._bounded_view(content or "")
        return {
            "content": view["text"],
            "elided": True,
            "ref": ref,
            "bytes": size,
            "shown_bytes": view["shown"],
            "omitted_bytes": size - view["shown"],
            "truncated_head": view["head_chars"],
            "truncated_tail": view["tail_chars"],
            "sha256": hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16],
            "note": (f"output elided: showing {view['shown']} of {size} chars; "
                     f"full output available via ref '{ref}'"),
        }

    def _bounded_view(self, content: str) -> Dict[str, Any]:
        if len(content) <= self.head + self.tail + 64:
            return {"text": content, "shown": len(content),
                    "head_chars": len(content), "tail_chars": 0}
        head_part = content[: self.head]
        tail_part = content[-self.tail:]
        omitted = len(content) - len(head_part) - len(tail_part)
        text = (f"{head_part}\n\n…[{omitted} chars omitted — full output stored]…\n\n{tail_part}")
        return {"text": text, "shown": len(text),
                "head_chars": len(head_part), "tail_chars": len(tail_part)}

    # ---------------------------------------------------------------- report
    def status(self) -> Dict[str, Any]:
        stored = 0
        if self.root is not None and self.root.is_dir():
            stored = len(list(self.root.glob("*.txt")))
        return {"root": str(self.root) if self.root else "in-memory",
                "stored": stored + len(self._inline),
                "threshold": self.threshold, "head": self.head, "tail": self.tail}
