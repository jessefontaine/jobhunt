"""Compare Claude's scores against the ratings the user gave, so the two can be trusted
(or not) against each other. Pure functions over (score, rating) pairs — no Claude call."""

from __future__ import annotations

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
