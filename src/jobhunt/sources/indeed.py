"""Indeed — manual only.

Indeed answers plain requests with a 403 and a CAPTCHA. Working around that would mean evading
bot detection, so this source never fetches: it only puts one saved-search link per query in the
digest header for you to open by hand. Config: queries, location, domain (e.g. nl.indeed.com).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from jobhunt.sources.base import SourceResult

DEFAULT_DOMAIN = "www.indeed.com"


def search_url(query: str, location: str, domain: str = DEFAULT_DOMAIN) -> str:
    return f"https://{domain}/jobs?" + urlencode({"q": query, "l": location})


class IndeedSource:
    name = "indeed"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        location = cfg.get("location", "")
        domain = cfg.get("domain", DEFAULT_DOMAIN)
        return SourceResult(
            manual_urls={
                f"Indeed: {q}": search_url(q, location, domain) for q in cfg.get("queries", [])
            }
        )
