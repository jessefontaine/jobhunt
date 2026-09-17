"""EURAXESS — the EU research-jobs portal, filtered to one country.

Server-rendered Drupal pages, 10 results per `page=N`. The country facet is
`f[0]=job_country:<id>` (Netherlands = 798). As of 2026-09 the site sometimes ignores its own
filters, so results are additionally filtered by the country named in the Work Locations line.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from jobhunt.models import Listing
from jobhunt.sources.base import SourceResult

BASE = "https://euraxess.ec.europa.eu"
NETHERLANDS_ID = 798
DEFAULT_PAGES = 5
DETAIL_SECTIONS = ("job-information", "offer-description", "requirements", "additional-information")

_DEADLINE_RE = re.compile(r"(\d{1,2} [A-Za-z]{3} \d{4})")
_POSTED_RE = re.compile(r"Posted on:\s*(\d{1,2} [A-Za-z]+ \d{4})")


def search_url(keywords: str, country_id: int = NETHERLANDS_ID, page: int = 0) -> str:
    params = {"keywords": keywords, "f[0]": f"job_country:{country_id}", "page": page}
    return f"{BASE}/jobs/search?{urlencode(params)}"


def parse_deadline(text: str) -> date | None:
    m = _DEADLINE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %b %Y").date()
    except ValueError:
        return None


def _posted(card: Tag) -> date | None:
    m = _POSTED_RE.search(card.get_text(" ", strip=True))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %B %Y").date()
    except ValueError:
        return None


def _country(card: Tag) -> str | None:
    """`Number of offers: 1, Spain, VHIR, …` → `Spain`."""
    loc = card.select_one(".id-Work-Locations .ecl-text-standard")
    if loc is None:
        return None
    parts = [p.strip() for p in loc.get_text(" ", strip=True).split(",")]
    parts = [p for p in parts if p and not p.lower().startswith("number of offers")]
    return parts[0] if parts else None


def _links(card: Tag, selector: str) -> list[str]:
    return [a.get_text(" ", strip=True) for a in card.select(selector)]


def parse_search(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for card in soup.select("div.ecl-content-item__content-block"):
        title = card.select_one("h3 a")
        if title is None or not title.get("href"):
            continue
        org = card.select_one("ul.ecl-content-block__primary-meta-container li a")
        desc = card.select_one("div.ecl-content-block__description")
        deadline_block = card.select_one(".id-Application-Deadline")
        listings.append(
            Listing(
                source=EuraxessSource.name,
                title=title.get_text(" ", strip=True),
                employer=org.get_text(" ", strip=True) if org else "",
                url=BASE + title["href"],
                location=_country(card),
                posted=_posted(card),
                deadline=parse_deadline(deadline_block.get_text(" ", strip=True))
                if deadline_block
                else None,
                summary=desc.get_text(" ", strip=True) if desc else "",
                raw={
                    "research_field": _links(card, ".id-Research-Field a"),
                    "profile": _links(card, ".id-Researcher-Profile a"),
                },
            )
        )
    return listings


def parse_detail(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "svg", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    parts: list[str] = []
    for section_id in DETAIL_SECTIONS:
        heading = soup.find("h2", id=section_id)
        if heading is None:
            continue
        container = heading.parent
        title = heading.get_text(" ", strip=True)
        heading.extract()
        parts.append(f"## {title}\n{container.get_text('\n', strip=True)}")
    return "\n\n".join(parts).strip()


class EuraxessSource:
    name = "euraxess"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        result = SourceResult()
        country = cfg.get("country", "Netherlands")
        country_id = int(cfg.get("country_id", NETHERLANDS_ID))
        max_pages = int(cfg.get("pages", DEFAULT_PAGES))
        seen: set[str] = set()
        for keyword in cfg.get("keywords", []):
            for page in range(max_pages):
                url = search_url(keyword, country_id, page)
                try:
                    cards = parse_search(http.get_text(url))
                except Exception as exc:
                    result.errors.append(f"{keyword!r} page {page}: {type(exc).__name__}: {exc}")
                    break
                if not cards:
                    break
                for lst in cards:
                    if lst.location == country and lst.id not in seen:
                        seen.add(lst.id)
                        result.listings.append(lst)
        return result

    def fetch_detail(self, url: str, http: Any) -> str:
        return parse_detail(http.get_text(url))
