"""User settings: `config/settings.yaml`, what the Settings page writes.

`config/sources.yaml` says where to look; this says how the results are shown and how the
pipeline behaves. Workspaces made before 0.5.0 have no settings.yaml, so the scoring and
digest blocks are seeded from sources.yaml until the file is saved for the first time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from jobhunt.config import Config, ConfigError, DigestConfig, Paths, ScoringConfig

Theme = Literal["auto", "light", "dark"]
Sort = Literal["score", "deadline", "newest"]


class DisplaySettings(BaseModel):
    theme: Theme = "auto"
    min_score: int = Field(0, ge=0, le=100)
    max_score: int = Field(100, ge=0, le=100)
    sort: Sort = "score"
    deadline_soon_days: int = Field(14, ge=0)  # 0 turns the "closes soon" marker off

    @model_validator(mode="after")
    def _ordered(self) -> DisplaySettings:
        if self.min_score > self.max_score:
            raise ValueError("display.min_score must not be greater than display.max_score")
        return self

    def in_range(self, score: int | None) -> bool:
        """Unscored listings (`None`) count as in range only while the floor is 0."""
        if score is None:
            return self.min_score == 0
        return self.min_score <= score <= self.max_score

    def filtering(self) -> bool:
        return self.min_score > 0 or self.max_score < 100

    def closing_soon(self, deadline, today) -> bool:
        if not deadline or not self.deadline_soon_days:
            return False
        return 0 <= (deadline - today).days <= self.deadline_soon_days


class ShortlistSettings(BaseModel):
    min_rating: int = Field(4, ge=1, le=5)


class RatedSettings(BaseModel):
    """When a rating drops off the Rated page. `hide_below: 1` keeps everything."""

    hide_below: int = Field(3, ge=1, le=5)
    hide_after_days: int = Field(30, ge=0)

    def hidden(self, rating: int, rated_at: datetime, now: datetime) -> bool:
        """Hidden only when the rating is low *and* older than the window."""
        if rating >= self.hide_below:
            return False
        return (now - rated_at).days > self.hide_after_days


class PreferenceSettings(BaseModel):
    """Caps written into the preferences prompt so the rules condense instead of piling up."""

    max_rules: int = Field(10, ge=1, le=50)
    max_specifics: int = Field(8, ge=0, le=50)
    max_words_per_rule: int = Field(20, ge=5, le=100)


class UpdateSettings(BaseModel):
    check: bool = True
    interval_minutes: int = Field(10, ge=1)


class Settings(BaseModel):
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    shortlist: ShortlistSettings = Field(default_factory=ShortlistSettings)
    rated: RatedSettings = Field(default_factory=RatedSettings)
    preferences: PreferenceSettings = Field(default_factory=PreferenceSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)


HEADER = """\
# jobhunt settings. The Settings page in `jobhunt serve` writes this file; editing it by hand
# is fine too (comments outside this header are not preserved on save).
"""


def parse_settings(text: str) -> Settings:
    """Build Settings from the text of settings.yaml; raise ConfigError if unusable."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("the file must be a YAML mapping (display:, digest:, scoring:, …)")
    try:
        return Settings(**data)
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"invalid settings: {exc}") from exc


def load_settings(paths: Paths, config: Config) -> Settings:
    """Read settings.yaml, or seed the shared blocks from sources.yaml when it does not exist."""
    if paths.settings_yaml.exists():
        return parse_settings(paths.settings_yaml.read_text())
    return Settings(scoring=config.scoring, digest=config.digest)


def save_settings(paths: Paths, settings: Settings) -> Path:
    body = yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    paths.settings_yaml.parent.mkdir(parents=True, exist_ok=True)
    paths.settings_yaml.write_text(HEADER + body)
    return paths.settings_yaml
