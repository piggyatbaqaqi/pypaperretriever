# HTTP Client & Polite Crawling

PyPaperRetriever includes an `HttpClient` that wraps `requests.get()` with two
opt-in politeness features:

1. **robots.txt / Crawl-Delay** — check each domain's `robots.txt` before
   fetching and respect its `Crawl-Delay` directive.
2. **HTTP 429 retry** — automatically wait and retry when a server replies with
   *429 Too Many Requests*.

By default robots.txt support is **off** (zero overhead) while 429 retry is
**on** (safe default — it only activates when a server explicitly asks you to
slow down).

## Quick examples

### Python

```python
from pypaperretriever import PaperRetriever

retriever = PaperRetriever(
    email="you@example.com",
    doi="10.7759/cureus.76081",
    download_directory="PDFs",
    respect_robots_txt=True,   # enable robots.txt + Crawl-Delay
)
retriever.download()
```

429 retry is handled transparently — no code changes needed.

### CLI

```bash
pypaperretriever \
    --email you@example.com \
    --doi 10.7759/cureus.76081 \
    --dwn-dir PDFs \
    --respect-robots-txt
```

---

## robots.txt support

### How it works

When `respect_robots_txt=True`, every outgoing GET request is first checked
against the target domain's `robots.txt`.

- **Disallowed URL** — `HttpClient.get()` returns `None` and the caller skips
  that source.
- **Unreachable robots.txt** (network error, 404, etc.) — the domain is treated
  as fully permissive.
- **Caching** — parsed `robots.txt` files are cached per domain for the
  lifetime of the `HttpClient` instance.  Repeat requests to the same domain do
  not re-fetch `robots.txt`.

### Crawl-Delay

If a domain's `robots.txt` declares a `Crawl-Delay` for the active User-Agent,
`HttpClient` sleeps between requests to that domain so consecutive fetches are
at least that many seconds apart.  When no `Crawl-Delay` is declared a random
fallback between `rate_limit_min_s` and `rate_limit_max_s` (default 1–3 s) is
used instead.

Delays are tracked **per domain** — a slow Crawl-Delay on one site does not
block requests to a different site.

---

## HTTP 429 (Too Many Requests) retry

When `honor_retry_after=True` (the default) and a server responds with status
**429**, `HttpClient` automatically determines how long to wait and retries the
request — up to `max_retries` times (default 3).

### Header priority

The wait time is extracted from response headers in this order:

| Priority | Header | Format |
|----------|--------|--------|
| 1 | `Retry-After` (RFC 7231) | Integer seconds **or** HTTP date |
| 2 | `RateLimit-Reset` / `X-RateLimit-Reset` (IETF draft) | Unix timestamp (> 1 000 000 000) **or** seconds until reset |
| 3 | *(none found)* | Falls back to `default_retry_after_s` (default 60 s) |

The resolved wait time is clamped to `max_retry_after_s` (default 300 s) to
prevent unbounded waits.

### Example log output

At `verbosity >= 1` you will see messages like:

```text
  [HttpClient] HTTP 429 — waiting 30s before retry (from Retry-After)
```

When no rate-limit headers are found and `verbosity >= 2`, all response headers
are printed to help with debugging.

### Disabling 429 retry

Pass `honor_retry_after=False` to disable automatic retry entirely.  The 429
response will be returned to the caller as-is.

```python
from pypaperretriever import HttpClient

client = HttpClient(honor_retry_after=False)
```

---

## Using `HttpClient` directly

You can instantiate `HttpClient` on its own for custom workflows:

```python
from pypaperretriever import HttpClient

client = HttpClient(
    user_agent="MyBot/1.0",
    respect_robots_txt=True,
    honor_retry_after=True,
    max_retries=5,
    default_retry_after_s=30.0,
    max_retry_after_s=120.0,
    rate_limit_min_s=1.0,
    rate_limit_max_s=3.0,
    verbosity=2,
)

response = client.get("https://api.example.com/data")
if response is None:
    print("Blocked by robots.txt")
elif response.status_code == 429:
    print("Still rate-limited after max retries")
else:
    print(response.status_code)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_agent` | `str` | `"PyPaperRetriever/1.0"` | Default `User-Agent` header and the name used for robots.txt look-ups. |
| `respect_robots_txt` | `bool` | `False` | Enable robots.txt checking and Crawl-Delay enforcement. |
| `honor_retry_after` | `bool` | `True` | Automatically retry on HTTP 429 responses. |
| `max_retries` | `int` | `3` | Maximum number of 429 retries per request. |
| `default_retry_after_s` | `float` | `60.0` | Fallback wait (seconds) when no rate-limit header is present. |
| `max_retry_after_s` | `float` | `300.0` | Upper clamp on the server-requested wait time. |
| `rate_limit_min_s` | `float` | `1.0` | Minimum fallback delay (seconds) when no Crawl-Delay is declared. |
| `rate_limit_max_s` | `float` | `3.0` | Maximum fallback delay (seconds). |
| `verbosity` | `int` | `1` | Logging verbosity: 0 = silent, 1 = warnings, 2 = info, 3 = debug. |

### Return value of `get()`

`HttpClient.get()` returns `Optional[requests.Response]`:

- A normal `requests.Response` on success (any HTTP status code).
- `None` **only** when `respect_robots_txt=True` and the URL is disallowed by
  `robots.txt`.  Callers must handle the `None` case.
- When `respect_robots_txt=False`, `get()` never returns `None`.
- If 429 retry is enabled and the server keeps responding with 429 after
  `max_retries` attempts, the final 429 response is returned (not `None`).

---

## Integration with other classes

`HttpClient` is used internally by all main classes:

| Class | How it's configured |
|-------|---------------------|
| `PaperRetriever` | Accepts `respect_robots_txt` constructor parameter; creates its own `HttpClient`. |
| `PubMedSearcher` | Accepts `respect_robots_txt`; creates its own `HttpClient` and passes the flag through to `PaperRetriever` and `ReferenceRetriever`. |
| `ReferenceRetriever` | Accepts an optional `http_client` parameter. |
| `utils.doi_to_pmid()` | Accepts an optional `http_client` parameter for its PMC ID Converter fallback request. |

All classes benefit from 429 retry automatically (it is on by default in every
`HttpClient` instance).

---

## API Reference

::: pypaperretriever.http_client.RobotsTxtCache
    options:
      members:
        - __init__
        - can_fetch
        - crawl_delay
      inherited_members: false

::: pypaperretriever.http_client.HttpClient
    options:
      members:
        - __init__
        - get
      inherited_members: false
