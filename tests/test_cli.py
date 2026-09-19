import json

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
    assert "PhD vision" not in latest
    assert "RA fMRI" in latest


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
