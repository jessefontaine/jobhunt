"""The `manual` source: one listing built from a link the user pasted.

Not in the source registry — nothing polls it. `jobhunt add URL` (and the Add a link form)
fetch the page once, guess the few fields a listing needs, and store the result like any
other listing so it can be scored, ranked and rated. When the site refuses the request the
fields can be supplied by hand instead; nothing here tries to get around a block.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from jobhunt.models import Listing

NAME = "manual"
SUMMARY_LIMIT = 300
DESCRIPTION_LIMIT = 20000
DROP_TAGS = ("script", "style", "nav", "header", "footer", "form", "noscript", "aside")
TITLE_SEPARATORS = ("|", "·", "—", " - ")


class ManualFetchError(Exception):
    """The page could not be read or made sense of; the message is shown to the user."""


def _clean_title(raw: str) -> str:
    """`Job title | Careers | Employer` → `Job title`: sites append their own name."""
    for separator in TITLE_SEPARATORS:
        if separator in raw:
            return raw.split(separator)[0].strip()
    return raw.strip()


def _employer(soup: BeautifulSoup, url: str) -> str:
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and meta.get("content", "").strip():
        return meta["content"].strip()
    return urlparse(url).netloc.removeprefix("www.")


def _body_text(soup: BeautifulSoup) -> str:
    for tag in soup(list(DROP_TAGS)):
        tag.decompose()
    node = soup.find("main") or soup.find("article") or soup.body or soup
    return node.get_text(" ", strip=True)[:DESCRIPTION_LIMIT]


def parse_listing(url: str, html: str) -> Listing:
    """Build a `manual` Listing from a vacancy page. Raises ManualFetchError if there is no
    title to go on — a page that generic is almost always a sign-in wall or an error page."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    raw_title = heading.get_text(" ", strip=True) if heading else ""
    if not raw_title and soup.title:
        raw_title = _clean_title(soup.title.get_text(" ", strip=True))
    if not raw_title:
        raise ManualFetchError(
            f"no title found at {url} — pass --title and --employer to add it by hand"
        )
    employer = _employer(soup, url)
    description = _body_text(soup)
    return Listing(
        source=NAME,
        title=raw_title,
        employer=employer,
        url=url,
        summary=description[:SUMMARY_LIMIT],
        description=description,
    )


def fetch_listing(url: str, http) -> Listing:
    """GET `url` through the polite client and parse it."""
    try:
        html = http.get_text(url)
    except httpx.HTTPStatusError as exc:
        raise ManualFetchError(
            f"{url} returned {exc.response.status_code} — the site does not allow this. "
            "Open it yourself and add it with --title, --employer and --description."
        ) from None
    except httpx.TransportError as exc:
        raise ManualFetchError(f"could not reach {url}: {exc}") from None
    return parse_listing(url, html)


def manual_listing(
    url: str, title: str = "", employer: str = "", description: str = ""
) -> Listing:
    """A listing from fields the user supplied, for a page that cannot be fetched."""
    if not title:
        raise ManualFetchError("--title is required when the page is not fetched")
    return Listing(
        source=NAME,
        title=title,
        employer=employer or urlparse(url).netloc.removeprefix("www."),
        url=url,
        summary=description[:SUMMARY_LIMIT],
        description=description,
    )
