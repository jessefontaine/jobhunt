"""Render the ranked markdown digest that the user rates in."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jobhunt.models import Listing, Score

ROLE_LABELS = {
    "phd": "PhD",
    "postdoc": "Postdoc",
    "ra": "RA",
    "industry": "Industry",
    "other": "Other",
}


@dataclass
class RunInfo:
    new: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    manual: dict[str, str] = field(default_factory=dict)  # label -> url


def _meta_line(lst: Listing, score: Score | None) -> str:
    parts: list[str] = []
    if score is not None:
        parts.append(f"score {score.score}")
        parts.append(ROLE_LABELS.get(score.role_type, score.role_type))
    if lst.location:
        parts.append(lst.location)
    if lst.posted:
        parts.append(f"posted {lst.posted.isoformat()}")
    if lst.deadline:
        parts.append(f"deadline {lst.deadline.isoformat()}")
    parts.append(f"[{lst.source}]({lst.url})")
    return " · ".join(parts)


def _entry(heading: str, lst: Listing, score: Score | None) -> str:
    lines = [
        f"## {heading}",
        f"<!-- id: {lst.id} -->",
        _meta_line(lst, score),
    ]
    if score is not None:
        lines.append(f"**Why:** {score.why}")
        if score.concerns:
            lines.append(f"**Concerns:** {score.concerns}")
    lines += ["rating:", "note:", ""]
    return "\n".join(lines)


def render_digest(
    today: date,
    listings: list[Listing],
    scores: dict[str, Score],
    info: RunInfo,
) -> str:
    scored = [lst for lst in listings if lst.id in scores]
    unscored = [lst for lst in listings if lst.id not in scores]
    scored.sort(key=lambda lst: scores[lst.id].score, reverse=True)

    header = [f"New: {info.new}", f"Unscored: {len(unscored)}"]
    if info.errors:
        errs = ", ".join(f"{name} ({msg})" for name, msg in info.errors.items())
        header.append(f"Source errors: {errs}")
    if info.manual:
        links = ", ".join(f"[{label}]({url})" for label, url in info.manual.items())
        header.append(f"Manual: {links}")

    out = [f"# Job digest — {today.isoformat()}", " · ".join(header), ""]
    for i, lst in enumerate(scored, 1):
        out.append(_entry(f"{i}. {lst.title} — {lst.employer}", lst, scores[lst.id]))
    if unscored:
        out.append("## Unscored\n")
        for lst in unscored:
            out.append(_entry(f"{lst.title} — {lst.employer}", lst, None))
    return "\n".join(out)


def digest_path(digests_dir: Path, today: date) -> Path:
    """`digests/<today>.md`, or `-2`, `-3`, … if that file already exists."""
    digests_dir.mkdir(parents=True, exist_ok=True)
    candidate = digests_dir / f"{today.isoformat()}.md"
    n = 2
    while candidate.exists():
        candidate = digests_dir / f"{today.isoformat()}-{n}.md"
        n += 1
    return candidate


DIGEST_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")


def newest_digest(digests_dir: Path) -> Path | None:
    """Latest digest by (date, run number) — plain name sorting gets `-2` files wrong."""
    if not digests_dir.exists():
        return None
    best: tuple[tuple[str, int], Path] | None = None
    for path in digests_dir.iterdir():
        m = DIGEST_NAME_RE.match(path.name)
        if not m:
            continue
        key = (m.group(1), int(m.group(2) or 1))
        if best is None or key > best[0]:
            best = (key, path)
    return best[1] if best else None
