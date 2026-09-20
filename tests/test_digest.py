from datetime import date

from jobhunt.digest import RunInfo, render_digest, render_shortlist
from jobhunt.models import Listing, Rating, Score
from jobhunt.ratings import parse_digest


def L(n, **kw):
    base = dict(
        source="academictransfer", title=f"Job {n}", employer=f"Uni {n}", url=f"https://x.org/j/{n}"
    )
    base.update(kw)
    return Listing(**base)


def test_render_orders_by_score_and_puts_unscored_last():
    l1, l2, l3 = L(1), L(2), L(3)
    scores = {
        l1.id: Score(listing_id=l1.id, score=40, role_type="ra", why="ok", concerns=""),
        l2.id: Score(listing_id=l2.id, score=90, role_type="phd", why="great", concerns="dutch"),
    }
    md = render_digest(date(2026, 9, 17), [l1, l2, l3], scores, RunInfo())
    assert md.index("## 1. Job 2") < md.index("## 2. Job 1") < md.index("## Unscored")
    assert md.index("## Unscored") < md.index("Job 3")


def test_render_entry_format_exact():
    lst = L(
        1,
        location="Nijmegen",
        posted=date(2026, 9, 10),
        deadline=date(2026, 10, 15),
        title="PhD: Visual cortex",
        employer="Donders Institute",
    )
    score = Score(
        listing_id=lst.id, score=84, role_type="phd", why="fMRI + DNN", concerns="Dutch teaching"
    )
    md = render_digest(date(2026, 9, 17), [lst], {lst.id: score}, RunInfo(new=1))
    expected = f"""# Job digest — 2026-09-17
New: 1 · Unscored: 0

## 1. PhD: Visual cortex — Donders Institute
<!-- id: {lst.id} -->
score 84 · PhD · Nijmegen · posted 2026-09-10 · deadline 2026-10-15 · [academictransfer](https://x.org/j/1)
**Why:** fMRI + DNN
**Concerns:** Dutch teaching
rating:
note:
"""
    assert md == expected


def test_render_omits_missing_optional_fields_and_concerns():
    lst = L(1)
    score = Score(listing_id=lst.id, score=10, role_type="other", why="no")
    md = render_digest(date(2026, 9, 17), [lst], {lst.id: score}, RunInfo())
    assert "score 10 · Other · [academictransfer](https://x.org/j/1)\n" in md
    assert "**Concerns:**" not in md
    assert "posted" not in md and "deadline" not in md


def test_render_header_lists_errors_and_manual_urls():
    info = RunInfo(
        new=0,
        errors={"euraxess": "timeout"},
        manual={"Indeed search": "https://nl.indeed.com/jobs?q=neuro"},
    )
    md = render_digest(date(2026, 9, 17), [], {}, info)
    assert "Source errors: euraxess (timeout)" in md
    assert "Manual: [Indeed search](https://nl.indeed.com/jobs?q=neuro)" in md
    assert "## Unscored" not in md


def test_unscored_entries_have_id_and_rating_lines():
    lst = L(7, deadline=date(2026, 12, 1))
    md = render_digest(date(2026, 9, 17), [lst], {}, RunInfo())
    block = md.split("## Unscored\n", 1)[1]
    assert f"<!-- id: {lst.id} -->" in block
    assert "rating:\nnote:\n" in block
    assert "deadline 2026-12-01" in block


def test_render_shortlist_line_format_exact():
    lst = L(1, title="PhD: Visual cortex", employer="Donders", deadline=date(2026, 10, 15))
    items = [
        (lst, Rating(listing_id=lst.id, rating=5, note="ask about start date")),
        (L(2), Rating(listing_id=L(2).id, rating=4)),
    ]
    assert render_shortlist(items) == (
        "- **PhD: Visual cortex** — Donders · 5/5 · deadline 2026-10-15"
        " · [academictransfer](https://x.org/j/1) · note: ask about start date\n"
        "- **Job 2** — Uni 2 · 4/5 · [academictransfer](https://x.org/j/2)"
    )


def test_render_digest_puts_shortlist_after_header_and_hides_it_from_rate():
    l1, l2 = L(1), L(2)
    scores = {l1.id: Score(listing_id=l1.id, score=40, role_type="ra", why="ok")}
    shortlist = [(l2, Rating(listing_id=l2.id, rating=5, note="yes"))]
    md = render_digest(date(2026, 9, 17), [l1], scores, RunInfo(), shortlist=shortlist)
    assert md.index("New: 0") < md.index("## Shortlist") < md.index("## 1. Job 1")
    assert "- **Job 2** — Uni 2 · 5/5" in md
    parsed, errors = parse_digest(md.replace("rating:", "rating: 3"))
    assert errors == []
    assert [pid for pid, _, _ in parsed] == [l1.id]


def test_render_digest_omits_shortlist_when_empty():
    md = render_digest(date(2026, 9, 17), [L(1)], {}, RunInfo(), shortlist=[])
    assert "Shortlist" not in md
    assert "Shortlist" not in render_digest(date(2026, 9, 17), [L(1)], {}, RunInfo())


def test_digest_path_suffixes_when_file_exists(tmp_path):
    from jobhunt.digest import digest_path

    d = date(2026, 9, 17)
    first = digest_path(tmp_path / "digests", d)
    assert first == tmp_path / "digests" / "2026-09-17.md"
    first.write_text("x")
    second = digest_path(tmp_path / "digests", d)
    assert second == tmp_path / "digests" / "2026-09-17-2.md"
    second.write_text("x")
    assert digest_path(tmp_path / "digests", d).name == "2026-09-17-3.md"


def test_newest_digest_orders_by_date_then_run_number(tmp_path):
    from jobhunt.digest import newest_digest

    d = tmp_path / "digests"
    d.mkdir()
    for name in [
        "2026-09-16.md",
        "2026-09-17.md",
        "2026-09-17-2.md",
        "2026-09-17-10.md",
        "notes.md",
    ]:
        (d / name).write_text("x")
    assert newest_digest(d).name == "2026-09-17-10.md"
    (d / "2026-09-18.md").write_text("x")
    assert newest_digest(d).name == "2026-09-18.md"


def test_newest_digest_returns_none_when_empty(tmp_path):
    from jobhunt.digest import newest_digest

    assert newest_digest(tmp_path / "missing") is None
