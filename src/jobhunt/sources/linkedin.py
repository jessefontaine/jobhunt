"""LinkedIn public (guest) job search — best effort.

The unauthenticated `/jobs/search` page renders ~60 cards server-side. When LinkedIn blocks or
redirects to its sign-in wall, the search URL is handed to the digest as a manual link instead.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobhunt.models import Listing
from jobhunt.sources.base import SourceResult

SEARCH = "https://www.linkedin.com/jobs/search"
PAST_30_DAYS = "r2592000"


def search_url(keywords: str, location: str) -> str:
    return (
        SEARCH
        + "?"
        + urlencode({"keywords": keywords, "location": location, "f_TPR": PAST_30_DAYS})
    )


def parse_search(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for card in soup.select("div.base-search-card"):
        link = card.select_one("a.base-card__full-link")
        title = card.select_one("h3.base-search-card__title")
        if link is None or title is None or not link.get("href"):
            continue
        company = card.select_one("h4.base-search-card__subtitle")
        location = card.select_one("span.job-search-card__location")
        posted = card.select_one("time[datetime]")
        listings.append(
            Listing(
                source=LinkedInSource.name,
                title=title.get_text(" ", strip=True),
                employer=company.get_text(" ", strip=True) if company else "",
                url=link["href"].split("?", 1)[0],  # drop per-request tracking params
                location=location.get_text(" ", strip=True) if location else None,
                posted=date.fromisoformat(posted["datetime"]) if posted else None,
                raw={"urn": card.get("data-entity-urn", "")},
            )
        )
    return listings


def parse_detail(html: str) -> str:
    """Job description plus the criteria list; empty string if we hit the sign-in wall."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("div.show-more-less-html__markup")
    if body is None:
        return ""
    lines = [body.get_text("\n", strip=True)]
    for item in soup.select("li.description__job-criteria-item"):
        key = item.select_one("h3")
        value = item.select_one("span")
        if key and value:
            lines.append(f"{key.get_text(' ', strip=True)}: {value.get_text(' ', strip=True)}")
    return "\n".join(lines).strip()


class LinkedInSource:
    name = "linkedin"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        result = SourceResult()
        location = cfg.get("location", "Netherlands")
        seen: set[str] = set()
        for query in cfg.get("queries", []):
            url = search_url(query, location)
            try:
                cards = parse_search(http.get_text(url))
            except Exception as exc:
                cards = []
                result.errors.append(f"{query!r}: {type(exc).__name__}: {exc}")
            if not cards:
                if not any(query in e for e in result.errors):
                    result.errors.append(f"{query!r}: no cards (sign-in wall?)")
                result.manual_urls[f"LinkedIn: {query}"] = url
                continue
            for lst in cards:
                if lst.id not in seen:
                    seen.add(lst.id)
                    result.listings.append(lst)
        return result

    def fetch_detail(self, url: str, http: Any) -> str:
        return parse_detail(http.get_text(url))
