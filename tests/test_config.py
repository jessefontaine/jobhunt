import textwrap

import pytest

from jobhunt.config import Config, ConfigError, Paths, find_root, load_config, parse_config


def test_paths_are_relative_to_root(tmp_path):
    p = Paths(tmp_path)
    assert p.db == tmp_path / "data" / "jobs.sqlite"
    assert p.ratings == tmp_path / "data" / "ratings.jsonl"
    assert p.digests == tmp_path / "digests"
    assert p.profile == tmp_path / "profile" / "profile.md"
    assert p.preferences == tmp_path / "profile" / "preferences.md"
    assert p.cv == tmp_path / "docs" / "cv.md"
    assert p.sources_yaml == tmp_path / "config" / "sources.yaml"


def test_load_config_reads_scoring_and_sources(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(
        textwrap.dedent("""
        scoring:
          model: haiku
          batch_size: 5
        sources:
          academictransfer:
            enabled: true
            queries: [neuroscience]
          euraxess:
            enabled: false
    """)
    )
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)
    assert cfg.scoring.model == "haiku"
    assert cfg.scoring.batch_size == 5
    assert cfg.scoring.examples == 20  # default
    assert cfg.sources["academictransfer"]["queries"] == ["neuroscience"]
    assert cfg.enabled_sources() == ["academictransfer"]


def test_load_config_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.scoring.model == "sonnet"
    assert cfg.enabled_sources() == []


def test_digest_limit_config_default(tmp_path):
    assert load_config(tmp_path).digest.limit == 60


def test_load_config_reads_optional_contact(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("contact: me@example.org\nsources: {}\n")
    assert load_config(tmp_path).contact == "me@example.org"


def test_load_config_contact_defaults_to_none(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("sources: {}\n")
    assert load_config(tmp_path).contact is None
    assert load_config(tmp_path / "nowhere").contact is None


def test_find_root_finds_nearest_dir_with_sources_yaml(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("sources: {}\n")
    nested = tmp_path / "digests" / "deep"
    nested.mkdir(parents=True)
    assert find_root(nested) == tmp_path
    assert find_root(tmp_path) == tmp_path


def test_find_root_returns_none_outside_a_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")  # not enough any more
    assert find_root(tmp_path) is None


def test_parse_config_accepts_empty_text():
    assert parse_config("").enabled_sources() == []


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("sources: [\n", "invalid YAML"),
        ("- just\n- a list\n", "must be a YAML mapping"),
        ("sources: [a, b]\n", "sources: must be a mapping"),
        ("scoring: {batch_size: x}\n", "invalid scoring/digest settings"),
    ],
)
def test_parse_config_rejects_unusable_text(text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_config(text)
