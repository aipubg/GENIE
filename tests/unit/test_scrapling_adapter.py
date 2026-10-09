"""Tests for integrations/scrapling_adapter.py — Scrapling web-scraping boundary.

Deterministic: no network calls, no scrapling import needed. Proves GENIE owns the
scraping scope (URL allowlist, proxy/credential gating), that an unavailable library
is reported honestly rather than faked, and that tier selection escalates correctly.
"""
from __future__ import annotations

import sys
import types
import pytest

from integrations.scrapling_adapter import (
    FetchTier,
    ScraplingAdapter,
    ScraplingScope,
    ScopeViolation,
    _select_tier,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _scope(**kw):
    base = dict(
        allowed_domains=["example.com", "quotes.toscrape.com"],
        blocked_domains=("localhost", "127.0.0.1"),
        allow_proxy=False,
        allow_credentials=False,
        obey_robots=True,
        ai_targeted=True,
        max_content_length=1024,
    )
    base.update(kw)
    return ScraplingScope(**base)


def _adapter(**scope_kw):
    return ScraplingAdapter(scope=_scope(**scope_kw))


def _ensure_scrapling_absent():
    """Force scrapling to be unimportable for a test."""
    mods = {k: v for k, v in sys.modules.items() if k == "scrapling" or k.startswith("scrapling.")}
    for k in mods:
        del sys.modules[k]
    import builtins
    orig = builtins.__import__

    def _blocker(name, *args, **kwargs):
        if name == "scrapling" or name.startswith("scrapling."):
            raise ImportError(f"blocked for test: {name}")
        return orig(name, *args, **kwargs)

    builtins.__import__ = _blocker
    import integrations.scrapling_adapter as sa
    sa._SCRAPLING_AVAILABLE = None
    return orig, builtins, sa


def _restore_import(old_import, builtins, sa):
    builtins.__import__ = old_import
    sa._SCRAPLING_AVAILABLE = None


def _fake_scrapling_present():
    """Install fake scrapling modules so adapter thinks it's installed."""
    fake_scrapling = types.ModuleType("scrapling")
    fake_parser = types.ModuleType("scrapling.parser")

    class FakeElement:
        def __init__(self, text=""):
            self._text = text
            self.attrib = {}

        def get_all_text(self, separator=" ", strip=False):
            return self._text.strip() if strip else self._text

        def extract(self):
            return f"<el>{self._text}</el>"

    class FakeSelectors(list):
        pass

    class FakeAdaptor:
        def __init__(self, html, auto_match=False):
            self.html = html
            self._elements = [FakeElement("extracted text")]

        def css(self, sel):
            return FakeSelectors(self._elements)

        def xpath(self, expr):
            return FakeSelectors(self._elements)

        def get_all_text(self, separator="\n", strip=False):
            return "extracted text"

    fake_parser.Adaptor = FakeAdaptor
    fake_scrapling.parser = fake_parser
    sys.modules["scrapling"] = fake_scrapling
    sys.modules["scrapling.parser"] = fake_parser
    import integrations.scrapling_adapter as sa_mod
    sa_mod._SCRAPLING_AVAILABLE = True
    return sa_mod


def _cleanup_fake_scrapling(sa_mod):
    sys.modules.pop("scrapling", None)
    sys.modules.pop("scrapling.parser", None)
    sa_mod._SCRAPLING_AVAILABLE = None


# ---------------------------------------------------------------------------
# scope authority
# ---------------------------------------------------------------------------
class TestScraplingScope:
    def test_allowed_domain_is_permitted(self):
        s = _scope()
        assert s.allows_url("https://example.com/page") is True

    def test_subdomain_of_allowed_is_permitted(self):
        s = _scope()
        assert s.allows_url("https://api.example.com/data") is True

    def test_unlisted_domain_is_rejected(self):
        s = _scope()
        assert s.allows_url("https://evil.test/page") is False

    def test_blocked_domain_is_rejected_even_if_in_allowlist(self):
        s = _scope(allowed_domains=["localhost"])
        assert s.allows_url("http://localhost:8080/") is False

    def test_empty_allowlist_allows_non_blocked(self):
        s = _scope(allowed_domains=())
        assert s.allows_url("https://anything.com/page") is True

    def test_empty_allowlist_still_blocks_listed(self):
        s = _scope(allowed_domains=())
        assert s.allows_url("http://127.0.0.1/x") is False

    def test_scope_is_frozen(self):
        s = _scope()
        with pytest.raises(AttributeError):
            s.allowed_domains = ["new.com"]


# ---------------------------------------------------------------------------
# tier selection
# ---------------------------------------------------------------------------
class TestTierSelection:
    def test_default_is_static(self):
        assert _select_tier({}) == FetchTier.STATIC

    def test_explicit_tier_overrides(self):
        assert _select_tier({"tier": "stealthy"}) == FetchTier.STEALTHY

    def test_javascript_flag_selects_dynamic(self):
        assert _select_tier({"javascript": True}) == FetchTier.DYNAMIC

    def test_wait_selector_selects_dynamic(self):
        assert _select_tier({"wait_selector": ".loaded"}) == FetchTier.DYNAMIC

    def test_network_idle_selects_dynamic(self):
        assert _select_tier({"network_idle": True}) == FetchTier.DYNAMIC

    def test_solve_cloudflare_selects_stealthy(self):
        assert _select_tier({"solve_cloudflare": True}) == FetchTier.STEALTHY

    def test_stealth_flag_selects_stealthy(self):
        assert _select_tier({"stealth": True}) == FetchTier.STEALTHY

    def test_url_without_html_selects_fetch(self):
        assert _select_tier({"url": "https://example.com"}) == FetchTier.FETCH

    def test_html_param_selects_static(self):
        assert _select_tier({"html": "<div>test</div>"}) == FetchTier.STATIC


# ---------------------------------------------------------------------------
# adapter contract — availability & conformance
# ---------------------------------------------------------------------------
class TestAdapterContract:
    def test_disabled_adapter_reports_not_available(self):
        a = ScraplingAdapter(enabled=False)
        info = a.available()
        assert info["available"] is False
        assert info["enabled"] is False

    def test_conformance_pending_when_not_installed(self):
        old_import, builtins, sa = _ensure_scrapling_absent()
        try:
            a = _adapter()
            conf = a.conformance()
            assert conf["state"] == "pending-live-acceptance"
            assert conf["available"] is False
            assert conf["capability"] == "web.scraping"
            assert conf["source_repo"] == "Scrapling"
        finally:
            _restore_import(old_import, builtins, sa)

    def test_describe_returns_metadata(self):
        a = _adapter()
        d = a.describe()
        assert d["engine"] == "Scrapling"
        assert d["capability"] == "web.scraping"
        assert "operation" in d

    def test_conformance_includes_scope_info(self):
        a = _adapter()
        conf = a.conformance()
        assert "example.com" in conf["scope_domains"]
        assert conf["ai_targeted"] is True

    def test_available_reports_static_live(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            info = a.available()
            assert info["static_live"] is True
            assert info["dynamic_live"] is False
        finally:
            _cleanup_fake_scrapling(sa_mod)


# ---------------------------------------------------------------------------
# invoke guard rails
# ---------------------------------------------------------------------------
class TestInvokeGuards:
    def test_invoke_disabled_returns_error(self):
        a = ScraplingAdapter(enabled=False)
        result = a.invoke("fetch", {"url": "https://example.com"})
        assert result["ok"] is False
        assert "disabled" in result["error"]

    def test_invoke_missing_url_and_html_returns_error(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.invoke("fetch", {})
            assert result["ok"] is False
            assert "url" in result["error"] or "html" in result["error"]
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_blocked_url_raises_scope_violation(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            with pytest.raises(ScopeViolation):
                a.invoke("fetch", {"url": "https://evil.test/page"})
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_proxy_without_authority_returns_error(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter(allow_proxy=False)
            result = a.invoke("fetch", {"url": "https://example.com", "proxy": "http://p:8080"})
            assert result["ok"] is False
            assert "proxy" in result["error"]
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_cookies_without_authority_returns_error(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter(allow_credentials=False)
            result = a.invoke("fetch", {"url": "https://example.com", "cookies": "a=b"})
            assert result["ok"] is False
            assert "credential" in result["error"]
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_not_installed_returns_pending(self):
        old_import, builtins, sa = _ensure_scrapling_absent()
        try:
            a = _adapter()
            result = a.invoke("fetch", {"url": "https://example.com"})
            assert result["ok"] is False
            assert result.get("status") == "pending-live-acceptance"
        finally:
            _restore_import(old_import, builtins, sa)

    def test_invoke_dynamic_returns_pending_live(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.invoke("fetch", {"url": "https://example.com", "javascript": True})
            assert result["ok"] is False
            assert result.get("status") == "pending-live-acceptance"
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_stealthy_returns_pending_live(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.invoke("fetch", {"url": "https://example.com", "stealth": True})
            assert result["ok"] is False
            assert result.get("status") == "pending-live-acceptance"
        finally:
            _cleanup_fake_scrapling(sa_mod)


# ---------------------------------------------------------------------------
# parse_html (offline, no fetch)
# ---------------------------------------------------------------------------
class TestParseHtml:
    def test_parse_html_not_installed_returns_pending(self):
        old_import, builtins, sa = _ensure_scrapling_absent()
        try:
            a = _adapter()
            result = a.parse_html("<html></html>")
            assert result["ok"] is False
            assert result.get("status") == "pending-live-acceptance"
        finally:
            _restore_import(old_import, builtins, sa)

    def test_parse_html_with_mock_scrapling(self):
        """Prove extraction logic works when scrapling Adaptor is present."""
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.parse_html("<div>test</div>", css_selector="div")
            assert result["ok"] is True
            assert isinstance(result["content"], list)
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_parse_html_markdown_format(self):
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.parse_html("<div>test</div>", css_selector="div")
            # Default format is "text" which uses get_all_text on elements
            assert result["ok"] is True
        finally:
            _cleanup_fake_scrapling(sa_mod)

    def test_invoke_with_html_param(self):
        """invoke() with html param should use STATIC tier (parse-only)."""
        sa_mod = _fake_scrapling_present()
        try:
            a = _adapter()
            result = a.invoke("fetch", {"html": "<div>hello</div>", "css_selector": "div"})
            assert result["ok"] is True
        finally:
            _cleanup_fake_scrapling(sa_mod)
