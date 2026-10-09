"""Context compaction (agents/contextcompaction.py) — re-audit 14.5.

Capability donor: **DeerFlow** (durable context protection + compaction).

Long missions explode context: 20 files, 50 tool calls, 10 test attempts. Sending all of it to a
model is wasteful — but naively summarising *everything* is dangerous, because a summary can
silently drop the mission goal, a hard constraint, or an owner decision.

So context is explicitly classified, and the classes are treated differently:

    protected      mission truth — goal, id, hard constraints, owner decisions. NEVER compacted.
    receipt        tool receipts — kept as references (they are durable, see agents/receipts.py)
    artifact       artifact references — kept (the payload lives in the artifact store)
    recent         current working context — kept while within the recent window
    compressible   older history — the ONLY thing compaction may drop or summarise

The invariant, tested: **after compaction, every protected item is still present byte-for-byte.**
If a compaction pass would lose mission truth, that is a bug, not a trade-off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.contextcompaction")

PROTECTED = "protected"
RECEIPT = "receipt"
ARTIFACT = "artifact"
RECENT = "recent"
COMPRESSIBLE = "compressible"

#: ordered by how much we are willing to lose them
PRIORITY = (PROTECTED, RECEIPT, ARTIFACT, RECENT, COMPRESSIBLE)


@dataclass
class ContextItem:
    key: str
    content: str = ""
    kind: str = COMPRESSIBLE
    ref: str = ""
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.kind not in PRIORITY:
            self.kind = COMPRESSIBLE
        if not self.tokens:
            # crude but deterministic: ~4 chars per token
            self.tokens = max(1, len(self.content) // 4)

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "content": self.content, "kind": self.kind,
                "ref": self.ref, "tokens": self.tokens}


@dataclass
class CompactionReport:
    kept: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    summary: str = ""
    protected_preserved: bool = True
    tokens_before: int = 0
    tokens_after: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"kept": self.kept, "dropped": self.dropped, "summary": self.summary,
                "protected_preserved": self.protected_preserved,
                "tokens_before": self.tokens_before, "tokens_after": self.tokens_after}


class ContextCompactor:
    """Owns the working context and compacts only what is safe to compact."""

    def __init__(self, *, keep_recent: int = 8, max_tokens: Optional[int] = None):
        self.keep_recent = int(keep_recent)
        self.max_tokens = max_tokens
        self._items: List[ContextItem] = []

    # ------------------------------------------------------------------- add
    def add(self, key: str, content: str = "", *, kind: str = COMPRESSIBLE,
            ref: str = "") -> ContextItem:
        item = ContextItem(key=key, content=content, kind=kind, ref=ref)
        self._items.append(item)
        return item

    def items(self) -> List[ContextItem]:
        return list(self._items)

    def protected(self) -> List[ContextItem]:
        return [i for i in self._items if i.kind == PROTECTED]

    # --------------------------------------------------------------- compact
    def compact(self, *, budget: Optional[int] = None) -> CompactionReport:
        limit = budget if budget is not None else self.max_tokens
        before = sum(i.tokens for i in self._items)
        protected_before = {i.key: i.content for i in self.protected()}

        kept: List[ContextItem] = []
        dropped: List[str] = []

        # 1. mission truth and durable references always survive
        for item in self._items:
            if item.kind in (PROTECTED, RECEIPT, ARTIFACT):
                kept.append(item)

        # 2. most recent compressible/recent items survive, up to the window
        candidates = [i for i in self._items if i.kind in (RECENT, COMPRESSIBLE)]
        for item in list(reversed(candidates))[: self.keep_recent]:
            kept.append(item)
        windowed = {id(i) for i in kept}
        for item in candidates:
            if id(item) not in windowed:
                dropped.append(item.key)

        summary = self._summarise([i for i in candidates if i.key in dropped])

        # 3. if a hard token budget applies, shed oldest compressible first
        if limit is not None:
            kept = self._enforce_budget(kept, limit, dropped)

        self._items = kept
        after = sum(i.tokens for i in self._items)
        protected_after = {i.key: i.content for i in self.protected()}

        report = CompactionReport(
            kept=[i.key for i in self._items], dropped=dropped, summary=summary,
            protected_preserved=protected_before == protected_after,
            tokens_before=before, tokens_after=after)
        if not report.protected_preserved:
            log.error("compaction lost protected mission truth: %s",
                      set(protected_before) - set(protected_after))
        return report

    # -------------------------------------------------------------- internals
    def _enforce_budget(self, kept: List[ContextItem], limit: int,
                        dropped: List[str]) -> List[ContextItem]:
        order = {PROTECTED: 0, RECEIPT: 1, ARTIFACT: 2, RECENT: 3, COMPRESSIBLE: 4}
        survivors = sorted(kept, key=lambda i: (order[i.kind], -i.tokens))
        out: List[ContextItem] = []
        total = 0
        for item in kept:
            if item.kind == PROTECTED:          # never shed mission truth for space
                out.append(item)
                total += item.tokens
        for item in survivors:
            if item.kind == PROTECTED or any(i is item for i in out):
                continue
            if total + item.tokens <= limit:
                out.append(item)
                total += item.tokens
            elif item.key not in dropped:
                dropped.append(item.key)
        # preserve original ordering for readability
        original = {id(i): n for n, i in enumerate(self._items)}
        return sorted(out, key=lambda i: original.get(id(i), 0))

    @staticmethod
    def _summarise(dropped_items: List[ContextItem]) -> str:
        if not dropped_items:
            return ""
        lines = [f"- {i.key}: {(i.content[:80] + '…') if len(i.content) > 80 else i.content}"
                 for i in dropped_items[:20]]
        return "compacted history:\n" + "\n".join(lines)

    def status(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {k: 0 for k in PRIORITY}
        for item in self._items:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {"items": len(self._items), "tokens": sum(i.tokens for i in self._items),
                "by_kind": counts}
