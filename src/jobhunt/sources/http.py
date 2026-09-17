"""Shared polite HTTP client for scrapers."""

from __future__ import annotations

import httpx

USER_AGENT = "jobhunt/0.1 (personal job search)"


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,nl;q=0.8"},
        timeout=20.0,
        follow_redirects=True,
    )
