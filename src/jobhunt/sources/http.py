"""Shared polite HTTP client for scrapers: fixed UA, minimum interval between requests."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

USER_AGENT = "jobhunt/0.1 (personal job search)"


class PoliteClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,nl;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def get_text(self, url: str) -> str:
        """GET `url`, raising httpx.HTTPStatusError on 4xx/5xx."""
        if self._last is not None:
            wait = self._min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        try:
            response = self._client.get(url)
        finally:
            self._last = self._clock()
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
