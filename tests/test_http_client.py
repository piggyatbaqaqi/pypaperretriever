from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock, call
from urllib.robotparser import RobotFileParser

import pytest

from pypaperretriever.http_client import HttpClient, RobotsTxtCache


# ---------------------------------------------------------------------------
# RobotsTxtCache tests
# ---------------------------------------------------------------------------

class TestRobotsTxtCache:

    def test_domain_key_extracts_scheme_and_netloc(self):
        assert RobotsTxtCache._domain_key(
            "https://example.com/some/path?q=1"
        ) == "https://example.com"

    def test_cache_returns_none_on_fetch_failure(self):
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=None
        ):
            parser = cache.get_parser("https://broken.example.com/page")
        assert parser is None

    def test_cache_returns_parser_on_success(self):
        rp = RobotFileParser()
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=rp
        ):
            parser = cache.get_parser("https://good.example.com/page")
        assert parser is rp

    def test_cache_deduplicates_by_domain(self):
        rp = RobotFileParser()
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=rp
        ) as mock_fetch:
            cache.get_parser("https://example.com/page1")
            cache.get_parser("https://example.com/page2")
        mock_fetch.assert_called_once_with("https://example.com")

    def test_can_fetch_returns_true_when_no_robots(self):
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=None
        ):
            assert cache.can_fetch("https://example.com/anything") is True

    def test_can_fetch_delegates_to_parser(self):
        rp = Mock(spec=RobotFileParser)
        rp.can_fetch.return_value = False
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=rp
        ):
            result = cache.can_fetch("https://example.com/secret")
        rp.can_fetch.assert_called_once_with(
            "TestBot/1.0", "https://example.com/secret"
        )
        assert result is False

    def test_crawl_delay_returns_value_when_present(self):
        rp = Mock(spec=RobotFileParser)
        rp.crawl_delay.return_value = 10
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=rp
        ):
            delay = cache.crawl_delay("https://example.com/page")
        assert delay == 10.0

    def test_crawl_delay_returns_none_when_absent(self):
        rp = Mock(spec=RobotFileParser)
        rp.crawl_delay.return_value = None
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=rp
        ):
            delay = cache.crawl_delay("https://example.com/page")
        assert delay is None

    def test_crawl_delay_returns_none_when_no_parser(self):
        cache = RobotsTxtCache(user_agent="TestBot/1.0")
        with patch.object(
            RobotsTxtCache, "_fetch_robots_txt", return_value=None
        ):
            delay = cache.crawl_delay("https://example.com/page")
        assert delay is None


# ---------------------------------------------------------------------------
# HttpClient tests — respect_robots_txt=False (pass-through mode)
# ---------------------------------------------------------------------------

class TestHttpClientPassthrough:

    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_returns_response(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        client = HttpClient(respect_robots_txt=False)
        resp = client.get("https://example.com/data")
        assert resp.status_code == 200
        mock_get.assert_called_once()

    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_never_returns_none(self, mock_get):
        mock_get.return_value = Mock(status_code=403)
        client = HttpClient(respect_robots_txt=False)
        resp = client.get("https://example.com/forbidden")
        assert resp is not None

    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_forwards_kwargs(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        client = HttpClient(respect_robots_txt=False)
        client.get("https://example.com", timeout=5, stream=True)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 5
        assert kwargs["stream"] is True

    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_sets_default_user_agent(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        client = HttpClient(
            user_agent="MyBot/1.0", respect_robots_txt=False
        )
        client.get("https://example.com")
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] == "MyBot/1.0"

    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_preserves_caller_headers(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        client = HttpClient(respect_robots_txt=False)
        client.get(
            "https://example.com",
            headers={"User-Agent": "Custom/2.0", "Accept": "text/html"},
        )
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] == "Custom/2.0"
        assert kwargs["headers"]["Accept"] == "text/html"

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.requests.get")
    def test_passthrough_does_not_sleep(self, mock_get, mock_sleep):
        mock_get.return_value = Mock(status_code=200)
        client = HttpClient(respect_robots_txt=False)
        client.get("https://example.com/a")
        client.get("https://example.com/b")
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# HttpClient tests — respect_robots_txt=True
# ---------------------------------------------------------------------------

class TestHttpClientWithRobots:

    def _make_client(self, can_fetch=True, crawl_delay=None, **kwargs):
        """Helper to create an HttpClient with a mocked RobotsTxtCache."""
        client = HttpClient(
            user_agent="TestBot/1.0",
            respect_robots_txt=True,
            verbosity=0,
            **kwargs,
        )
        mock_cache = Mock(spec=RobotsTxtCache)
        mock_cache.can_fetch.return_value = can_fetch
        mock_cache.crawl_delay.return_value = crawl_delay
        client._robots_cache = mock_cache
        return client

    @patch("pypaperretriever.http_client.requests.get")
    def test_returns_none_when_disallowed(self, mock_get):
        client = self._make_client(can_fetch=False)
        resp = client.get("https://example.com/secret")
        assert resp is None
        mock_get.assert_not_called()

    @patch("pypaperretriever.http_client.requests.get")
    def test_returns_response_when_allowed(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        client = self._make_client(can_fetch=True)
        resp = client.get("https://example.com/page")
        assert resp is not None
        assert resp.status_code == 200

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_applies_crawl_delay(self, mock_get, mock_time, mock_sleep):
        mock_get.return_value = Mock(status_code=200)
        # Simulate: first call at t=100, second call at t=100.5
        mock_time.side_effect = [100.0, 100.5, 100.5]
        client = self._make_client(crawl_delay=5.0)

        client.get("https://example.com/page1")
        client.get("https://example.com/page2")

        # Should sleep 5.0 - 0.5 = 4.5 seconds
        mock_sleep.assert_called_once()
        actual_sleep = mock_sleep.call_args[0][0]
        assert abs(actual_sleep - 4.5) < 0.01

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_fallback_delay_when_no_crawl_delay(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        # Simulate: first call at t=100, second call immediately at t=100
        mock_time.side_effect = [100.0, 100.0, 100.0]
        client = self._make_client(
            crawl_delay=None,
            rate_limit_min_s=2.0,
            rate_limit_max_s=2.0,  # Fixed for determinism
        )

        client.get("https://example.com/a")
        client.get("https://example.com/b")

        mock_sleep.assert_called_once()
        actual_sleep = mock_sleep.call_args[0][0]
        assert abs(actual_sleep - 2.0) < 0.01

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_sleep_on_first_request(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        mock_time.return_value = 100.0
        client = self._make_client(crawl_delay=10.0)

        client.get("https://example.com/first")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_per_domain_delay_independence(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        # Both calls happen "immediately"
        mock_time.return_value = 100.0
        client = self._make_client(crawl_delay=10.0)

        # First request to domain A — no sleep (first for this domain)
        client.get("https://a.example.com/page")
        # First request to domain B — no sleep (first for this domain)
        client.get("https://b.example.com/page")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_sleep_when_enough_time_elapsed(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        # First call at t=100, second call at t=200 (well past 5s delay)
        mock_time.side_effect = [100.0, 200.0, 200.0]
        client = self._make_client(crawl_delay=5.0)

        client.get("https://example.com/page1")
        client.get("https://example.com/page2")

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# HttpClient tests — explicit inter-request delay
# ---------------------------------------------------------------------------

class TestHttpClientDelay:
    """Tests for the delay_min_s / delay_max_s feature."""

    def test_delay_disabled_by_default(self):
        client = HttpClient(verbosity=0)
        assert client.delay_min_s is None
        assert client.delay_max_s is None
        assert client._delay_enabled is False

    def test_both_bounds_set(self):
        client = HttpClient(delay_min_s=1.0, delay_max_s=5.0, verbosity=0)
        assert client.delay_min_s == 1.0
        assert client.delay_max_s == 5.0

    def test_only_min_set_derives_max(self):
        client = HttpClient(delay_min_s=2.0, verbosity=0)
        assert client.delay_min_s == 2.0
        assert client.delay_max_s == 6.0  # 3 * 2.0

    def test_only_max_set_derives_min(self):
        client = HttpClient(delay_max_s=4.0, verbosity=0)
        assert client.delay_min_s == 0.0
        assert client.delay_max_s == 4.0

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_delay_applied_between_requests(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        # First call at t=100, second call immediately at t=100
        mock_time.side_effect = [100.0, 100.0, 100.0]
        client = HttpClient(
            delay_min_s=2.0, delay_max_s=2.0, verbosity=0,
        )

        client.get("https://example.com/a")
        client.get("https://example.com/b")

        mock_sleep.assert_called_once()
        actual_sleep = mock_sleep.call_args[0][0]
        assert abs(actual_sleep - 2.0) < 0.01

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_delay_on_first_request(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        mock_time.return_value = 100.0
        client = HttpClient(delay_min_s=5.0, delay_max_s=5.0, verbosity=0)

        client.get("https://example.com/first")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_delay_without_explicit_config(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        mock_time.return_value = 100.0
        client = HttpClient(verbosity=0)  # no delay, no robots

        client.get("https://example.com/a")
        client.get("https://example.com/b")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_sleep_when_enough_time_elapsed(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        # First at t=100, second at t=200 — well past the 2s delay
        mock_time.side_effect = [100.0, 200.0, 200.0]
        client = HttpClient(delay_min_s=2.0, delay_max_s=2.0, verbosity=0)

        client.get("https://example.com/a")
        client.get("https://example.com/b")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_per_domain_independence(
        self, mock_get, mock_time, mock_sleep
    ):
        mock_get.return_value = Mock(status_code=200)
        mock_time.return_value = 100.0
        client = HttpClient(delay_min_s=10.0, delay_max_s=10.0, verbosity=0)

        client.get("https://a.example.com/page")
        client.get("https://b.example.com/page")

        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_crawl_delay_overrides_lower_bound(
        self, mock_get, mock_time, mock_sleep
    ):
        """Crawl-Delay of 10 should override delay_min_s=1, keeping max=5 raised to 10."""
        mock_get.return_value = Mock(status_code=200)
        mock_time.side_effect = [100.0, 100.0, 100.0]

        client = HttpClient(
            user_agent="TestBot/1.0",
            respect_robots_txt=True,
            delay_min_s=1.0,
            delay_max_s=5.0,
            verbosity=0,
        )
        mock_cache = Mock(spec=RobotsTxtCache)
        mock_cache.can_fetch.return_value = True
        mock_cache.crawl_delay.return_value = 10.0
        client._robots_cache = mock_cache

        client.get("https://example.com/page1")
        client.get("https://example.com/page2")

        mock_sleep.assert_called_once()
        actual_sleep = mock_sleep.call_args[0][0]
        # Both lower and upper are 10 (Crawl-Delay raised both)
        assert abs(actual_sleep - 10.0) < 0.01

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_crawl_delay_below_range_has_no_effect(
        self, mock_get, mock_time, mock_sleep
    ):
        """Crawl-Delay of 1 should NOT lower delay_min_s=5."""
        mock_get.return_value = Mock(status_code=200)
        mock_time.side_effect = [100.0, 100.0, 100.0]

        client = HttpClient(
            user_agent="TestBot/1.0",
            respect_robots_txt=True,
            delay_min_s=5.0,
            delay_max_s=5.0,
            verbosity=0,
        )
        mock_cache = Mock(spec=RobotsTxtCache)
        mock_cache.can_fetch.return_value = True
        mock_cache.crawl_delay.return_value = 1.0
        client._robots_cache = mock_cache

        client.get("https://example.com/page1")
        client.get("https://example.com/page2")

        mock_sleep.assert_called_once()
        actual_sleep = mock_sleep.call_args[0][0]
        assert abs(actual_sleep - 5.0) < 0.01

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time")
    @patch("pypaperretriever.http_client.requests.get")
    def test_crawl_delay_raises_lower_keeps_upper(
        self, mock_get, mock_time, mock_sleep
    ):
        """Crawl-Delay of 3 with range 1-10 should give range 3-10."""
        mock_get.return_value = Mock(status_code=200)
        mock_time.side_effect = [100.0, 100.0, 100.0]

        client = HttpClient(
            user_agent="TestBot/1.0",
            respect_robots_txt=True,
            delay_min_s=1.0,
            delay_max_s=10.0,
            verbosity=0,
        )
        mock_cache = Mock(spec=RobotsTxtCache)
        mock_cache.can_fetch.return_value = True
        mock_cache.crawl_delay.return_value = 3.0
        client._robots_cache = mock_cache

        # Run many times to check the range empirically
        sleeps = []
        for i in range(50):
            client._last_fetch_times["https://example.com"] = 100.0
            mock_time.side_effect = [100.0]
            mock_sleep.reset_mock()
            client._apply_rate_limit("https://example.com/page")
            if mock_sleep.called:
                sleeps.append(mock_sleep.call_args[0][0])

        assert all(s >= 2.99 for s in sleeps), f"Found sleep below 3: {min(sleeps)}"
        assert all(s <= 10.01 for s in sleeps), f"Found sleep above 10: {max(sleeps)}"


# ---------------------------------------------------------------------------
# HttpClient tests — _handle_429 header parsing
# ---------------------------------------------------------------------------

class TestHandle429:
    """Tests for _handle_429 header parsing logic."""

    def _make_client(self, **kwargs):
        return HttpClient(
            honor_retry_after=True,
            verbosity=0,
            **kwargs,
        )

    def test_retry_after_integer_seconds(self):
        client = self._make_client()
        resp = Mock(headers={"Retry-After": "30"})
        assert client._handle_429(resp, "https://example.com") == 30.0

    def test_retry_after_http_date(self):
        client = self._make_client()
        # Use a date 45 seconds in the future
        future = datetime.now(timezone.utc).timestamp() + 45
        future_dt = datetime.fromtimestamp(future, tz=timezone.utc)
        http_date = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        resp = Mock(headers={"Retry-After": http_date})
        wait = client._handle_429(resp, "https://example.com")
        # Allow 2s tolerance for test execution time
        assert 43 <= wait <= 47

    def test_ratelimit_reset_as_seconds(self):
        client = self._make_client()
        resp = Mock(headers={"RateLimit-Reset": "120"})
        assert client._handle_429(resp, "https://example.com") == 120.0

    @patch("pypaperretriever.http_client.time.time", return_value=1700000000.0)
    def test_ratelimit_reset_as_unix_timestamp(self, _mock_time):
        client = self._make_client()
        resp = Mock(headers={"RateLimit-Reset": "1700000090"})
        wait = client._handle_429(resp, "https://example.com")
        assert abs(wait - 90.0) < 1.0

    def test_x_ratelimit_reset_fallback(self):
        client = self._make_client()
        resp = Mock(headers={"X-RateLimit-Reset": "25"})
        assert client._handle_429(resp, "https://example.com") == 25.0

    def test_ratelimit_reset_preferred_over_x(self):
        client = self._make_client()
        resp = Mock(headers={
            "RateLimit-Reset": "10",
            "X-RateLimit-Reset": "99",
        })
        assert client._handle_429(resp, "https://example.com") == 10.0

    def test_retry_after_preferred_over_ratelimit_reset(self):
        client = self._make_client()
        resp = Mock(headers={
            "Retry-After": "5",
            "RateLimit-Reset": "99",
        })
        assert client._handle_429(resp, "https://example.com") == 5.0

    def test_default_fallback_when_no_headers(self):
        client = self._make_client(default_retry_after_s=60.0)
        resp = Mock(headers={})
        assert client._handle_429(resp, "https://example.com") == 60.0

    def test_custom_default_fallback(self):
        client = self._make_client(default_retry_after_s=30.0)
        resp = Mock(headers={})
        assert client._handle_429(resp, "https://example.com") == 30.0

    def test_max_retry_after_clamps_value(self):
        client = self._make_client(max_retry_after_s=10.0)
        resp = Mock(headers={"Retry-After": "600"})
        assert client._handle_429(resp, "https://example.com") == 10.0

    def test_unparseable_retry_after_falls_through(self):
        client = self._make_client(default_retry_after_s=60.0)
        resp = Mock(headers={"Retry-After": "not-a-number-or-date"})
        assert client._handle_429(resp, "https://example.com") == 60.0


# ---------------------------------------------------------------------------
# HttpClient tests — 429 retry integration in get()
# ---------------------------------------------------------------------------

class TestHttpClient429Retry:
    """Tests for the retry loop in HttpClient.get()."""

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_retries_on_429_then_succeeds(self, mock_get, _mock_time, mock_sleep):
        resp_429 = Mock(status_code=429, headers={"Retry-After": "2"})
        resp_200 = Mock(status_code=200, headers={})
        mock_get.side_effect = [resp_429, resp_200]

        client = HttpClient(honor_retry_after=True, verbosity=0)
        resp = client.get("https://example.com/page")

        assert resp.status_code == 200
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_gives_up_after_max_retries(self, mock_get, _mock_time, mock_sleep):
        resp_429 = Mock(status_code=429, headers={"Retry-After": "1"})
        mock_get.return_value = resp_429

        client = HttpClient(honor_retry_after=True, max_retries=3, verbosity=0)
        resp = client.get("https://example.com/page")

        assert resp.status_code == 429
        # 1 initial + 3 retries = 4 calls
        assert mock_get.call_count == 4
        assert mock_sleep.call_count == 3

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_retry_when_disabled(self, mock_get, _mock_time, mock_sleep):
        resp_429 = Mock(status_code=429, headers={"Retry-After": "5"})
        mock_get.return_value = resp_429

        client = HttpClient(honor_retry_after=False, verbosity=0)
        resp = client.get("https://example.com/page")

        assert resp.status_code == 429
        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_no_retry_on_non_429(self, mock_get, _mock_time, mock_sleep):
        resp_500 = Mock(status_code=500, headers={})
        mock_get.return_value = resp_500

        client = HttpClient(honor_retry_after=True, verbosity=0)
        resp = client.get("https://example.com/page")

        assert resp.status_code == 500
        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("pypaperretriever.http_client.time.sleep")
    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_multiple_429s_then_success(self, mock_get, _mock_time, mock_sleep):
        resp_429 = Mock(status_code=429, headers={"Retry-After": "1"})
        resp_200 = Mock(status_code=200, headers={})
        mock_get.side_effect = [resp_429, resp_429, resp_200]

        client = HttpClient(honor_retry_after=True, max_retries=3, verbosity=0)
        resp = client.get("https://example.com/page")

        assert resp.status_code == 200
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# HttpClient tests — 403 domain suppression
# ---------------------------------------------------------------------------

class TestHttpClient403Suppression:
    """Tests for the 403 domain suppression feature."""

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_403_suppresses_domain(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://publisher.com/article/1"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=True, verbosity=0)
        resp1 = client.get("https://publisher.com/article/1")

        assert resp1.status_code == 403
        assert mock_get.call_count == 1

        # Second request to the same domain should be short-circuited
        resp2 = client.get("https://publisher.com/article/2")

        assert resp2.status_code == 403
        assert resp2.url == "https://publisher.com/article/2"
        # Still only 1 real network call
        assert mock_get.call_count == 1

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_403_does_not_suppress_different_domain(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://publisher-a.com/page"
        resp_200 = Mock(status_code=200, headers={})
        mock_get.side_effect = [resp_403, resp_200]

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://publisher-a.com/page")
        resp2 = client.get("https://publisher-b.com/page")

        assert resp2.status_code == 200
        assert mock_get.call_count == 2

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_suppression_disabled(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://publisher.com/article/1"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=False, verbosity=0)
        client.get("https://publisher.com/article/1")
        client.get("https://publisher.com/article/2")

        # Both requests should hit the network
        assert mock_get.call_count == 2

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_doi_org_not_suppressed(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://doi.org/10.1234/test"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://doi.org/10.1234/test")
        client.get("https://doi.org/10.5678/other")

        # doi.org is exempt — both requests should hit the network
        assert mock_get.call_count == 2
        assert client.suppressed_domains == {}

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_dx_doi_org_not_suppressed(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://dx.doi.org/10.1234/test"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://dx.doi.org/10.1234/test")
        client.get("https://dx.doi.org/10.5678/other")

        assert mock_get.call_count == 2
        assert client.suppressed_domains == {}

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_200_does_not_suppress(self, mock_get, _mock_time):
        resp_200 = Mock(status_code=200, headers={})
        mock_get.return_value = resp_200

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://publisher.com/article/1")
        client.get("https://publisher.com/article/2")

        assert mock_get.call_count == 2
        assert client.suppressed_domains == {}

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_suppressed_domains_property(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://blocked.com/x"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://blocked.com/x")

        domains = client.suppressed_domains
        assert "https://blocked.com" in domains
        assert "403 Forbidden" in domains["https://blocked.com"]

    @patch("pypaperretriever.http_client.time.time", return_value=100.0)
    @patch("pypaperretriever.http_client.requests.get")
    def test_synthetic_403_has_correct_url(self, mock_get, _mock_time):
        resp_403 = Mock(status_code=403, headers={})
        resp_403.url = "https://publisher.com/article/1"
        mock_get.return_value = resp_403

        client = HttpClient(suppress_on_403=True, verbosity=0)
        client.get("https://publisher.com/article/1")

        # Synthetic response should carry the new URL
        resp2 = client.get("https://publisher.com/article/99")
        assert resp2.status_code == 403
        assert resp2.url == "https://publisher.com/article/99"
