import textwrap

from jobhunt.config import Config, Paths, load_config


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
