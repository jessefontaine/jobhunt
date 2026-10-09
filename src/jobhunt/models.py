"""Core data types: Listing, Score, Rating, StatusEvent, DiscardEvent."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

RoleType = Literal["phd", "postdoc", "ra", "industry", "other"]
# Where an application stands. `none` clears a status set by mistake.
Status = Literal["none", "applied", "interview", "offer", "rejected", "withdrawn"]
STATUSES: tuple[str, ...] = get_args(Status)


def canonical_url(url: str) -> str:
    """Strip fragment and trailing slash so cosmetic URL variants share an id."""
    url = url.split("#", 1)[0]
    return url.rstrip("/")


def make_id(source: str, url: str) -> str:
    digest = hashlib.sha1(f"{source}|{canonical_url(url)}".encode()).hexdigest()
    return digest[:12]


class Listing(BaseModel):
    id: str = ""
    source: str
    title: str
    employer: str
    url: str
    location: str | None = None
    posted: date | None = None
    deadline: date | None = None
    summary: str = ""
    description: str = ""
    fetched_at: datetime = Field(default_factory=datetime.now)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_id(self) -> Listing:
        if not self.id:
            self.id = make_id(self.source, self.url)
        return self

    def is_expired(self, today: date) -> bool:
        return self.deadline is not None and self.deadline < today


class Score(BaseModel):
    listing_id: str
    score: int = Field(ge=0, le=100)
    role_type: RoleType = "other"
    area_tags: list[str] = Field(default_factory=list)
    why: str = ""
    concerns: str = ""
    model: str = ""
    scored_at: datetime = Field(default_factory=datetime.now)
    # Why this person cannot apply at all (a degree or level they lack), or "". Not stored with
    # the score: scoring turns it into a DiscardEvent, which is where the reason is kept.
    ineligible: str = ""


class Rating(BaseModel):
    listing_id: str
    rating: int = Field(ge=1, le=5)
    note: str = ""
    digest: str = ""
    rated_at: datetime = Field(default_factory=datetime.now)


class DiscardEvent(BaseModel):
    """A listing put on the discard pile, or taken off it.

    A discarded listing stays in the store, so a fetch that sees it again does not bring it
    back, but it leaves the queue, scoring, calibration and learning: it says the person cannot
    apply, not what they like. Its rating, if any, is kept for when it is restored.
    """

    listing_id: str
    discarded: bool = True  # False: restored, and scoring may not discard it again
    reason: str = ""
    by: Literal["scoring", "user"] = "user"
    at: datetime = Field(default_factory=datetime.now)


class StatusEvent(BaseModel):
    """One step of an application, kept apart from the rating: `learn` never reads it."""

    listing_id: str
    status: Status
    on: date = Field(default_factory=date.today)  # when it happened, as the user says
    note: str = ""
    at: datetime = Field(default_factory=datetime.now)  # when it was recorded
