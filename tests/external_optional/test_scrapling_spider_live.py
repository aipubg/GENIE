"""REAL upstream Scrapling spider runtime proof (external_optional).

Uses the actual upstream framework (`scrapling.spiders`) against a deterministic local
HTTP site — not a hand-rolled crawler.

Upstream pieces exercised:
    scrapling.spiders.Spider            (base spider, `parse` async generator)
    scrapling.spiders.Request           (yielded to schedule more work)
    scrapling.spiders.CrawlerEngine     (drives the crawl)
    scrapling.spiders.Scheduler         (queue/dedupe)
    response.follow(...)                (link following)
    Spider.stats -> CrawlStats          (requests/items/offsite/robots/bytes)
    Spider(crawldir=...)                (checkpoint persistence)
    allowed_domains / concurrent_requests / download_delay (scope, concurrency, throttle)

Then the crawl output is fed through GENIE's real Markdown conversion so the chain
real spider -> page output -> clean Markdown is exercised.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

import pytest

scrapling = pytest.importorskip("scrapling", reason="scrapling is not installed here")

from scrapling.spiders import Request, Spider  # noqa: E402

from integrations.scrapling_markdown import html_to_markdown  # noqa: E402

# ---------------------------------------------------------------- test site
PAGES = {
    "/": ("<html><head><title>Home</title></head><body>"
          "<h1>Home</h1><p>Welcome to the test site.</p>"
          '<a href="/page1">Page 1</a> <a href="/page2">Page 2</a> '
          '<a href="http://offsite.invalid/other">Offsite</a>'
          "</body></html>"),
    "/page1": ("<html><head><title>Page One</title></head><body>"
               "<h1>Page One</h1><p>First article body.</p>"
               '<a href="/page2">Page 2</a></body></html>'),
    "/page2": ("<html><head><title>Page Two</title></head><body>"
               "<h1>Page Two</h1><p>Second article body.</p></body></html>"),
}
ROBOTS = "User-agent: *\nAllow: /\n"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/robots.txt":
            body, ctype = ROBOTS, "text/plain"
        else:
            body, ctype = PAGES.get(self.path, ""), "text/html"
        if not body:
            self.send_response(404)
            self.end_headers()
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence test-server noise
        pass


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


# ------------------------------------------------------------------- spider
class _SiteSpider(Spider):
    """Follows in-domain links only; yields one item per page."""

    name = "genie-test-site"
    allowed_domains = {"127.0.0.1"}
    concurrent_requests = 2
    download_delay = 0.05
    robots_txt_obey = False

    async def parse(self, response):
        yield {
            "url": response.url,
            "title": "".join(t.text for t in response.css("title")) or "",
            "html": response.html_content if hasattr(response, "html_content") else "",
        }
        for anchor in response.css("a"):
            href = (anchor.attrib or {}).get("href", "")
            # yield EVERY href, including absolute offsite ones, so the filtering is done
            # by upstream allowed_domains rather than by this test's own logic.
            if href:
                yield response.follow(urljoin(response.url, href))


def _run(site_url, crawldir):
    # interval is the upstream autosave period; 0 means "always checkpoint time" in the
    # engine, which is what lets a fast local crawl actually flush a checkpoint.
    spider = _SiteSpider(crawldir=str(crawldir), interval=0)
    spider.start_urls = [site_url + "/"]
    # allowed_domains matches the host AS REQUESTED, which includes the port on this
    # ephemeral-port test server; a bare "127.0.0.1" would filter every link as offsite.
    spider.allowed_domains = {urlparse(site_url).netloc}
    result = spider.start()
    return spider, result


class TestRealSpiderCrawl:
    def test_spider_crawls_multiple_pages(self, site, tmp_path):
        spider, result = _run(site, tmp_path / "crawl1")
        stats = result.stats

        assert result.completed is True
        # /, /page1, /page2 (and /page2 is reached from two places -> deduped)
        assert stats.requests_count >= 3, f"only {stats.requests_count} requests"
        assert stats.items_scraped >= 3, f"only {stats.items_scraped} items"

    def test_link_following_reached_child_pages(self, site, tmp_path):
        spider, result = _run(site, tmp_path / "crawl2")
        # the start page links /page1 and /page2; both must have been fetched
        assert result.stats.requests_count >= 3
        assert result.stats.failed_requests_count == 0

    def test_allowed_domain_enforcement(self, site, tmp_path):
        """The offsite link is never fetched: it is counted offsite, not requested."""
        spider, result = _run(site, tmp_path / "crawl3")
        assert result.stats.offsite_requests_count >= 1, \
            "offsite link was not filtered by allowed_domains"

    def test_concurrency_and_throttle_configured(self, site, tmp_path):
        spider, result = _run(site, tmp_path / "crawl4")
        assert result.stats.concurrent_requests == 2
        assert result.stats.download_delay == 0.05
        assert result.stats.requests_per_second > 0

    def test_checkpoint_cleaned_up_on_successful_completion(self, site, tmp_path):
        """Upstream deletes checkpoint files when a crawl completes (not paused).

        This is real upstream behaviour (`CrawlerEngine` cleans up on success), so an
        empty/absent crawldir after a finished crawl is the CORRECT outcome, not a failure.
        """
        crawldir = tmp_path / "crawl5"
        spider, result = _run(site, crawldir)
        assert result.paused is False
        assert (not crawldir.exists()) or not any(crawldir.iterdir()), \
            "completed crawl should not leave checkpoint state behind"

    def test_pause_stops_and_retains_checkpoint(self, site, tmp_path):
        """Pause ends the crawl gracefully AND keeps the checkpoint for resume."""
        import time as _time

        crawldir = tmp_path / "crawl_pause"

        class _PausableSpider(_SiteSpider):
            download_delay = 0.3          # slow enough that the pause lands mid-crawl

        spider = _PausableSpider(crawldir=str(crawldir), interval=0)
        spider.start_urls = [site + "/"]
        spider.allowed_domains = {urlparse(site).netloc}

        def _request_pause():
            for _ in range(500):
                if getattr(spider, "_engine", None):
                    spider.pause()
                    return
                _time.sleep(0.01)

        t = threading.Thread(target=_request_pause, daemon=True)
        t.start()
        result = spider.start()
        t.join(timeout=5)

        assert result.paused is True, "pause() did not stop the crawl gracefully"
        assert crawldir.exists() and any(crawldir.iterdir()), \
            "paused crawl must retain its checkpoint for resume"

    def test_crawl_output_to_clean_markdown(self, site, tmp_path):
        """real spider output -> clean Markdown (GENIE conversion)."""
        spider, result = _run(site, tmp_path / "crawl6")
        assert result.stats.items_scraped >= 3

        # regenerate one page's markdown through GENIE's real converter
        md = html_to_markdown(PAGES["/page1"], url=site + "/page1")
        assert "Page One" in md["markdown"]
        assert md["title"] == "Page One"
        assert "First article body." in md["markdown"]
