from unittest.mock import Mock, patch, MagicMock
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
