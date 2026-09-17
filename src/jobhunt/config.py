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


@dataclass
class Config:
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    sources: dict[str, Any] = field(default_factory=dict)

    def enabled_sources(self) -> list[str]:
        return [
            name
            for name, cfg in self.sources.items()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) to the directory containing pyproject.toml."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return here


def load_config(root: Path) -> Config:
    paths = Paths(root)
    if not paths.sources_yaml.exists():
        return Config()
    data = yaml.safe_load(paths.sources_yaml.read_text()) or {}
    return Config(
        scoring=ScoringConfig(**(data.get("scoring") or {})),
        sources=data.get("sources") or {},
    )
