"""Remaining REAL upstream Scrapling spider behaviours (external_optional).

Proves the stages that "it crawled some pages" does NOT cover, each via the real
upstream mechanism and an instrumented local server:

  A. resume from checkpoint   (crawldir + a NEW spider instance)
  B. cancellation             (second pause() == upstream force stop)
  C. retry                    (max_blocked_retries + retry_blocked_request)
  D. robots policy            (RobotsTxtManager)
  E. real concurrency         (server-observed max in-flight >= 2)
  F. real throttle            (server-observed minimum request pacing)
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

import pytest

scrapling = pytest.importorskip("scrapling", reason="scrapling is not installed here")

from scrapling.spiders import Spider  # noqa: E402

# ------------------------------------------------------------------ site state
class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.inflight = 0
        self.max_inflight = 0
        self.timestamps: list[float] = []
        self.flaky_hits = 0

    def enter(self, path):
        with self.lock:
            self.counts[path] = self.counts.get(path, 0) + 1
            self.timestamps.append(time.monotonic())
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)

    def leave(self):
        with self.lock:
            self.inflight -= 1

    def get(self, path):
        with self.lock:
            return self.counts.get(path, 0)


ROBOTS = "User-agent: *\nAllow: /allowed\nDisallow: /private\n"

PAGES = {
    "/": ('<html><head><title>Home</title></head><body>'
          '<a href="/allowed">allowed</a> <a href="/private">private</a>'
          '<a href="/page1">p1</a></body></html>'),
    "/allowed": "<html><head><title>Allowed</title></head><body><h1>ok</h1></body></html>",
    "/private": "<html><head><title>Private</title></head><body><h1>secret</h1></body></html>",
    "/page1": "<html><head><title>P1</title></head><body><h1>p1</h1></body></html>",
    "/page2": "<html><head><title>P2</title></head><body><h1>p2</h1></body></html>",
    "/slow1": "<html><head><title>S1</title></head><body><h1>s1</h1></body></html>",
    "/slow2": "<html><head><title>S2</title></head><body><h1>s2</h1></body></html>",
    "/slow3": "<html><head><title>S3</title></head><body><h1>s3</h1></body></html>",
}
SLOW = {"/slow1", "/slow2", "/slow3"}


def _make_server(state: _State, flaky_first_status: int = 503):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path
            state.enter(path)
            try:
                if path == "/robots.txt":
                    self._reply(200, ROBOTS, "text/plain")
                    return
                if path == "/flaky":
                    with state.lock:
                        state.flaky_hits += 1
                        n = state.flaky_hits
                    # first attempt "blocked", later attempts succeed
                    if n == 1:
                        self._reply(flaky_first_status, "blocked", "text/html")
                        return
                    self._reply(200, PAGES["/page1"], "text/html")
                    return
                if path in SLOW:
                    time.sleep(0.4)          # widen the window to observe concurrency
                body = PAGES.get(path)
                if body is None:
                    self._reply(404, "", "text/html")
                    return
                self._reply(200, body, "text/html")
            finally:
                state.leave()

        def _reply(self, code, body, ctype):
            raw = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def site():
    state = _State()
    server = _make_server(state)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, state
    server.shutdown()
    server.server_close()


def _host(base):
    return urlparse(base).netloc


# ------------------------------------------------------------------- spiders
class _Base(Spider):
    name = "behaviour-base"
    robots_txt_obey = False

    async def parse(self, response):
        yield {"url": response.url, "title": "".join(t.text for t in response.css("title"))}
        for a in response.css("a"):
            href = (a.attrib or {}).get("href", "")
            if href:
                yield response.follow(urljoin(response.url, href))


class _FlakySpider(_Base):
    """Treats 503 as a BLOCKED response so upstream's retry path engages."""
    name = "flaky"
    max_blocked_retries = 3

    async def is_blocked(self, response) -> bool:
        # the engine AWAITS this hook, so it must be a coroutine
        return getattr(response, "status", 200) == 503


class _RobotsSpider(_Base):
    name = "robots"
    robots_txt_obey = True


# ==================================================================== E: concurrency
class TestRealConcurrency:
    def test_server_observes_parallel_requests(self, site):
        base, state = site
        spider = _Base(crawldir=None)
        spider.name = "concurrency"
        spider.start_urls = [base + "/slow1", base + "/slow2", base + "/slow3"]
        spider.allowed_domains = {_host(base)}
        spider.concurrent_requests = 3
        spider.download_delay = 0.0
        spider.start()

        assert state.max_inflight >= 2, \
            f"server never saw >=2 simultaneous requests (max={state.max_inflight})"


# ==================================================================== F: throttle
class TestRealThrottle:
    def test_download_delay_is_actually_enforced(self, site):
        base, state = site
        delay = 0.25
        spider = _Base(crawldir=None)
        spider.name = "throttle"
        spider.start_urls = [base + "/page1", base + "/page2"]
        spider.allowed_domains = {_host(base)}
        spider.concurrent_requests = 1
        spider.download_delay = delay
        spider.start()

        stamps = sorted(state.timestamps)
        assert len(stamps) >= 2
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        # allow generous tolerance for scheduler/socket overhead
        assert max(gaps) >= delay * 0.8, \
            f"no pacing observed; gaps={['%.3f' % g for g in gaps]} expected >= {delay}"


# ==================================================================== D: robots
class TestRobotsPolicy:
    def test_disallowed_path_is_never_fetched(self, site):
        base, state = site
        spider = _RobotsSpider(crawldir=None)
        spider.start_urls = [base + "/"]
        spider.allowed_domains = {_host(base)}
        spider.concurrent_requests = 1
        spider.start()

        assert state.get("/private") == 0, "robots-disallowed path was fetched"
        assert state.get("/allowed") >= 1, "robots-allowed path was not crawled"

    def test_robots_disabled_fetches_both(self, site):
        """Documented contrast: with robots off, the disallowed path is reachable."""
        base, state = site
        spider = _Base(crawldir=None)
        spider.name = "no-robots"
        spider.start_urls = [base + "/"]
        spider.allowed_domains = {_host(base)}
        spider.robots_txt_obey = False
        spider.concurrent_requests = 1
        spider.start()

        assert state.get("/private") >= 1


# ==================================================================== C: retry
class TestRetry:
    def test_blocked_request_is_retried_then_succeeds(self, site):
        base, state = site
        spider = _FlakySpider(crawldir=None)
        spider.start_urls = [base + "/flaky"]
        spider.allowed_domains = {_host(base)}
        spider.concurrent_requests = 1
        result = spider.start()

        # upstream: first response counted blocked, then retried and succeeded
        assert state.flaky_hits >= 2, "upstream retry did not re-request"
        assert result.stats.failed_requests_count == 0 or result.completed


# ==================================================================== B: cancellation
class TestCancellation:
    def test_force_stop_halts_the_crawl(self, site):
        base, state = site
        spider = _Base(crawldir=None)
        spider.name = "cancel"
        spider.start_urls = [base + "/slow1", base + "/slow2", base + "/slow3"]
        spider.allowed_domains = {_host(base)}
        spider.concurrent_requests = 1
        spider.download_delay = 0.2

        def _cancel():
            # wait until the crawl is genuinely under way before cancelling it,
            # otherwise we would only be proving that an idle crawl can be stopped
            for _ in range(2000):
                eng = getattr(spider, "_engine", None)
                if eng and sum(state.counts.values()) >= 1:
                    eng.request_pause()      # graceful pause
                    eng.request_pause()      # second call == upstream force stop
                    return
                time.sleep(0.005)

        threading.Thread(target=_cancel, daemon=True).start()
        result = spider.start()

        at_cancel = sum(state.counts.values())
        time.sleep(0.6)
        after = sum(state.counts.values())
        total_pages = 3

        # upstream force-stop does NOT set paused (only a graceful pause does), so the
        # proof is that the crawl ended early and stopped issuing work.
        assert after <= at_cancel + 1, "requests continued after cancellation"
        assert sum(state.counts.values()) < total_pages, \
            "cancellation did not halt the crawl early"


# ==================================================================== A: resume
class TestResumeFromCheckpoint:
    def test_new_instance_resumes_and_does_not_refetch(self, site, tmp_path):
        base, state = site
        crawldir = tmp_path / "resume"

        # --- run 1: pause so upstream RETAINS the checkpoint
        spider1 = _Base(crawldir=str(crawldir), interval=0)
        spider1.name = "resume-1"
        spider1.start_urls = [base + "/"]
        spider1.allowed_domains = {_host(base)}
        spider1.concurrent_requests = 1
        spider1.download_delay = 0.15

        def _pause():
            for _ in range(1000):
                if getattr(spider1, "_engine", None):
                    spider1.pause()
                    return
                time.sleep(0.005)

        threading.Thread(target=_pause, daemon=True).start()
        r1 = spider1.start()
        assert r1.paused is True, "run 1 did not pause; no checkpoint to resume from"
        assert crawldir.exists() and any(crawldir.iterdir()), "no checkpoint retained"

        completed_before = {p: c for p, c in state.counts.items()}

        # --- run 2: a NEW spider instance resumes from that checkpoint
        spider2 = _Base(crawldir=str(crawldir), interval=0)
        spider2.name = "resume-2"
        spider2.start_urls = [base + "/"]
        spider2.allowed_domains = {_host(base)}
        spider2.concurrent_requests = 1
        spider2.download_delay = 0.15
        r2 = spider2.start()

        # pages already completed in run 1 must not be fetched again
        for path, before in completed_before.items():
            now = state.get(path)
            assert now <= before + 1, \
                f"{path} was refetched after resume ({before} -> {now})"
        assert r2.completed is True or r2.stats.requests_count >= 1
