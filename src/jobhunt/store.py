"""SQLite persistence for listings, scores and ratings."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from jobhunt.models import Listing, Rating, Score

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    employer TEXT NOT NULL,
    url TEXT NOT NULL,
    location TEXT,
    posted TEXT,
    deadline TEXT,
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS scores (
    listing_id TEXT PRIMARY KEY REFERENCES listings(id),
    score INTEGER NOT NULL,
    role_type TEXT NOT NULL,
    area_tags TEXT NOT NULL,
    why TEXT NOT NULL,
    concerns TEXT NOT NULL,
    model TEXT NOT NULL,
    scored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
    listing_id TEXT PRIMARY KEY REFERENCES listings(id),
    rating INTEGER NOT NULL,
    note TEXT NOT NULL,
    digest TEXT NOT NULL,
    rated_at TEXT NOT NULL
);
"""


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- listings -----------------------------------------------------------

    def upsert_listings(self, listings: list[Listing]) -> set[str]:
        """Insert or update; return ids that were not present before."""
        new: set[str] = set()
        with self.conn:
            for lst in listings:
                exists = self.conn.execute(
                    "SELECT 1 FROM listings WHERE id = ?", (lst.id,)
                ).fetchone()
                if exists is None:
                    new.add(lst.id)
                self.conn.execute(
                    """
                    INSERT INTO listings
                        (id, source, title, employer, url, location, posted, deadline,
                         summary, description, fetched_at, raw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        employer = excluded.employer,
                        url = excluded.url,
                        location = excluded.location,
                        posted = excluded.posted,
                        deadline = excluded.deadline,
                        summary = excluded.summary,
                        description = CASE WHEN excluded.description = ''
                                           THEN listings.description
                                           ELSE excluded.description END,
                        fetched_at = excluded.fetched_at,
                        raw = excluded.raw
                    """,
                    (
                        lst.id,
                        lst.source,
                        lst.title,
                        lst.employer,
                        lst.url,
                        lst.location,
                        _iso(lst.posted),
                        _iso(lst.deadline),
                        lst.summary,
                        lst.description,
                        _iso(lst.fetched_at),
                        json.dumps(lst.raw),
                    ),
                )
        return new

    def set_description(self, listing_id: str, description: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE listings SET description = ? WHERE id = ?", (description, listing_id)
            )

    def count_listings(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def get_listing(self, listing_id: str) -> Listing | None:
        row = self.conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        return self._row_to_listing(row) if row else None

    def get_listings(self, ids: list[str]) -> list[Listing]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM listings WHERE id IN ({marks})", ids).fetchall()
        by_id = {r["id"]: self._row_to_listing(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def unscored_listings(self, today: date) -> list[Listing]:
        rows = self.conn.execute(
            """
            SELECT l.* FROM listings l
            LEFT JOIN scores s ON s.listing_id = l.id
            WHERE s.listing_id IS NULL
              AND (l.deadline IS NULL OR l.deadline >= ?)
            ORDER BY l.fetched_at DESC
            """,
            (today.isoformat(),),
        ).fetchall()
        return [self._row_to_listing(r) for r in rows]

    def unexpired_listings(self, today: date) -> list[Listing]:
        """Every listing whose deadline has not passed, scored or rated or not (for --rescore)."""
        rows = self.conn.execute(
            """
            SELECT * FROM listings
            WHERE deadline IS NULL OR deadline >= ?
            ORDER BY fetched_at DESC
            """,
            (today.isoformat(),),
        ).fetchall()
        return [self._row_to_listing(r) for r in rows]

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Listing:
        return Listing(
            id=row["id"],
            source=row["source"],
            title=row["title"],
            employer=row["employer"],
            url=row["url"],
            location=row["location"],
            posted=date.fromisoformat(row["posted"]) if row["posted"] else None,
            deadline=date.fromisoformat(row["deadline"]) if row["deadline"] else None,
            summary=row["summary"],
            description=row["description"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            raw=json.loads(row["raw"]),
        )

    # -- scores -------------------------------------------------------------

    def save_scores(self, scores: list[Score]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO scores
                    (listing_id, score, role_type, area_tags, why, concerns, model, scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.listing_id,
                        s.score,
                        s.role_type,
                        json.dumps(s.area_tags),
                        s.why,
                        s.concerns,
                        s.model,
                        _iso(s.scored_at),
                    )
                    for s in scores
                ],
            )

    def get_score(self, listing_id: str) -> Score | None:
        row = self.conn.execute(
            "SELECT * FROM scores WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        return self._row_to_score(row) if row else None

    def get_scores(self, ids: list[str]) -> dict[str, Score]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM scores WHERE listing_id IN ({marks})", ids
        ).fetchall()
        return {r["listing_id"]: self._row_to_score(r) for r in rows}

    @staticmethod
    def _row_to_score(row: sqlite3.Row) -> Score:
        return Score(
            listing_id=row["listing_id"],
            score=row["score"],
            role_type=row["role_type"],
            area_tags=json.loads(row["area_tags"]),
            why=row["why"],
            concerns=row["concerns"],
            model=row["model"],
            scored_at=datetime.fromisoformat(row["scored_at"]),
        )

    # -- ratings ------------------------------------------------------------

    def save_rating(self, rating: Rating) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO ratings (listing_id, rating, note, digest, rated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rating.listing_id,
                    rating.rating,
                    rating.note,
                    rating.digest,
                    _iso(rating.rated_at),
                ),
            )

    def get_rating(self, listing_id: str) -> Rating | None:
        row = self.conn.execute(
            "SELECT * FROM ratings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        return self._row_to_rating(row) if row else None

    def rated_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT listing_id FROM ratings")}

    def rated_examples(self, limit: int) -> list[tuple[Listing, Rating]]:
        """Strong signals (rating >= 4 or <= 2), most recent first, for few-shot prompting."""
        rows = self.conn.execute(
            """
            SELECT l.*, r.rating AS r_rating, r.note AS r_note, r.digest AS r_digest,
                   r.rated_at AS r_rated_at
            FROM ratings r JOIN listings l ON l.id = r.listing_id
            WHERE r.rating >= 4 OR r.rating <= 2
            ORDER BY r.rated_at DESC, l.rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            rating = Rating(
                listing_id=row["id"],
                rating=row["r_rating"],
                note=row["r_note"],
                digest=row["r_digest"],
                rated_at=datetime.fromisoformat(row["r_rated_at"]),
            )
            out.append((self._row_to_listing(row), rating))
        return out

    @staticmethod
    def _row_to_rating(row: sqlite3.Row) -> Rating:
        return Rating(
            listing_id=row["listing_id"],
            rating=row["rating"],
            note=row["note"],
            digest=row["digest"],
            rated_at=datetime.fromisoformat(row["rated_at"]),
        )

    def candidate_listings(self, today: date) -> list[Listing]:
        """Unexpired listings the user has not rated yet — what a digest shows."""
        rows = self.conn.execute(
            """
            SELECT l.* FROM listings l
            LEFT JOIN ratings r ON r.listing_id = l.id
            WHERE r.listing_id IS NULL
              AND (l.deadline IS NULL OR l.deadline >= ?)
            ORDER BY l.fetched_at DESC
            """,
            (today.isoformat(),),
        ).fetchall()
        return [self._row_to_listing(r) for r in rows]

    def all_ratings(self) -> list[tuple[Listing, Rating]]:
        """Every rated listing, most recent rating first."""
        rows = self.conn.execute(
            """
            SELECT l.*, r.rating AS r_rating, r.note AS r_note, r.digest AS r_digest,
                   r.rated_at AS r_rated_at
            FROM ratings r JOIN listings l ON l.id = r.listing_id
            ORDER BY r.rated_at DESC, l.rowid DESC
            """
        ).fetchall()
        return [(self._row_to_listing(row), self._row_to_joined_rating(row)) for row in rows]

    @staticmethod
    def _row_to_joined_rating(row: sqlite3.Row) -> Rating:
        return Rating(
            listing_id=row["id"],
            rating=row["r_rating"],
            note=row["r_note"],
            digest=row["r_digest"],
            rated_at=datetime.fromisoformat(row["r_rated_at"]),
        )
