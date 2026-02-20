"""HTTP client with optional robots.txt, Crawl-Delay, and 429 retry support.

This module provides an :class:`HttpClient` that can optionally fetch and
respect robots.txt directives, including Crawl-Delay, for every domain it
contacts.  It can also automatically retry on HTTP 429 (Too Many Requests)
responses using the delay indicated by the server.  When both features are
disabled (the default) the client is a thin pass-through to
:func:`requests.get`.
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


class RobotsTxtCache:
    """Lazily fetch and cache :class:`RobotFileParser` instances per domain.

    Args:
        user_agent: User-Agent string used for ``can_fetch`` / ``crawl_delay``
            look-ups in the parsed robots.txt.
    """

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._cache: Dict[str, Optional[RobotFileParser]] = {}

    @staticmethod
    def _domain_key(url: str) -> str:
        """Return ``scheme://netloc`` for *url*."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_parser(self, url: str) -> Optional[RobotFileParser]:
        """Return a :class:`RobotFileParser` for the domain of *url*.

        The parser is fetched and cached on first access.  Returns ``None``
        when the robots.txt cannot be retrieved (treated as fully permissive).
        """
        key = self._domain_key(url)
        if key not in self._cache:
            self._cache[key] = self._fetch_robots_txt(key)
        return self._cache[key]

    @staticmethod
    def _fetch_robots_txt(domain_key: str) -> Optional[RobotFileParser]:
        """Fetch and parse robots.txt for *domain_key*."""
        robots_url = f"{domain_key}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.read()
            return rp
        except Exception:
            return None

    def can_fetch(self, url: str) -> bool:
        """Check whether the user-agent is allowed to fetch *url*."""
        parser = self.get_parser(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> Optional[float]:
        """Return the Crawl-Delay for the domain of *url*, or ``None``."""
        parser = self.get_parser(url)
        if parser is None:
            return None
        delay = parser.crawl_delay(self.user_agent)
        if delay is not None:
            return float(delay)
        return None


class HttpClient:
    """HTTP client with optional robots.txt, Crawl-Delay, and 429 retry support.

    When *respect_robots_txt* is ``True`` the client:

    1. Checks ``can_fetch()`` before each request and returns ``None`` for
       disallowed URLs.
    2. Applies the Crawl-Delay directive for the target domain if present,
       otherwise falls back to a random delay between *rate_limit_min_s* and
       *rate_limit_max_s*.

    When *honor_retry_after* is ``True`` the client automatically retries
    requests that receive an HTTP 429 (Too Many Requests) response.  The wait
    time is determined from ``Retry-After`` (RFC 7231), ``RateLimit-Reset`` or
    ``X-RateLimit-Reset`` (IETF draft) headers.  If none are present a
    *default_retry_after_s* fallback is used.

    When both features are disabled (the default for robots.txt) the client is
    a thin wrapper around :func:`requests.get` with no rate limiting.

    Args:
        user_agent: Default User-Agent string.
        respect_robots_txt: Enable robots.txt checking and Crawl-Delay.
        honor_retry_after: Automatically retry on HTTP 429 responses.
        max_retries: Maximum number of 429 retries per request.
        default_retry_after_s: Fallback wait time when no retry header is
            present.
        max_retry_after_s: Cap on the wait time extracted from headers.
        rate_limit_min_s: Minimum fallback delay in seconds.
        rate_limit_max_s: Maximum fallback delay in seconds.
        verbosity: Logging verbosity (0=silent, 1=warnings, 2=info, 3=debug).
    """

    def __init__(
        self,
        user_agent: str = "PyPaperRetriever/1.0",
        respect_robots_txt: bool = False,
        honor_retry_after: bool = True,
        max_retries: int = 3,
        default_retry_after_s: float = 60.0,
        max_retry_after_s: float = 300.0,
        rate_limit_min_s: float = 1.0,
        rate_limit_max_s: float = 3.0,
        verbosity: int = 1,
    ) -> None:
        self.user_agent = user_agent
        self.respect_robots_txt = respect_robots_txt
        self.honor_retry_after = honor_retry_after
        self.max_retries = max_retries
        self.default_retry_after_s = default_retry_after_s
        self.max_retry_after_s = max_retry_after_s
        self.rate_limit_min_s = rate_limit_min_s
        self.rate_limit_max_s = rate_limit_max_s
        self.verbosity = verbosity
        self._last_fetch_times: Dict[str, float] = {}
        self._robots_cache: Optional[RobotsTxtCache] = None
        if self.respect_robots_txt:
            self._robots_cache = RobotsTxtCache(user_agent=self.user_agent)

    @staticmethod
    def _domain_key(url: str) -> str:
        """Return ``scheme://netloc`` for per-domain tracking."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _apply_rate_limit(self, url: str) -> None:
        """Sleep to respect Crawl-Delay or fallback delay for this domain."""
        if not self.respect_robots_txt:
            return

        domain = self._domain_key(url)
        last_time = self._last_fetch_times.get(domain)
        if last_time is None:
            return

        delay_seconds: Optional[float] = None

        if self._robots_cache is not None:
            delay_seconds = self._robots_cache.crawl_delay(url)
            if delay_seconds is not None and self.verbosity >= 3:
                print(
                    f"  [HttpClient] Crawl-Delay for {domain}: "
                    f"{delay_seconds}s"
                )

        if delay_seconds is None:
            delay_seconds = random.uniform(
                self.rate_limit_min_s, self.rate_limit_max_s
            )
            if self.verbosity >= 3:
                print(
                    f"  [HttpClient] Fallback delay for {domain}: "
                    f"{delay_seconds:.2f}s"
                )

        elapsed = time.time() - last_time
        sleep_time = delay_seconds - elapsed
        if sleep_time > 0:
            if self.verbosity >= 3:
                print(f"  [HttpClient] Sleeping {sleep_time:.2f}s for {domain}")
            time.sleep(sleep_time)

    def _handle_429(self, response: requests.Response, url: str) -> float:
        """Parse a 429 response to determine how long to wait before retrying.

        The method checks, in order:

        1. ``Retry-After`` header (RFC 7231) — integer seconds or HTTP date.
        2. ``RateLimit-Reset`` / ``X-RateLimit-Reset`` (IETF draft) — Unix
           timestamp (>1 000 000 000) or seconds-to-reset.
        3. Falls back to *default_retry_after_s*.

        The returned value is clamped to *max_retry_after_s*.

        Args:
            response: The 429 response.
            url: The URL that was requested (for logging).

        Returns:
            Wait time in seconds.
        """
        wait_seconds: Optional[float] = None
        header_used: Optional[str] = None

        # 1. Retry-After (RFC 7231)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait_seconds = float(retry_after)
                header_used = "Retry-After"
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                    wait_seconds = (
                        retry_time - datetime.now(retry_time.tzinfo)
                    ).total_seconds()
                    wait_seconds = max(0.0, wait_seconds)
                    header_used = "Retry-After"
                except (ValueError, TypeError):
                    pass

        # 2. RateLimit-Reset / X-RateLimit-Reset (IETF draft)
        if wait_seconds is None:
            reset_header = (
                response.headers.get("RateLimit-Reset")
                or response.headers.get("X-RateLimit-Reset")
            )
            if reset_header:
                try:
                    reset_value = int(reset_header)
                    if reset_value > 1_000_000_000:
                        wait_seconds = max(0.0, reset_value - time.time())
                    else:
                        wait_seconds = float(reset_value)
                    header_used = (
                        "RateLimit-Reset"
                        if "RateLimit-Reset" in response.headers
                        else "X-RateLimit-Reset"
                    )
                except (ValueError, TypeError):
                    pass

        # 3. Default fallback
        if wait_seconds is None:
            wait_seconds = self.default_retry_after_s
            header_used = "default"
            if self.verbosity >= 1:
                print(
                    "  [HttpClient] No rate-limit headers found, "
                    f"using {self.default_retry_after_s}s default"
                )
                if self.verbosity >= 2:
                    print("  All response headers:")
                    for name, value in response.headers.items():
                        print(f"    {name}: {value}")

        # Clamp to max
        wait_seconds = min(wait_seconds, self.max_retry_after_s)

        if self.verbosity >= 1:
            print(
                f"  [HttpClient] HTTP 429 — waiting {wait_seconds:.0f}s "
                f"before retry (from {header_used})"
            )

        return wait_seconds

    def get(self, url: str, **kwargs: Any) -> Optional[requests.Response]:
        """Make a GET request, optionally respecting robots.txt and 429 retry.

        Args:
            url: The URL to fetch.
            **kwargs: Additional keyword arguments passed to
                :func:`requests.get`.

        Returns:
            A :class:`requests.Response`, or ``None`` if the URL is disallowed
            by robots.txt.
        """
        if self.respect_robots_txt and self._robots_cache is not None:
            if not self._robots_cache.can_fetch(url):
                if self.verbosity >= 1:
                    print(f"  [HttpClient] Blocked by robots.txt: {url}")
                return None

        self._apply_rate_limit(url)

        headers = kwargs.get("headers", {})
        if "User-Agent" not in headers:
            headers["User-Agent"] = self.user_agent
            kwargs["headers"] = headers

        domain = self._domain_key(url)
        self._last_fetch_times[domain] = time.time()

        response = requests.get(url, **kwargs)

        if self.honor_retry_after and response.status_code == 429:
            for attempt in range(self.max_retries):
                wait = self._handle_429(response, url)
                time.sleep(wait)
                self._last_fetch_times[domain] = time.time()
                response = requests.get(url, **kwargs)
                if response.status_code != 429:
                    break

        return response
