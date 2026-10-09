"""Authoritative browser target selection (browser/targets.py).

Why this exists
---------------
GENIE used to pick the page to automate with `pages[0]`. Chrome target ordering
is NOT an active-tab guarantee, so after tabs were closed GENIE silently ended up
driving a hidden/background page. That surfaced as "download did not start",
because Chrome will not begin a download from a hidden document. The same defect
can cause a wrong click, a wrong form, extraction from a stale page, a navigation
mismatch, an upload failure or a dialog mismatch — it is a page-selection bug,
not a download bug.

`BrowserTargetResolver` replaces every index-based choice with an evidence-based
one. It is the single place that decides which page GENIE acts on.

Selection evidence
------------------
    * target type == page
    * target still attached / alive
    * URL matches expectation where one is known
    * document.readyState
    * document.visibilityState
    * GENIE ownership (never hijack an unrelated user tab)
    * the target GENIE intentionally opened/navigated, while it stays valid

Fresh-target creation is RECOVERY, never normal behaviour.

All CDP access is injected, so this is fully testable without Chrome.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

# A page is only "healthy enough" in these readyStates.
_OK_READY = ("complete", "interactive", "loading", "")


class TargetInfo(Dict[str, Any]):
    """A CDP target dict. Kept as a dict subclass for compatibility."""


class BrowserTargetResolver:
    """Chooses the page GENIE should act on. Never by array position alone."""

    def __init__(self, port: int = 0, *,
                 list_targets: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 probe: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
                 create: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                 owned: Optional[Set[str]] = None):
        self.port = port
        self._list = list_targets or (lambda: [])
        self._probe = probe or (lambda t: {})
        self._create = create
        self.owned: Set[str] = set(owned or ())
        self.active_id: str = ""
        self.last_reason: str = ""

    # ------------------------------------------------------------ ownership
    def mark_owned(self, target_id: str) -> None:
        if target_id:
            self.owned.add(target_id)

    def set_active(self, target_id: str) -> None:
        """Record the target GENIE intentionally opened or navigated."""
        self.active_id = target_id or ""
        self.mark_owned(target_id)

    def forget(self, target_id: str) -> None:
        """A target disappeared — drop it and stop preferring it."""
        self.owned.discard(target_id)
        if self.active_id == target_id:
            self.active_id = ""

    # ------------------------------------------------------------ inspection
    def pages(self) -> List[Dict[str, Any]]:
        try:
            raw = self._list() or []
        except Exception:  # noqa: BLE001
            return []
        return [t for t in raw
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]

    def probe(self, target: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._probe(target) or {}
        except Exception:  # noqa: BLE001
            return {}

    def is_alive(self, target: Dict[str, Any]) -> bool:
        state = self.probe(target)
        return bool(state.get("alive", True))

    def is_visible(self, target: Dict[str, Any]) -> bool:
        return self.probe(target).get("visibilityState") != "hidden"

    def matches_url(self, target: Dict[str, Any], expected: str) -> bool:
        if not expected:
            return True
        state = self.probe(target)
        url = (target.get("url") or state.get("url") or "")
        return expected in url or url in expected

    def is_usable(self, target: Dict[str, Any], expected_url: str = "",
                  *, need_visible: bool = True) -> bool:
        state = self.probe(target)
        if not state.get("alive", True):
            return False
        if need_visible and state.get("visibilityState") == "hidden":
            return False
        ready = state.get("readyState") or ""
        if ready and ready not in _OK_READY:
            return False
        if not self.matches_url(target, expected_url):
            return False
        return True

    # ------------------------------------------------------------- resolution
    def resolve(self, expected_url: str = "", *, need_visible: bool = True,
                allow_create: bool = False) -> Optional[Dict[str, Any]]:
        """Return the best target to act on, or None.

        Order (all evidence-based):
          1. the tracked target, while it is owned and usable;
          2. another GENIE-OWNED target, preferring a visible one, then one whose
             URL matches, then one that is merely alive;
          3. recovery: create a fresh target — only when explicitly allowed.

        An unrelated user tab is never hijacked: only ids in `self.owned` are
        considered before recovery.
        """
        pages = self.pages()
        self.last_reason = ""

        # 1. tracked target stays preferred while valid (preserve identity)
        for t in pages:
            if t.get("id") and t.get("id") == self.active_id:
                if self.is_usable(t, expected_url, need_visible=need_visible):
                    self.last_reason = "tracked target still usable"
                    return t
                self.last_reason = "tracked target unusable"
                break

        # 2. other GENIE-owned targets, ranked by evidence
        owned = [t for t in pages if t.get("id") in self.owned]
        if owned:
            def rank(t: Dict[str, Any]) -> tuple:
                url = (t.get("url") or "")
                # A working page beats a placeholder: when no expected URL is
                # given, an about:blank tab must never outrank the page GENIE
                # actually navigated. Without this, a fresh process resolved to
                # a stale about:blank target and every later DOM query returned
                # 0 elements against the wrong page.
                return (
                    0 if url.startswith(("http://", "https://", "file://")) else 1,
                    0 if (not need_visible or self.is_visible(t)) else 1,
                    0 if self.matches_url(t, expected_url) else 1,
                    0 if (self.probe(t).get("readyState") == "complete") else 1,
                )
            for t in sorted(owned, key=rank):
                if self.is_usable(t, expected_url, need_visible=need_visible):
                    self.last_reason = "owned target selected by evidence"
                    self.active_id = t.get("id", "")
                    return t
            # 2b. owned but reported hidden (its window is merely occluded or
            #     minimised): it is still GENIE's own page, so drive it instead of
            #     abandoning it for a fresh about:blank tab. The caller brings it
            #     to the front when visibility actually matters.
            if need_visible:
                for t in sorted(owned, key=rank):
                    if self.is_usable(t, expected_url, need_visible=False):
                        self.last_reason = "owned target selected (hidden but owned)"
                        self.active_id = t.get("id", "")
                        return t

        # 3. recovery — fresh target, only when the caller allows it
        if allow_create and self._create is not None:
            try:
                created = self._create(expected_url)
            except Exception:  # noqa: BLE001
                created = None
            if created and created.get("id"):
                self.mark_owned(created["id"])
                self.active_id = created["id"]
                self.last_reason = "recovery: created fresh target"
                return created
            self.last_reason = "recovery attempted but no target created"
            return None

        self.last_reason = ("no owned usable target"
                            + (" (creation not allowed)" if not allow_create else ""))
        return None
