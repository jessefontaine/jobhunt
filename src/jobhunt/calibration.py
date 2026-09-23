"""Compare Claude's scores against the ratings the user gave, so the two can be trusted
(or not) against each other. Pure functions over (score, rating) pairs — no Claude call."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# The bands the scoring prompt describes, highest first.
BANDS: tuple[tuple[str, int, int], ...] = (
    ("apply", 85, 100),
    ("strong", 65, 84),
    ("maybe", 40, 64),
    ("weak", 15, 39),
    ("irrelevant", 0, 14),
)

# Below this many ratings the correlation is noise, and the report says so.
ENOUGH_RATINGS = 10


def expected_rating(score: int) -> int:
    """The rating this score predicts, by the band it falls in: apply → 5 … irrelevant → 1."""
    for n, (_, low, high) in enumerate(BANDS):
        if low <= score <= high:
            return len(BANDS) - n
    return 5 if score > BANDS[0][2] else 1


def surprise(score: int, rating: int) -> int:
    """How far the rating landed from the band's prediction — 0 when they agree, 4 at worst."""
    return abs(expected_rating(score) - rating)


def effective_n(ratings: list[int]) -> int:
    """How many listings this sample behaves like, once tied ratings are discounted.

    Spearman reads pairs, and a pair of listings you rated the same says nothing about the
    ranking. Rating a job hunt is lopsided — most listings are a 1 — so `n` badly overstates
    how much a correlation rests on. This is the size of an untied sample with the same number
    of informative pairs.
    """
    counts: dict[int, int] = {}
    for rating in ratings:
        counts[rating] = counts.get(rating, 0) + 1
    n = len(ratings)
    informative = n * (n - 1) // 2 - sum(k * (k - 1) // 2 for k in counts.values())
    if informative <= 0:
        return 0
    return round((1 + (1 + 8 * informative) ** 0.5) / 2)


# Spearman's z is slightly wider than Pearson's; the usual correction for it.
_Z_SPREAD = 1.03
_Z_95 = 1.96


def rho_interval(rho: float | None, n: int) -> tuple[float, float] | None:
    """A rough 95% interval for a rank correlation measured on `n` listings.

    Fisher's transform, so the interval is asymmetric near ±1 and never leaves [-1, 1]. Pass
    `effective_n` rather than the raw count: with ties the raw count flatters the estimate.
    """
    if rho is None or n <= 3:
        return None
    z = math.atanh(max(min(rho, 0.999999), -0.999999))
    spread = _Z_95 * _Z_SPREAD / math.sqrt(n - 3)
    return (math.tanh(z - spread), math.tanh(z + spread))


def mean_surprise(pairs: list[tuple[int, int]]) -> float:
    """Average gap between the band a score promised and the rating it got. 0 when empty."""
    return sum(surprise(s, r) for s, r in pairs) / len(pairs) if pairs else 0.0


def _ranks(values: list[float]) -> list[float]:
    """Ranks from 1 up, tied values sharing the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation of the pairs, or None when it is not defined (fewer than two
    pairs, or one side that never varies)."""
    if len(pairs) < 2:
        return None
    xs = _ranks([p[0] for p in pairs])
    ys = _ranks([p[1] for p in pairs])
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    var_x = sum(d * d for d in dx)
    var_y = sum(d * d for d in dy)
    if var_x == 0 or var_y == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / (var_x * var_y) ** 0.5


@dataclass
class Band:
    """One slice of the 0-100 scale and how the user rated what landed in it."""

    label: str
    low: int
    high: int
    ratings: list[int] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.ratings)

    @property
    def mean_rating(self) -> float | None:
        return sum(self.ratings) / len(self.ratings) if self.ratings else None


@dataclass
class Agreement:
    """How Claude's scores and the user's ratings line up."""

    n: int
    rho: float | None
    bands: list[Band]
    unscored: int = 0


def agreement(pairs: list[tuple[int, int]], unscored: int = 0) -> Agreement:
    """Summarise (score, rating) pairs: rank correlation plus a mean rating per band.

    `unscored` is how many rated listings had no score to pair with.
    """
    bands = [Band(label, low, high) for label, low, high in BANDS]
    for score, rating in pairs:
        for band in bands:
            if band.low <= score <= band.high:
                band.ratings.append(rating)
                break
    return Agreement(
        n=len(pairs),
        rho=spearman(pairs),
        bands=bands,
        unscored=unscored,
    )


def verdict(rho: float | None) -> str:
    """One sentence on what a rank correlation means for the scoring pipeline."""
    if rho is None:
        return "no correlation to compute — the scores or the ratings never vary."
    if rho >= 0.7:
        return "strong: the ranking broadly matches how you rate."
    if rho >= 0.4:
        return "moderate: the ranking is useful but the cut-off is worth a look."
    if rho >= 0.2:
        return "weak: the scores barely track your ratings."
    if rho > -0.2:
        return "none: these scores tell you nothing about how you would rate a listing."
    return "inverted: the scores point the wrong way; check the profile and preferences."


def _listings(n: int) -> str:
    return f"{n} rated listing" + ("" if n == 1 else "s")


def render(result: Agreement) -> str:
    """The plain-text report `jobhunt calibration` prints."""
    lines = [f"Claude's scores vs your ratings — {_listings(result.n)}", ""]
    if not result.n:
        lines.append(
            f"{_listings(result.unscored)} had no score, so there is nothing to compare yet."
            if result.unscored
            else "Rate some listings first: a digest, then `jobhunt rate`."
        )
        return "\n".join(lines)
    shown = "n/a" if result.rho is None else f"{result.rho:.2f}"
    lines.append(f"Spearman rank correlation: {shown} — {verdict(result.rho)}")
    if result.n < ENOUGH_RATINGS:
        lines.append(
            f"Too few ratings to read much into this — {ENOUGH_RATINGS}+ makes the number mean "
            "something."
        )
    filled = [b for b in result.bands if b.n]
    if filled:
        lines.append("")
        for band in filled:
            span = f"{band.low}-{band.high}"
            lines.append(
                f"  {span:>7}  {band.label:<11} {band.n:>3}   mean rating {band.mean_rating:.1f}"
            )
    if result.unscored:
        lines += ["", f"Left out: {_listings(result.unscored)} with no score."]
    return "\n".join(lines)
