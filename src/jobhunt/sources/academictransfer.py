"""AcademicTransfer — the national portal for Dutch academic vacancies.

Server-rendered search page, 10 newest results per query when sorted by `order=published`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from jobhunt.models import Listing
from jobhunt.sources.base import SourceResult

BASE = "https://www.academictransfer.com"
SEARCH = f"{BASE}/en/jobs/"
_DATE_RE = re.compile(r"^(\d{1,2}) ([A-Za-z]{3}) [’'](\d{2})$")


def parse_date(text: str, today: date) -> date | None:
    """AcademicTransfer prints `26 Oct ’26`, or `today` / `yesterday` for fresh posts."""
    text = text.strip()
    if text.lower() == "today":
        return today
    if text.lower() == "yesterday":
        return today - timedelta(days=1)
    m = _DATE_RE.match(text)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} 20{m.group(3)}", "%d %b %Y").date()
    except ValueError:
        return None


def _card_dates(card: Tag, today: date) -> tuple[date | None, date | None]:
    deadline = posted = None
    for span in card.select("span"):
        text = span.get_text(" ", strip=True)
        if text.startswith("Deadline "):
            deadline = parse_date(text.removeprefix("Deadline "), today)
        elif text.startswith("Published "):
            posted = parse_date(text.removeprefix("Published "), today)
    return deadline, posted


def parse_search(html: str, today: date) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for card in soup.select("ul[name=list] > li > article"):
        link = card.select_one("a[href^='/en/jobs/']")
        title = card.select_one("h3")
        if link is None or title is None:
            continue
        spans = [s.get_text(" ", strip=True) for s in card.select("span")]
        spans = [s for s in spans if s and not s.startswith(("Deadline ", "Published "))]
        # Remaining plain spans are `location`, `employer` in that order.
        location = spans[0] if len(spans) > 0 else None
        employer = spans[1] if len(spans) > 1 else ""
        deadline, posted = _card_dates(card, today)
        excerpt = card.select_one("p")
        listings.append(
            Listing(
                source=AcademicTransferSource.name,
                title=title.get_text(" ", strip=True),
                employer=employer,
                location=location,
                url=BASE + link["href"],
                deadline=deadline,
                posted=posted,
                summary=excerpt.get_text(" ", strip=True) if excerpt else "",
            )
        )
    return listings


def parse_detail(html: str) -> str:
    """Metadata (`Research fields: …`) plus the headed sections of the vacancy body."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    h1 = soup.select_one("h1")
    main = h1.find_parent("section") if h1 else None
    if main is None:
        return soup.get_text("\n", strip=True)

    lines: list[str] = []
    meta = main.find("section", recursive=False)
    if meta is not None:
        for block in meta.find_all("div", recursive=True):
            ps = block.find_all("p", recursive=False)
            if len(ps) >= 2:
                key = ps[0].get_text(" ", strip=True)
                value = " / ".join(p.get_text(" ", strip=True) for p in ps[1:])
                lines.append(f"{key}: {value}")
    for section in main.select("div > section"):
        heading = section.find("h2")
        if heading is None:
            continue
        lines.append("")
        lines.append(f"## {heading.get_text(' ', strip=True)}")
        heading.extract()
        lines.append(section.get_text("\n", strip=True))
    return "\n".join(lines).strip()


class AcademicTransferSource:
    name = "academictransfer"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        result = SourceResult()
        seen: set[str] = set()
        today = date.today()
        for query in cfg.get("queries", []):
            url = SEARCH + "?" + urlencode({"q": query, "order": "published"})
            try:
                html = http.get_text(url)
            except Exception as exc:
                result.errors.append(f"query {query!r}: {type(exc).__name__}: {exc}")
                continue
            for lst in parse_search(html, today):
                if lst.id not in seen:
                    seen.add(lst.id)
                    result.listings.append(lst)
        return result

    def fetch_detail(self, url: str, http: Any) -> str:
        return parse_detail(http.get_text(url))
