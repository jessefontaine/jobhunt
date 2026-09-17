"""Generic scraper for institute / company vacancy pages, driven by CSS selectors in
config/sources.yaml:

    pages:
      - name: Spinoza Centre
        url: https://…/vacancies
        item: li.vacancy          # one element per vacancy (required)
        title: h3                 # optional; default: the item's text
        link: a                   # optional; default: first <a> in the item
        location: .loc            # optional
        deadline: .deadline       # optional; date is extracted from the element's text
        summary: p                # optional
        allow_empty: true         # optional; zero matches is not an error
      - name: Amsterdam UMC       # JS-only site: just link it in the digest header
        url: https://…
        manual: true
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from jobhunt.models import Listing
from jobhunt.sources.base import SourceResult

_DATE_PATTERNS = (
    (re.compile(r"(\d{4}-\d{2}-\d{2})"), "%Y-%m-%d"),
    (re.compile(r"(\d{1,2}-\d{1,2}-\d{4})"), "%d-%m-%Y"),
    (re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})"), "%d %B %Y"),
    (re.compile(r"(\d{1,2} [A-Za-z]{3} \d{4})"), "%d %b %Y"),
)


def extract_date(text: str) -> date | None:
    for pattern, fmt in _DATE_PATTERNS:
        if m := pattern.search(text):
            try:
                return datetime.strptime(m.group(1), fmt).date()
            except ValueError:
                continue
    return None


def _text(item: Tag, selector: str | None) -> str:
    node = item.select_one(selector) if selector else item
    return node.get_text(" ", strip=True) if node else ""


def parse_page(html: str, page: dict[str, Any]) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for item in soup.select(page["item"]):
        link_node = item.select_one(page.get("link") or "a")
        href = link_node.get("href") if link_node else None
        if not href:
            continue
        deadline_text = _text(item, page.get("deadline")) if page.get("deadline") else ""
        listings.append(
            Listing(
                source=PagesSource.name,
                title=_text(item, page.get("title")),
                employer=page["name"],
                url=urljoin(page["url"], href),
                location=_text(item, page["location"]) or None if page.get("location") else None,
                deadline=extract_date(deadline_text) if deadline_text else None,
                summary=_text(item, page["summary"]) if page.get("summary") else "",
            )
        )
    return listings


class PagesSource:
    name = "pages"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        result = SourceResult()
        seen: set[str] = set()
        for page in cfg.get("pages", []):
            if page.get("manual"):
                result.manual_urls[page["name"]] = page["url"]
                continue
            try:
                found = parse_page(http.get_text(page["url"]), page)
            except Exception as exc:
                result.errors.append(f"{page.get('name')}: {type(exc).__name__}: {exc}")
                continue
            if not found and not page.get("allow_empty"):
                result.errors.append(f"{page.get('name')}: no items matched {page.get('item')!r}")
            for lst in found:
                if lst.id not in seen:
                    seen.add(lst.id)
                    result.listings.append(lst)
        return result

    def fetch_detail(self, url: str, http: Any) -> str:
        soup = BeautifulSoup(http.get_text(url), "html.parser")
        for tag in soup(["script", "style", "svg", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        node = soup.select_one("main, article") or soup.body or soup
        return node.get_text("\n", strip=True)
