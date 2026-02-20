# HTTP Client & robots.txt Support

PyPaperRetriever includes an `HttpClient` that can optionally respect
[robots.txt](https://en.wikipedia.org/wiki/Robots.txt) directives and
per-domain **Crawl-Delay** values when fetching papers and metadata.

By default this behaviour is **off** — `HttpClient` acts as a thin wrapper
around `requests.get()` with zero delays. Opt in with `respect_robots_txt=True`
when you want to be a polite crawler.

## Quick example

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

From the CLI:

```bash
pypaperretriever \
    --email you@example.com \
    --doi 10.7759/cureus.76081 \
    --dwn-dir PDFs \
    --respect-robots-txt
```

## How it works

### robots.txt checking

When `respect_robots_txt=True`, every outgoing GET request is first checked
against the target domain's `robots.txt`.  If the URL is **disallowed** for the
configured User-Agent, `HttpClient.get()` returns `None` and the caller skips
that source.  If the `robots.txt` cannot be fetched (network error, 404, etc.)
the domain is treated as fully permissive.

Parsed `robots.txt` files are **cached per domain** for the lifetime of the
`HttpClient` instance — repeat requests to the same domain do not re-fetch
`robots.txt`.

### Crawl-Delay

If a domain's `robots.txt` specifies a `Crawl-Delay` for the active
User-Agent, `HttpClient` sleeps between requests to that domain so consecutive
fetches are at least `Crawl-Delay` seconds apart.  When no `Crawl-Delay` is
declared, a random fallback between `rate_limit_min_s` and `rate_limit_max_s`
(default 1–3 s) is used instead.

Delays are tracked **per domain** — a slow Crawl-Delay on one site does not
block requests to a different site.

## Using `HttpClient` directly

You can instantiate `HttpClient` on its own for custom workflows:

```python
from pypaperretriever import HttpClient

client = HttpClient(
    user_agent="MyBot/1.0",
    respect_robots_txt=True,
    rate_limit_min_s=1.0,
    rate_limit_max_s=3.0,
    verbosity=2,
)

response = client.get("https://api.example.com/data")
if response is None:
    print("Blocked by robots.txt")
else:
    print(response.status_code)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_agent` | `str` | `"PyPaperRetriever/1.0"` | Default `User-Agent` header and the name used for robots.txt look-ups. |
| `respect_robots_txt` | `bool` | `False` | Enable robots.txt checking and Crawl-Delay enforcement. |
| `rate_limit_min_s` | `float` | `1.0` | Minimum fallback delay (seconds) when no Crawl-Delay is declared. |
| `rate_limit_max_s` | `float` | `3.0` | Maximum fallback delay (seconds). |
| `verbosity` | `int` | `1` | Logging verbosity: 0 = silent, 1 = warnings, 2 = info, 3 = debug. |

### Return value of `get()`

`HttpClient.get()` returns `Optional[requests.Response]`:

- A normal `requests.Response` on success (any HTTP status code).
- `None` **only** when `respect_robots_txt=True` and the URL is disallowed by
  `robots.txt`. Callers must handle the `None` case.
- When `respect_robots_txt=False`, `get()` never returns `None`.

## Integration with other classes

`HttpClient` is used internally by all main classes:

| Class | How it's configured |
|-------|---------------------|
| `PaperRetriever` | Accepts `respect_robots_txt` constructor parameter; creates its own `HttpClient`. |
| `PubMedSearcher` | Accepts `respect_robots_txt`; creates its own `HttpClient` and passes the flag through to `PaperRetriever` and `ReferenceRetriever`. |
| `ReferenceRetriever` | Accepts an optional `http_client` parameter. |
| `utils.doi_to_pmid()` | Accepts an optional `http_client` parameter for its PMC ID Converter fallback request. |

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
