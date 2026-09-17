"""Indeed NL — manual only.

Indeed answers plain requests with a 403 and a CAPTCHA. Working around that would mean evading
bot detection, so this source never fetches: it only puts one saved-search link per query in the
digest header for you to open by hand.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from jobhunt.sources.base import SourceResult

SEARCH = "https://nl.indeed.com/jobs"


def search_url(query: str, location: str) -> str:
    return SEARCH + "?" + urlencode({"q": query, "l": location})


class IndeedSource:
    name = "indeed"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        location = cfg.get("location", "Nederland")
        return SourceResult(
            manual_urls={f"Indeed: {q}": search_url(q, location) for q in cfg.get("queries", [])}
        )
