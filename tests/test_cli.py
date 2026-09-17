import json

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.digest import newest_digest

runner = CliRunner()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
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
    assert "PhD vision" not in latest
    assert "RA fMRI" in latest


def test_sources_lists_registry(root):
    result = run(root, "sources")
    assert result.exit_code == 0
    assert "fixture" in result.output
