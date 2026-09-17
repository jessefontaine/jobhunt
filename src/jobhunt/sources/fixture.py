"""Loads listings from a JSON file. Used by tests and `jobhunt check --fixture`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jobhunt.models import Listing
from jobhunt.sources.base import SourceResult


class FixtureSource:
    name = "fixture"

    def fetch(self, cfg: dict[str, Any], http: Any) -> SourceResult:
        path = Path(cfg["path"])
        try:
            raw = json.loads(path.read_text())
        except OSError as exc:
            return SourceResult(errors=[f"cannot read {path}: {exc}"])
        listings = [Listing(source=self.name, **item) for item in raw]
        return SourceResult(listings=listings)
