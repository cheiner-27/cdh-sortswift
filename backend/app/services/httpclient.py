"""Shared HTTP client for third-party catalog/price APIs.

Every external request goes through here so we stay a good neighbor to the
services we depend on. Two hard requirements are baked in:

- **User-Agent** — TCGcsv blocks requests with a generic or missing
  User-Agent, and Scryfall asks for a descriptive one on every request
  (including image downloads). We always send ``USER_AGENT``.
- **Rate limiting** — TCGcsv throttles (10-minute IP ban) clients that
  exceed ~10 req/s and asks for a 100 ms sleep in update loops; Scryfall
  asks for 50-100 ms between requests. A ``min_interval`` enforces this
  across all calls made through a single client instance.
"""
import time

import httpx

USER_AGENT = "cdh-sortswift/1.0"


class RateLimitedClient:
    """Thin ``httpx.Client`` wrapper enforcing a minimum inter-request delay
    and a descriptive User-Agent. Supports the small surface we use:
    ``get`` and ``stream``."""

    def __init__(self, *, min_interval: float = 0.1, timeout: float | None = 60.0,
                 headers: dict | None = None):
        merged = {"User-Agent": USER_AGENT}
        if headers:
            merged.update(headers)
        self._client = httpx.Client(timeout=timeout, follow_redirects=True,
                                    headers=merged)
        self._min_interval = min_interval
        self._last = 0.0

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, *args, **kwargs):
        self._throttle()
        try:
            return self._client.get(*args, **kwargs)
        finally:
            self._last = time.monotonic()

    def stream(self, *args, **kwargs):
        self._throttle()
        self._last = time.monotonic()
        return self._client.stream(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()


def client(*, min_interval: float = 0.1, timeout: float | None = 60.0,
           headers: dict | None = None) -> RateLimitedClient:
    return RateLimitedClient(min_interval=min_interval, timeout=timeout,
                             headers=headers)
