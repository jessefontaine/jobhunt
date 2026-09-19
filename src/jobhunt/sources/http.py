"""Shared polite HTTP client for scrapers: configurable UA, minimum interval between requests."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

ATTEMPTS = 3


def _user_agent(contact: str | None) -> str:
    base = "jobhunt/0.1 (personal job search"
    return f"{base}; contact: {contact})" if contact else f"{base})"


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Retry-After (seconds) when the server sends one, else exponential backoff."""
    if isinstance(exc, httpx.HTTPStatusError):
        header = exc.response.headers.get("Retry-After", "")
        if header.isdigit():
            return float(header)
    return 2.0**attempt


class PoliteClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        contact: str | None = None,
    ):
        self.user_agent = _user_agent(contact)
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "en,nl;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self.attempts = ATTEMPTS

    def get_text(self, url: str) -> str:
        """GET `url`; retries 5xx/429/transport errors up to 3 attempts (2 s, 4 s backoff).

        Raises httpx.HTTPStatusError on a final 4xx/5xx.
        """
        for attempt in range(1, self.attempts + 1):
            self._throttle()
            try:
                response = self._client.get(url)
                self._last = self._clock()
                response.raise_for_status()
                return response.text
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                self._last = self._clock()
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                retryable = status is None or status >= 500 or status == 429
                if not retryable or attempt == self.attempts:
                    raise
                self._sleep(_retry_delay(exc, attempt))
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
