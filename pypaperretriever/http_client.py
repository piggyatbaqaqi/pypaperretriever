"""HTTP client with optional robots.txt and Crawl-Delay support.

This module provides an :class:`HttpClient` that can optionally fetch and
respect robots.txt directives, including Crawl-Delay, for every domain it
contacts.  When the feature is disabled (the default) the client is a thin
pass-through to :func:`requests.get`.
"""

from __future__ import annotations

import random
import time
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
    """HTTP client with optional robots.txt and Crawl-Delay support.

    When *respect_robots_txt* is ``True`` the client:

    1. Checks ``can_fetch()`` before each request and returns ``None`` for
       disallowed URLs.
    2. Applies the Crawl-Delay directive for the target domain if present,
       otherwise falls back to a random delay between *rate_limit_min_s* and
       *rate_limit_max_s*.

    When *respect_robots_txt* is ``False`` (the default) the client is a thin
    wrapper around :func:`requests.get` with no rate limiting.

    Args:
        user_agent: Default User-Agent string.
        respect_robots_txt: Enable robots.txt checking and Crawl-Delay.
        rate_limit_min_s: Minimum fallback delay in seconds.
        rate_limit_max_s: Maximum fallback delay in seconds.
        verbosity: Logging verbosity (0=silent, 1=warnings, 2=info, 3=debug).
    """

    def __init__(
        self,
        user_agent: str = "PyPaperRetriever/1.0",
        respect_robots_txt: bool = False,
        rate_limit_min_s: float = 1.0,
        rate_limit_max_s: float = 3.0,
        verbosity: int = 1,
    ) -> None:
        self.user_agent = user_agent
        self.respect_robots_txt = respect_robots_txt
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

    def get(self, url: str, **kwargs: Any) -> Optional[requests.Response]:
        """Make a GET request, optionally respecting robots.txt.

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

        return requests.get(url, **kwargs)
