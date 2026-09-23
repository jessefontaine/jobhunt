import json
import re

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.digest import newest_digest

runner = CliRunner()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("sources: {}\n")
    fixture = tmp_path / "listings.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "title": "PhD vision",
                    "employer": "Donders",
                    "url": "https://x.org/1",
                    "deadline": "2099-01-01",
                },
                {
                    "title": "Old job",
                    "employer": "Uni",
                    "url": "https://x.org/2",
                    "deadline": "2000-01-01",
                },
                {"title": "RA fMRI", "employer": "UvA", "url": "https://x.org/3"},
            ]
        )
    )
    return tmp_path


def run(root, *args):
    return runner.invoke(app, ["--root", str(root), *args])


def test_check_with_fixture_writes_digest_of_unexpired_listings(root):
    result = run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    assert result.exit_code == 0, result.output
    digests = list((root / "digests").glob("*.md"))
    assert len(digests) == 1
    md = digests[0].read_text()
    assert "PhD vision" in md and "RA fMRI" in md
    assert "Old job" not in md
    assert "New: 3" in md


def test_second_check_emits_nothing_new_but_still_lists_unrated(root):
    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    result = run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    assert result.exit_code == 0, result.output
    assert len(list((root / "digests").glob("*.md"))) == 2
    md = newest_digest(root / "digests").read_text()
    assert "New: 0" in md
    assert "PhD vision" in md  # unrated listings keep showing up until rated


def test_rate_ingests_and_excludes_rated_from_next_digest(root):
    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    digest = next((root / "digests").glob("*.md"))
    text = digest.read_text()
    head, sep, tail = text.partition("PhD vision")
    tail = tail.replace("rating:\nnote:", "rating: 5\nnote: yes", 1)
    digest.write_text(head + sep + tail)

    result = run(root, "rate", "--no-learn")
    assert result.exit_code == 0, result.output
    assert "1 rating" in result.output
    assert (root / "data" / "ratings.jsonl").exists()

    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    latest = newest_digest(root / "digests").read_text()
    assert "## 1. PhD vision" not in latest and "## PhD vision" not in latest
    assert "RA fMRI" in latest
    # …but a 5 stays visible at the top, in the shortlist
    assert latest.index("## Shortlist") < latest.index("- **PhD vision** — Donders · 5/5")
    assert latest.index("- **PhD vision**") < latest.index("RA fMRI")


def _rate(root, title, rating, note=""):
    digest = newest_digest(root / "digests")
    head, sep, tail = digest.read_text().partition(title)
    tail = tail.replace("rating:\nnote:", f"rating: {rating}\nnote: {note}", 1)
    digest.write_text(head + sep + tail)
    return run(root, "rate", "--no-learn")


def test_shortlist_prints_and_writes_file(root):
    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    result = run(root, "shortlist")
    assert result.exit_code == 0, result.output
    assert "(none yet)" in result.output

    _rate(root, "PhD vision", 5, "yes")
    _rate(root, "RA fMRI", 3)
    result = run(root, "shortlist")
    assert result.exit_code == 0, result.output
    expected = (
        "- **PhD vision** — Donders · 5/5 · deadline 2099-01-01"
        " · [fixture](https://x.org/1) · note: yes"
    )
    assert expected in result.output
    assert "RA fMRI" not in result.output
    body = (root / "shortlist.md").read_text()
    assert body.startswith("# Shortlist — ")
    assert expected in body
    assert f"shortlist: {root / 'shortlist.md'}" in result.output


def test_rate_and_check_refresh_shortlist_file(root):
    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    assert "(none yet)" in (root / "shortlist.md").read_text()
    _rate(root, "PhD vision", 4)
    assert "- **PhD vision** — Donders · 4/5" in (root / "shortlist.md").read_text()


def test_sources_lists_registry(root):
    result = run(root, "sources")
    assert result.exit_code == 0
    assert "fixture" in result.output


def _fake_runner(prompt, model, schema):
    import re

    ids = re.findall(r'"id": "([0-9a-f]{12})"', prompt)
    if "rules" in json.dumps(schema):
        return json.dumps({"type": "result", "structured_output": {"rules": ["learned rule"]}})
    scores = [
        {
            "id": i,
            "score": 90 - 10 * n,
            "role_type": "phd",
            "area_tags": ["t"],
            "why": f"why {i}",
            "concerns": "",
        }
        for n, i in enumerate(ids)
    ]
    return json.dumps({"type": "result", "structured_output": {"scores": scores}})


@pytest.fixture
def scored_root(root, monkeypatch):
    import jobhunt.cli as cli

    monkeypatch.setattr(cli, "RUNNER", _fake_runner)
    (root / "profile").mkdir()
    (root / "profile" / "profile.md").write_text("PROFILE")
    (root / "profile" / "preferences.md").write_text("# Preferences\n\n## Manual\n\n- mine\n")
    return root


def test_check_scores_and_ranks_digest(scored_root):
    root = scored_root
    result = run(root, "check", "--fixture", str(root / "listings.json"))
    assert result.exit_code == 0, result.output
    assert "scored: 2" in result.output
    md = newest_digest(root / "digests").read_text()
    assert "## 1. " in md and "## 2. " in md and "## Unscored" not in md
    assert "score 90" in md and "score 80" in md
    assert md.index("score 90") < md.index("score 80")
    assert "**Why:** why" in md


def test_score_dry_run_prints_prompt_without_scoring(scored_root):
    root = scored_root
    run(root, "fetch", "--fixture", str(root / "listings.json"))
    result = run(root, "score", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "PROFILE" in result.output and "Listings to score" in result.output
    result = run(root, "score")
    assert "scored: 2" in result.output


def test_score_rescore_replaces_existing_scores(scored_root, monkeypatch):
    import jobhunt.cli as cli

    root = scored_root
    run(root, "check", "--fixture", str(root / "listings.json"))
    assert "scored: 2" in run(root, "score", "--rescore").output  # plain score would find 0

    def flat_runner(prompt, model, schema):
        return _fake_runner(prompt, model, schema).replace('"score": 90', '"score": 42')

    monkeypatch.setattr(cli, "RUNNER", flat_runner)
    result = run(root, "check", "--fixture", str(root / "listings.json"), "--rescore")
    assert result.exit_code == 0, result.output
    assert "scored: 2" in result.output
    assert "score 42" in newest_digest(root / "digests").read_text()


def test_rate_learns_when_forced_and_keeps_manual(scored_root):
    root = scored_root
    run(root, "check", "--fixture", str(root / "listings.json"))
    digest = newest_digest(root / "digests")
    digest.write_text(digest.read_text().replace("rating:\nnote:", "rating: 5\nnote: yes", 1))
    result = run(root, "rate")
    assert result.exit_code == 0, result.output
    assert "preferences: skipped" in result.output  # < 3 new ratings
    result = run(root, "rate", "--force")
    assert "regenerating preferences" in result.output, result.output
    assert "preferences: updated" in result.output, result.output
    prefs = (root / "profile" / "preferences.md").read_text()
    assert "- mine" in prefs and "- learned rule" in prefs


NO_WORKSPACE = (
    "Not inside a jobhunt workspace (no config/sources.yaml found). Run: jobhunt init DIR"
)


def test_commands_fail_cleanly_outside_a_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 1
    assert NO_WORKSPACE in result.output


def test_subcommand_help_works_outside_a_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0, result.output
    assert "--no-score" in result.output


def test_root_option_must_point_at_a_workspace(tmp_path):
    result = runner.invoke(app, ["--root", str(tmp_path), "sources"])
    assert result.exit_code == 1
    assert NO_WORKSPACE in result.output


def test_init_creates_a_workspace_without_needing_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no workspace anywhere above
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(ws)])
    assert result.exit_code == 0, result.output
    assert (ws / "config" / "sources.yaml").exists()
    assert (ws / ".claude" / "skills" / "jobhunt" / "SKILL.md").exists()
    assert "profile/profile.md" in result.output and "claude login" in result.output
    # the new workspace is immediately usable
    assert runner.invoke(app, ["--root", str(ws), "sources"]).exit_code == 0


def test_init_writes_engine_option(tmp_path):
    ws = tmp_path / "ws"
    runner.invoke(app, ["init", str(ws), "--engine", "https://example.org/e"])
    assert "git+https://example.org/e" in (ws / "pyproject.toml").read_text()


def test_init_refuses_non_empty_dir(tmp_path):
    (tmp_path / "x").write_text("")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "not empty" in result.output


def test_serve_help_lists_its_options(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--port" in result.output and "--no-open" in result.output


def test_serve_requires_a_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 1
    assert NO_WORKSPACE in result.output


def test_update_check_explains_this_editable_checkout(root):
    result = run(root, "update", "--check")
    assert result.exit_code == 0, result.output
    assert "development checkout" in result.output
    assert "no update check" in result.output


def test_update_refuses_to_update_an_editable_checkout(root):
    result = run(root, "update")
    assert result.exit_code == 1
    assert "development checkout" in result.output


def test_learn_regenerates_preferences(scored_root):
    root = scored_root
    result = run(root, "learn")
    assert result.exit_code == 0, result.output
    assert "preferences: updated" in result.output
    assert "- learned rule" in (root / "profile" / "preferences.md").read_text()


def test_rescore_leaves_rated_listings_alone_unless_asked(scored_root):
    root = scored_root
    run(root, "check", "--fixture", str(root / "listings.json"))
    digest = newest_digest(root / "digests")
    digest.write_text(digest.read_text().replace("rating:\nnote:", "rating: 1\nnote: no", 1))
    run(root, "rate", "--no-learn")
    assert "scored: 1" in run(root, "score", "--rescore").output
    assert "scored: 2" in run(root, "score", "--rescore", "--include-rated").output


def test_add_stores_a_link_given_by_hand(scored_root):
    result = run(
        scored_root,
        "add",
        "https://example.org/j/1",
        "--title",
        "PhD vision",
        "--employer",
        "Donders",
        "--no-score",
    )
    assert result.exit_code == 0, result.output
    assert "added: PhD vision — Donders" in result.output
    assert "score" not in result.output


def test_add_scores_the_listing_it_stored(scored_root):
    result = run(
        scored_root, "add", "https://example.org/j/1", "--title", "PhD vision", "--employer", "D"
    )
    assert result.exit_code == 0, result.output
    assert "score 90" in result.output


def test_add_without_a_title_and_an_unreachable_page_explains_itself(scored_root):
    result = run(scored_root, "add", "https://127.0.0.1:9/nothing")
    assert result.exit_code == 1
    assert "could not reach" in result.output


def test_show_prints_a_listing_and_its_score(scored_root):
    root = scored_root
    run(root, "check", "--fixture", str(root / "listings.json"))
    result = run(root, "show", "https://x.org/1")
    assert result.exit_code == 0, result.output
    assert "PhD vision — Donders" in result.output
    assert "(phd) — why" in result.output


def test_show_reports_an_unknown_link(scored_root):
    result = run(scored_root, "show", "https://x.org/nope")
    assert result.exit_code == 1
    assert "not in the store" in result.output


def test_calibration_says_there_is_nothing_to_compare_yet(root):
    result = run(root, "calibration")
    assert result.exit_code == 0, result.output
    assert "0 rated listings" in result.output


def test_calibration_counts_ratings_given_without_a_score(root):
    run(root, "check", "--fixture", str(root / "listings.json"), "--no-score")
    _rate(root, "PhD vision", 5)

    result = run(root, "calibration")

    assert result.exit_code == 0, result.output
    assert "1 rated listing had no score" in result.output


def _rated_root(root, n):
    """A workspace whose store already holds `n` rated, scored listings."""
    from jobhunt.config import Paths
    from jobhunt.models import Listing, Rating, Score
    from jobhunt.store import Store

    paths = Paths(root)
    (root / "profile").mkdir(exist_ok=True)
    paths.profile.write_text("PROFILE")
    paths.preferences.write_text("# Preferences\n\n## Manual\n\n- mine\n")
    store = Store(paths.db)
    for i in range(n):
        lst = Listing(source="s", title=f"Job {i}", employer=f"Uni {i}", url=f"https://x.org/j/{i}")
        store.upsert_listings([lst])
        store.save_rating(Rating(listing_id=lst.id, rating=(i % 5) + 1))
        store.save_scores([Score(listing_id=lst.id, score=50, model="m")])
    return root


def _cv_runner(counter):
    """Scores a listing by the rating it was given, so the candidate rules always win."""

    def runner(prompt, model, schema):
        counter.append(prompt)
        if "rules" in json.dumps(schema):
            return json.dumps(
                {"type": "result", "structured_output": {"rules": ["CV-RULE"], "specifics": []}}
            )
        batch = prompt.partition("# Listings to score")[2]
        ids = re.findall(r'"id": "([0-9a-f]{12})"', batch)
        candidate = "CV-RULE" in prompt.partition("# Listings to score")[0]
        ranks = {i: n for n, i in enumerate(ids)}
        return json.dumps(
            {
                "type": "result",
                "structured_output": {
                    "scores": [
                        {
                            "id": i,
                            "score": 20 + ranks[i] * 15 if candidate else 60 - ranks[i] * 2,
                            "role_type": "phd",
                            "area_tags": [],
                            "why": "w",
                            "concerns": "",
                        }
                        for i in ids
                    ]
                },
            }
        )

    return runner


def test_learn_dry_run_prices_the_cross_validation_without_calling_claude(root, monkeypatch):
    _rated_root(root, 25)
    calls = []
    import jobhunt.cli as cli

    monkeypatch.setattr(cli, "RUNNER", _cv_runner(calls))
    result = run(root, "learn", "--cross-validate", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "claude calls" in result.output and "minutes" in result.output
    assert calls == []


def test_learn_cross_validate_reports_and_writes(root, monkeypatch):
    _rated_root(root, 25)
    calls = []
    import jobhunt.cli as cli

    monkeypatch.setattr(cli, "RUNNER", _cv_runner(calls))
    result = run(root, "learn", "--cross-validate", "--yes")
    assert result.exit_code == 0, result.output
    assert "candidate" in result.output and "current" in result.output
    assert "CV-RULE" in (root / "profile" / "preferences.md").read_text()
    assert "- mine" in (root / "profile" / "preferences.md").read_text()


def test_learn_cross_validate_refuses_with_too_few_ratings(root, monkeypatch):
    _rated_root(root, 5)
    calls = []
    import jobhunt.cli as cli

    monkeypatch.setattr(cli, "RUNNER", _cv_runner(calls))
    result = run(root, "learn", "--cross-validate", "--yes")
    assert result.exit_code == 1
    assert "at least 20" in result.output
    assert calls == []


def test_calibration_reports_the_last_cross_validation(root, monkeypatch):
    _rated_root(root, 25)
    import jobhunt.cli as cli

    monkeypatch.setattr(cli, "RUNNER", _cv_runner([]))
    run(root, "learn", "--cross-validate", "--yes")
    result = run(root, "calibration")
    assert result.exit_code == 0, result.output
    assert "Last cross-validation" in result.output
