"""Source protocol shared by every job site scraper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from jobhunt.models import Listing


@dataclass
class SourceResult:
    listings: list[Listing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    manual_urls: dict[str, str] = field(default_factory=dict)  # label -> url to check by hand


class Source(Protocol):
    name: str

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        """Never raises: problems go into SourceResult.errors."""
        ...
