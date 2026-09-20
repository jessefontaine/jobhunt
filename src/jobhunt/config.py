"""Project paths and config/sources.yaml loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "data" / "jobs.sqlite"

    @property
    def ratings(self) -> Path:
        return self.root / "data" / "ratings.jsonl"

    @property
    def digests(self) -> Path:
        return self.root / "digests"

    @property
    def shortlist(self) -> Path:
        return self.root / "shortlist.md"

    @property
    def profile(self) -> Path:
        return self.root / "profile" / "profile.md"

    @property
    def preferences(self) -> Path:
        return self.root / "profile" / "preferences.md"

    @property
    def cv(self) -> Path:
        return self.root / "docs" / "cv.md"

    @property
    def sources_yaml(self) -> Path:
        return self.root / "config" / "sources.yaml"


class ScoringConfig(BaseModel):
    model: str = "sonnet"
    batch_size: int = 10
    examples: int = 20


class DigestConfig(BaseModel):
    limit: int = 60  # entries per digest, best scores first


@dataclass
class Config:
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    sources: dict[str, Any] = field(default_factory=dict)
    contact: str | None = None  # goes into the scraper User-Agent

    def enabled_sources(self) -> list[str]:
        return [
            name
            for name, cfg in self.sources.items()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]


def find_root(start: Path | None = None) -> Path | None:
    """Nearest directory at or above `start` (default cwd) that contains config/sources.yaml."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if Paths(candidate).sources_yaml.exists():
            return candidate
    return None


def load_config(root: Path) -> Config:
    paths = Paths(root)
    if not paths.sources_yaml.exists():
        return Config()
    data = yaml.safe_load(paths.sources_yaml.read_text()) or {}
    contact = data.get("contact")
    return Config(
        scoring=ScoringConfig(**(data.get("scoring") or {})),
        digest=DigestConfig(**(data.get("digest") or {})),
        sources=data.get("sources") or {},
        contact=str(contact).strip() if contact else None,
    )
