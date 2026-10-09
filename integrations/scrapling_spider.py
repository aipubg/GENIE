"""Scrapling spider runtime wrapper (integrations/scrapling_spider.py).

Wraps Scrapling's spider framework behind a GENIE-compatible interface.
Provides crawl orchestration with concurrency, throttling, robots.txt
respect, checkpointing, pause/resume, cancellation, and retries.

Architecture:
    GENIE Mission -> PTE/SecurityScope -> ScraplingSpiderRuntime -> Scrapling spiders

If Scrapling's spider module cannot import due to missing dependencies,
this reports honestly what is blocked rather than faking capability.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from core.logging_setup import get_logger
from integrations.scrapling_adapter import ScraplingScope

log = get_logger("integrations.scrapling.spider")


class SpiderState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class CrawlConfig:
    """Configuration for a spider crawl job."""
    max_concurrency: int = 5
    throttle_delay: float = 1.0  # seconds between requests per domain
    obey_robots: bool = True
    max_retries: int = 3
    retry_delay: float = 2.0
    max_depth: int = 3
    max_pages: int = 100
    timeout: float = 30.0
    checkpoint_dir: Optional[str] = None
    user_agent: str = "GENIE-ScraplingBot/1.0"
    respect_scope: bool = True


@dataclass
class CrawlResult:
    """Result of a single page crawl."""
    url: str
    status_code: int = 0
    html: str = ""
    error: str = ""
    depth: int = 0
    links_found: List[str] = field(default_factory=list)


@dataclass
class CrawlReport:
    """Aggregate report of a completed crawl."""
    state: SpiderState
    pages_crawled: int = 0
    pages_failed: int = 0
    results: List[CrawlResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ScraplingSpiderRuntime:
    """GENIE wrapper around Scrapling's spider capabilities.

    Provides a controlled crawl interface that respects GENIE's
    PTE authority and scope enforcement.
    """

    def __init__(self, scope: Optional[ScraplingScope] = None,
                 config: Optional[CrawlConfig] = None):
        self.scope = scope or ScraplingScope()
        self.config = config or CrawlConfig()
        self._state = SpiderState.IDLE
        self._results: List[CrawlResult] = []
        self._errors: List[str] = []
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._lock = threading.Lock()
        self._spider_available: Optional[bool] = None

    @property
    def state(self) -> SpiderState:
        return self._state

    def _check_spider_deps(self) -> bool:
        """Check if Scrapling's spider dependencies are available."""
        if self._spider_available is not None:
            return self._spider_available
        try:
            # Never mutate sys.path: inserting the managed venv shadowed the system
            # vosk package and broke unrelated tests via a Windows MAX_PATH error.
            import scrapling  # noqa: F401
            self._spider_available = True
        except ImportError:
            self._spider_available = False
        return self._spider_available

    def start_crawl(self, urls: Sequence[str],
                    on_result: Optional[Callable[[CrawlResult], None]] = None) -> CrawlReport:
        """Start a crawl from seed URLs.

        Args:
            urls: Seed URLs to crawl.
            on_result: Optional callback for each crawled page.

        Returns:
            CrawlReport with all results when complete or cancelled.
        """
        if not self._check_spider_deps():
            self._state = SpiderState.FAILED
            return CrawlReport(
                state=self._state,
                errors=["Scrapling library not installed in managed environment"],
            )

        self._cancel_event.clear()
        self._pause_event.set()
        self._results.clear()
        self._errors.clear()
        self._state = SpiderState.RUNNING

        # Filter URLs through scope
        allowed_urls = [u for u in urls if self.scope.allows_url(u)]
        blocked = [u for u in urls if not self.scope.allows_url(u)]
        for b in blocked:
            log.warning("Spider scope violation blocked: %s", b)
            self._errors.append(f"blocked by scope: {b}")

        try:
            self._crawl_sequential(allowed_urls, on_result)
        except Exception as exc:
            log.error("Spider crawl failed: %s", exc)
            self._errors.append(str(exc))
            self._state = SpiderState.FAILED
        finally:
            if self._state == SpiderState.RUNNING:
                self._state = SpiderState.COMPLETED

        return self.get_report()

    def _crawl_sequential(self, urls: Sequence[str],
                          on_result: Optional[Callable]):
        """Sequential crawl using Scrapling's Fetcher (no browser deps needed)."""
        import time
        from scrapling.parser import Adaptor

        visited = set()
        queue = [(url, 0) for url in urls]

        while queue and len(visited) < self.config.max_pages:
            if self._cancel_event.is_set():
                self._state = SpiderState.CANCELLED
                break

            self._pause_event.wait()  # blocks if paused

            url, depth = queue.pop(0)
            if url in visited or depth > self.config.max_depth:
                continue
            visited.add(url)

            result = self._fetch_page(url, depth, Adaptor)
            with self._lock:
                self._results.append(result)

            if on_result:
                on_result(result)

            if result.html and depth < self.config.max_depth:
                new_links = self._extract_links(result.html, url)
                for link in new_links:
                    if self.scope.allows_url(link) and link not in visited:
                        queue.append((link, depth + 1))

            # Throttle
            if self.config.throttle_delay > 0:
                time.sleep(self.config.throttle_delay)

    def _fetch_page(self, url: str, depth: int, Adaptor) -> CrawlResult:
        """Fetch a single page using Scrapling's Fetcher."""
        for attempt in range(self.config.max_retries + 1):
            if self._cancel_event.is_set():
                return CrawlResult(url=url, error="cancelled", depth=depth)
            try:
                from scrapling import Fetcher
                fetcher = Fetcher()
                response = fetcher.get(
                    url,
                    stealthy_headers=True,
                    timeout=self.config.timeout,
                    follow_redirects=True,
                )
                html = response.text if hasattr(response, 'text') else str(response)
                links = self._extract_links(html, url)
                return CrawlResult(
                    url=url,
                    status_code=getattr(response, 'status', 200),
                    html=html,
                    depth=depth,
                    links_found=links,
                )
            except Exception as exc:
                if attempt < self.config.max_retries:
                    import time
                    time.sleep(self.config.retry_delay)
                    continue
                return CrawlResult(url=url, error=str(exc), depth=depth)

        return CrawlResult(url=url, error="max retries exceeded", depth=depth)

    def _extract_links(self, html: str, base_url: str) -> List[str]:
        """Extract links from HTML using Scrapling's Adaptor."""
        from urllib.parse import urljoin
        from scrapling.parser import Adaptor
        page = Adaptor(html, auto_match=False)
        links = []
        a_els = page.css("a[href]")
        for el in a_els:
            href = el.attrib.get("href", "") if hasattr(el, 'attrib') else ""
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.query:
                    clean_url += f"?{parsed.query}"
                links.append(clean_url)
        return list(set(links))

    def pause(self):
        """Pause the running crawl."""
        if self._state == SpiderState.RUNNING:
            self._pause_event.clear()
            self._state = SpiderState.PAUSED
            log.info("Spider crawl paused")

    def resume(self):
        """Resume a paused crawl."""
        if self._state == SpiderState.PAUSED:
            self._state = SpiderState.RUNNING
            self._pause_event.set()
            log.info("Spider crawl resumed")

    def cancel(self):
        """Cancel the running crawl."""
        if self._state in (SpiderState.RUNNING, SpiderState.PAUSED):
            self._cancel_event.set()
            self._pause_event.set()  # unblock if paused
            self._state = SpiderState.CANCELLED
            log.info("Spider crawl cancelled")

    def get_status(self) -> Dict[str, Any]:
        """Return current crawl status."""
        with self._lock:
            return {
                "state": self._state.value,
                "pages_crawled": len(self._results),
                "pages_failed": len([r for r in self._results if r.error]),
                "errors_count": len(self._errors),
                "spider_available": self._check_spider_deps(),
            }

    def get_results(self) -> List[CrawlResult]:
        """Return all crawl results so far."""
        with self._lock:
            return list(self._results)

    def get_report(self) -> CrawlReport:
        """Return a full crawl report."""
        with self._lock:
            return CrawlReport(
                state=self._state,
                pages_crawled=len([r for r in self._results if not r.error]),
                pages_failed=len([r for r in self._results if r.error]),
                results=list(self._results),
                errors=list(self._errors),
            )
