# HTTP Client & Polite Crawling

PyPaperRetriever includes an `HttpClient` that wraps `requests.get()` with
five features for polite and efficient crawling:

1. **robots.txt / Crawl-Delay** — check each domain's `robots.txt` before
   fetching and respect its `Crawl-Delay` directive.
2. **Inter-request delay** — insert a random per-domain delay between
   consecutive requests, independently of robots.txt.
3. **Exponential backoff** — automatically back off from domains that return
   503, 504, or 429 without delay headers, and gradually recover on success.
4. **HTTP 429 retry** — automatically wait and retry when a server replies with
   *429 Too Many Requests*.
5. **403 domain suppression** — remember domains that return *403 Forbidden*
   and short-circuit subsequent requests to the same domain, avoiding redundant
   network round-trips during bulk downloads.

By default, the `polite_mode` flag is enabled (`True`), meaning backoff, 429 retry, and 403 suppression are active. Individual features like robots.txt support and inter-request delays are **off** (zero overhead) by default unless explicitly enabled.

Setting `polite_mode=False` disables all polite crawling features completely (including robots.txt compliance, backoff, retry, and suppression) to prioritize raw access.

## Quick examples

### Python

```python
from pypaperretriever import PaperRetriever

retriever = PaperRetriever(
    email="you@example.com",
    doi="10.7759/cureus.76081",
    download_directory="PDFs",
    polite_mode=False,   # disable all polite crawling features for raw access
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
    --no-polite-mode
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

## Inter-request delay

You can insert a random per-domain delay between consecutive requests by
setting `delay_min_s` and/or `delay_max_s`.  This is useful for being polite
to servers that don't publish a `Crawl-Delay` in their `robots.txt`.

### Enabling delays

Set either or both bounds.  The other bound is derived automatically:

| You set | Resolved range |
|---------|---------------|
| `delay_min_s=1.0, delay_max_s=5.0` | 1–5 s |
| `delay_min_s=2.0` (only) | 2–6 s (max = 3 × min) |
| `delay_max_s=4.0` (only) | 0–4 s (min = 0) |
| Neither (default) | Off — no delay |

```python
from pypaperretriever import HttpClient

client = HttpClient(delay_min_s=1.0, delay_max_s=3.0)
```

### Interaction with Crawl-Delay

When both an explicit delay range and `respect_robots_txt=True` are active,
a `Crawl-Delay` directive **overrides the lower bound** of the delay range.
If the `Crawl-Delay` is larger than the configured minimum, it raises the
floor (and the ceiling if necessary so that max ≥ min).  If the `Crawl-Delay`
is smaller than the configured minimum, it has no effect.

| Config | Crawl-Delay | Effective range |
|--------|-------------|-----------------|
| `delay_min_s=1, delay_max_s=5` | 3 s | 3–5 s |
| `delay_min_s=1, delay_max_s=5` | 0.5 s | 1–5 s (unchanged) |
| `delay_min_s=1, delay_max_s=3` | 10 s | 10–10 s |
| No explicit delay | 5 s | 5–5 s (fixed) |
| No explicit delay | None | `rate_limit_min_s`–`rate_limit_max_s` fallback |

Delays are tracked **per domain** — a slow delay on one site does not block
requests to a different site.

---

## Exponential backoff

`HttpClient` maintains per-domain exponential backoff state.  When a request
to a domain receives one of the following responses, the backoff delay for
that domain is increased:

- **HTTP 503** (Service Unavailable)
- **HTTP 504** (Gateway Timeout)
- **HTTP 429** without any rate-limit headers (i.e. no `Retry-After`,
  `RateLimit-Reset`, or `X-RateLimit-Reset`)

The request is then retried (up to `max_retries` times) with exponentially
increasing wait times.

### How backoff grows

On the first error the backoff delay is set to `backoff_base_s` (default
1 s).  Each subsequent error **multiplies** the delay by
`backoff_multiplier` (default 2.0), capped at `backoff_max_s` (default
300 s).

| Error | Backoff (defaults) |
|-------|--------------------|
| 1st   | 1 s                |
| 2nd   | 2 s                |
| 3rd   | 4 s                |
| 4th   | 8 s                |
| …     | doubles each time  |

### Gradual recovery

When a request to a domain **succeeds** (HTTP 2xx) and that domain has
active backoff, the backoff delay is **linearly decreased** by
`backoff_decay_s` (default 1 s).  When the delay reaches zero it is
cleared entirely.  This allows a domain to gradually return to full speed
after a transient outage.

### Interaction with inter-request delays

The backoff delay acts as a **lower bound** on the inter-request delay,
using the same override logic as Crawl-Delay.  If the backoff is larger
than the configured `delay_min_s`, it raises the floor (and ceiling if
necessary).  If no explicit delay is configured, the backoff is applied as
a fixed delay.

### Inspecting backoff state

```python
client = HttpClient()
# ... after some requests ...
for domain, delay in client.backoff_delays.items():
    print(f"{domain}: {delay:.1f}s")
```

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

## 403 Forbidden domain suppression

When `suppress_on_403=True` (the default) and a server responds with **403
Forbidden**, `HttpClient` records that domain in an internal blocklist.  All
subsequent requests to the same domain are immediately returned as a synthetic
403 response *without making a network request*.  This dramatically speeds up
bulk downloads where certain publishers consistently deny access.

### Exempt domains

Some domains — notably `doi.org` and `dx.doi.org` — act purely as redirectors.
A 403 from `doi.org` actually originates at the *destination* publisher, not at
`doi.org` itself.  These domains are **exempt** from suppression and will never
be blocklisted.

### Inspecting the blocklist

The `suppressed_domains` property returns a copy of the current blocklist:

```python
client = HttpClient()
# ... after some requests ...
for domain, reason in client.suppressed_domains.items():
    print(f"{domain}: {reason}")
```

### Disabling 403 suppression

Pass `suppress_on_403=False` or use the CLI flag `--no-suppress-403`:

```python
from pypaperretriever import PaperRetriever

retriever = PaperRetriever(
    email="you@example.com",
    doi="10.7759/cureus.76081",
    suppress_on_403=False,
)
```

```bash
pypaperretriever \
    --email you@example.com \
    --doi 10.7759/cureus.76081 \
    --no-suppress-403
```

---

## Using `HttpClient` directly

You can instantiate `HttpClient` on its own for custom workflows:

```python
from pypaperretriever import HttpClient

client = HttpClient(
    user_agent="MyBot/1.0",
    respect_robots_txt=True,
    delay_min_s=1.0,
    delay_max_s=3.0,
    honor_retry_after=True,
    max_retries=5,
    default_retry_after_s=30.0,
    max_retry_after_s=120.0,
    backoff_base_s=1.0,
    backoff_multiplier=2.0,
    backoff_max_s=120.0,
    backoff_decay_s=0.5,
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
| `polite_mode` | `bool` | `True` | Wrap all polite crawling features. If `False`, completely bypass robots.txt checks, rate limits, delays, and retry/suppression logic to prioritize raw access. |
| `user_agent` | `str` | `"PyPaperRetriever/1.0"` | Default `User-Agent` header and the name used for robots.txt look-ups. |
| `respect_robots_txt` | `bool` | `False` | Enable robots.txt checking and Crawl-Delay enforcement. |
| `delay_min_s` | `Optional[float]` | `None` | Minimum inter-request delay (seconds).  Setting this or `delay_max_s` enables per-domain delays.  If only `delay_max_s` is given, defaults to `0`. |
| `delay_max_s` | `Optional[float]` | `None` | Maximum inter-request delay (seconds).  If only `delay_min_s` is given, defaults to `3 × delay_min_s`. |
| `honor_retry_after` | `bool` | `True` | Automatically retry on HTTP 429 responses. |
| `suppress_on_403` | `bool` | `True` | Suppress domains that return 403 Forbidden. |
| `max_retries` | `int` | `3` | Maximum number of retries per request (applies to 429, 503, and 504). |
| `default_retry_after_s` | `float` | `60.0` | Fallback wait (seconds) when no rate-limit header is present. |
| `max_retry_after_s` | `float` | `300.0` | Upper clamp on the server-requested wait time. |
| `backoff_base_s` | `float` | `1.0` | Initial backoff delay (seconds) on first error. |
| `backoff_multiplier` | `float` | `2.0` | Multiplier applied to the backoff delay on each consecutive error. |
| `backoff_max_s` | `float` | `300.0` | Maximum backoff delay (seconds). |
| `backoff_decay_s` | `float` | `1.0` | Amount subtracted from the backoff delay on each successful (2xx) response. |
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
- If 403 suppression is enabled and the domain was previously blocked, a
  synthetic `requests.Response` with status 403 is returned (not `None`).

---

## Integration with other classes

`HttpClient` is used internally by all main classes:

| Class | How it's configured |
|-------|---------------------|
| `PaperRetriever` | Accepts `respect_robots_txt`, `suppress_on_403`, and `polite_mode` constructor parameters; creates its own `HttpClient`. |
| `PubMedSearcher` | Accepts `respect_robots_txt`; creates its own `HttpClient` and passes the flag through to `PaperRetriever` and `ReferenceRetriever`. |
| `ReferenceRetriever` | Accepts an optional `http_client` parameter. |
| `utils.doi_to_pmid()` | Accepts an optional `http_client` parameter for its PMC ID Converter fallback request. |

All classes benefit from 429 retry and 403 suppression automatically when `polite_mode` is `True` (enabled by default).

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
        - suppressed_domains
        - backoff_delays
      inherited_members: false
