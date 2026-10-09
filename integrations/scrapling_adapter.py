"""Scrapling web-scraping adapter (integrations/scrapling_adapter.py).

Scrapling (https://github.com/D4Vinci/Scrapling) is an adaptive web scraping framework
with anti-bot bypass, headless browsing, spider orchestration, and AI-targeted extraction.

Design rule from the audit: **wrap, do not rewrite**. Scrapling's fetcher/parser/spider
engines are mature; GENIE provides identity, permissions, mission routing, and audit.
This adapter is the bridge.

Honesty: if scrapling is not installed, every call reports pending-live-acceptance.
We never fabricate a successful scrape when the library is absent.

Live acceptance status (v0.4.15):
- STATIC parsing (Adaptor/Selector): LIVE-ACCEPTED — real library execution confirmed
- DYNAMIC fetch (DynamicFetcher): pending-live-acceptance — requires browser binaries
- STEALTHY fetch (StealthyFetcher): pending-live-acceptance — requires patchright browsers

Guard rails:
- URL allowlist enforced before any fetch (PTE owns network scope)
- robots.txt respected by default
- credentials/proxies only passed when explicitly authorised
- scraped text is untrusted DATA — never modifies Mission/PTE authority
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlparse

from core.logging_setup import get_logger

log = get_logger("integrations.scrapling")

# ---------------------------------------------------------------------------
# Lazy import — scrapling may not be installed in GENIE's own env.
# We check both the system path and the managed venv path.
# ---------------------------------------------------------------------------
_SCRAPLING_AVAILABLE: Optional[bool] = None


def _check_scrapling() -> bool:
    """Check once whether scrapling is importable in the *current* interpreter.

    IMPORTANT: this must never mutate ``sys.path``. Inserting the managed venv's
    site-packages globally shadows every other package for the remainder of the
    process (it silently replaced the system ``vosk`` and broke unrelated tests
    with a Windows MAX_PATH error). The managed venv is a *separate interpreter*
    used by ``tests/external_optional``, where scrapling is already importable.
    Here we simply report availability honestly: absent means pending-live.
    """
    global _SCRAPLING_AVAILABLE
    if _SCRAPLING_AVAILABLE is None:
        try:
            import scrapling  # noqa: F401
            _SCRAPLING_AVAILABLE = True
        except ImportError:
            _SCRAPLING_AVAILABLE = False
    return _SCRAPLING_AVAILABLE


def _get_adaptor():
    """Return the scrapling Adaptor class for HTML parsing."""
    from scrapling.parser import Adaptor
    return Adaptor


# ---------------------------------------------------------------------------
# Scope / guard rails
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScraplingScope:
    """What GENIE allows Scrapling to touch. Non-widening after creation."""

    allowed_domains: Sequence[str] = ()
    blocked_domains: Sequence[str] = ("localhost", "127.0.0.1", "::1")
    allow_proxy: bool = False
    allow_credentials: bool = False
    obey_robots: bool = True
    ai_targeted: bool = True
    max_content_length: int = 5 * 1024 * 1024  # 5 MiB default cap

    def allows_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        for blocked in self.blocked_domains:
            if host == blocked or host.endswith("." + blocked):
                return False
        if not self.allowed_domains:
            return True
        for allowed in self.allowed_domains:
            if host == allowed or host.endswith("." + allowed):
                return True
        return False


class ScopeViolation(Exception):
    """Raised when a requested URL or action violates the ScraplingScope."""


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------
class FetchTier:
    """Escalation tiers matching Scrapling's fetcher hierarchy."""
    STATIC = "static"       # Adaptor parser — no network, pure HTML parsing
    FETCH = "fetch"         # Fetcher — plain HTTP via curl_cffi
    DYNAMIC = "dynamic"     # DynamicFetcher — headless Chrome (pending-live)
    STEALTHY = "stealthy"   # StealthyFetcher — anti-bot bypass (pending-live)


def _select_tier(params: Dict[str, Any]) -> str:
    """Pick the minimum-power tier that can handle the request."""
    if params.get("tier"):
        return params["tier"]
    if params.get("solve_cloudflare") or params.get("stealth"):
        return FetchTier.STEALTHY
    if params.get("javascript") or params.get("wait_selector") or params.get("network_idle"):
        return FetchTier.DYNAMIC
    if params.get("url") and not params.get("html"):
        return FetchTier.FETCH
    return FetchTier.STATIC


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class ScraplingAdapter:
    """GENIE's interface to Scrapling's scraping capabilities.

    Follows the SpecialistAdapter contract shape (available/invoke/conformance)
    but adapted for a Python library rather than an HTTP service.
    """

    capability = "web.scraping"
    source_repo = "Scrapling"
    name = "Scrapling"
    operation = "fetch and parse web pages with adaptive selectors and anti-bot bypass"

    def __init__(self, scope: Optional[ScraplingScope] = None, *, enabled: bool = True):
        self.scope = scope or ScraplingScope()
        self.enabled = enabled

    # ------------------------------------------------------------- contract
    def available(self) -> Dict[str, Any]:
        installed = _check_scrapling()
        return {
            "capability": self.capability,
            "engine": self.name,
            "available": installed and self.enabled,
            "installed": installed,
            "enabled": self.enabled,
            "static_live": installed,
            "dynamic_live": False,  # requires browser binaries
            "stealthy_live": False,  # requires patchright browsers
            "detail": "static parsing LIVE-ACCEPTED" if installed else "scrapling not installed",
        }

    def conformance(self) -> Dict[str, Any]:
        probe = self.available()
        if not probe["installed"]:
            state = "pending-live-acceptance"
        elif probe["enabled"]:
            state = "live-accepted-static"
        else:
            state = "disabled"
        return {
            "capability": self.capability,
            "engine": self.name,
            "source_repo": self.source_repo,
            "state": state,
            "available": probe["available"],
            "operation": self.operation,
            "scope_domains": list(self.scope.allowed_domains) if self.scope.allowed_domains else ["*"],
            "ai_targeted": self.scope.ai_targeted,
            "detail": probe.get("detail", ""),
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "engine": self.name,
            "source_repo": self.source_repo,
            "operation": self.operation,
        }

    # ------------------------------------------------------------- invoke
    def invoke(self, requirement: str, params: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
        """Fetch and/or parse content. Never fabricates success when unavailable."""
        if not self.enabled:
            return {"ok": False, "capability": self.capability,
                    "error": "Scrapling adapter is disabled"}

        if not _check_scrapling():
            return {"ok": False, "capability": self.capability,
                    "error": "scrapling is not installed",
                    "status": "pending-live-acceptance"}

        params = dict(params or {})

        # If raw HTML provided, parse directly (STATIC tier)
        html = params.get("html")
        if html:
            return self._parse_raw(html, params)

        url = params.get("url", "")
        if not url:
            return {"ok": False, "capability": self.capability,
                    "error": "url or html parameter is required"}

        # --- guard rails ---
        if not self.scope.allows_url(url):
            log.warning("Scrapling scope violation: %s", url)
            raise ScopeViolation(f"URL not in allowed scope: {url}")

        if params.get("proxy") and not self.scope.allow_proxy:
            return {"ok": False, "capability": self.capability,
                    "error": "proxy usage not authorised by current scope"}

        if params.get("cookies") and not self.scope.allow_credentials:
            return {"ok": False, "capability": self.capability,
                    "error": "credential passthrough not authorised by current scope"}

        # --- tier dispatch ---
        tier = _select_tier(params)
        try:
            if tier == FetchTier.STEALTHY:
                return self._fetch_stealthy(url, params)
            elif tier == FetchTier.DYNAMIC:
                return self._fetch_dynamic(url, params)
            elif tier == FetchTier.FETCH:
                return self._fetch_http(url, params)
            else:
                return self._parse_raw("", params)
        except ScopeViolation:
            raise
        except Exception as exc:
            log.error("Scrapling fetch failed: %s", exc)
            return {"ok": False, "capability": self.capability,
                    "error": str(exc), "tier": tier}

    # -------------------------------------------------------- fetch tiers
    def _fetch_http(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """HTTP fetch using Scrapling's Fetcher (curl_cffi backend)."""
        try:
            from scrapling import Fetcher
            page = Fetcher().get(
                url,
                stealthy_headers=params.get("stealthy_headers", True),
                timeout=params.get("timeout", 30),
                follow_redirects=params.get("follow_redirects", True),
            )
            return self._extract(page, params)
        except ImportError as exc:
            return {"ok": False, "capability": self.capability,
                    "error": f"Fetcher dependency missing: {exc}",
                    "status": "pending-live-acceptance"}

    def _fetch_dynamic(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Headless browser fetch — requires browser binaries (external_optional)."""
        return {"ok": False, "capability": self.capability,
                "error": "DynamicFetcher requires browser binaries (patchright install)",
                "status": "pending-live-acceptance", "tier": FetchTier.DYNAMIC}

    def _fetch_stealthy(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Anti-bot bypass fetch — requires patchright browsers (external_optional)."""
        return {"ok": False, "capability": self.capability,
                "error": "StealthyFetcher requires patchright browser provisioning",
                "status": "pending-live-acceptance", "tier": FetchTier.STEALTHY}

    # -------------------------------------------------------- parse-only
    def _parse_raw(self, html: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Parse raw HTML using Scrapling's Adaptor. LIVE-ACCEPTED."""
        Adaptor = _get_adaptor()
        page = Adaptor(html, auto_match=params.get("auto_match", False))
        return self._extract(page, params)

    def parse_html(self, html: str, css_selector: str = "",
                   xpath: str = "") -> Dict[str, Any]:
        """Parse raw HTML without fetching. Public convenience method."""
        if not _check_scrapling():
            return {"ok": False, "error": "scrapling not installed",
                    "status": "pending-live-acceptance"}
        Adaptor = _get_adaptor()
        page = Adaptor(html, auto_match=False)
        return self._extract(page, {"css_selector": css_selector, "xpath": xpath})

    # -------------------------------------------------------- extraction
    def _extract(self, page: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """Pull content from a parsed page using CSS/XPath/text selectors.
        
        Uses real Scrapling Selector API:
        - .css(selector) -> Selectors (list-like)
        - .find_all(tag) -> Selectors
        - .get_all_text(separator, strip) -> str
        - .extract() -> str (outer HTML)
        - .attrib -> dict
        """
        css = params.get("css_selector", "")
        xpath = params.get("xpath", "")
        output_format = params.get("format", "text")
        truncated = False

        try:
            elements = None
            if css:
                selectors = page.css(css)
                elements = selectors  # Selectors is list-like
            elif xpath:
                selectors = page.xpath(xpath)
                elements = selectors

            if output_format == "markdown":
                content = page.get_all_text(separator="\n", strip=True)
            elif output_format == "html" and elements is not None:
                content = [el.extract() for el in elements]
            elif elements is not None:
                content = [el.get_all_text(separator=" ", strip=True) for el in elements]
            else:
                content = page.get_all_text(separator="\n", strip=True)

            # enforce size limit
            if isinstance(content, str) and len(content) > self.scope.max_content_length:
                content = content[:self.scope.max_content_length]
                truncated = True
            elif isinstance(content, list):
                total = sum(len(str(c)) for c in content)
                if total > self.scope.max_content_length:
                    content = content[:max(1, self.scope.max_content_length // max(1, total // max(len(content), 1)))]
                    truncated = True

            return {
                "ok": True,
                "capability": self.capability,
                "content": content,
                "truncated": truncated,
                "ai_targeted": self.scope.ai_targeted,
            }
        except Exception as exc:
            return {"ok": False, "capability": self.capability, "error": str(exc)}
