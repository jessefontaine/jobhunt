"""config/settings.yaml: defaults, seeding from sources.yaml, round trips, filters."""

from datetime import datetime

import pytest

from jobhunt.config import Config, ConfigError, DigestConfig, Paths, ScoringConfig
from jobhunt.settings import Settings, load_settings, parse_settings, save_settings


def test_defaults_when_the_file_is_missing(tmp_path):
    settings = load_settings(Paths(tmp_path), Config())
    assert settings.display.theme == "auto"
    assert settings.display.min_score == 0
    assert settings.rated.hide_below == 3
    assert settings.updates.check is True


def test_missing_file_seeds_scoring_and_digest_from_sources_yaml(tmp_path):
    config = Config(scoring=ScoringConfig(model="opus", batch_size=4), digest=DigestConfig(limit=7))
    settings = load_settings(Paths(tmp_path), config)
    assert settings.scoring.model == "opus"
    assert settings.scoring.batch_size == 4
    assert settings.digest.limit == 7


def test_settings_file_wins_over_sources_yaml(tmp_path):
    paths = Paths(tmp_path)
    paths.settings_yaml.parent.mkdir(parents=True)
    paths.settings_yaml.write_text("scoring:\n  model: haiku\n")
    settings = load_settings(paths, Config(scoring=ScoringConfig(model="opus")))
    assert settings.scoring.model == "haiku"
    assert settings.digest.limit == 60  # not in the file: the default, not the sources.yaml value


def test_save_then_load_round_trips(tmp_path):
    paths = Paths(tmp_path)
    settings = Settings()
    settings.display.theme = "dark"
    settings.display.max_score = 80
    settings.rated.hide_after_days = 5
    save_settings(paths, settings)
    assert paths.settings_yaml.read_text().startswith("#")  # a header comment for hand-editors
    again = load_settings(paths, Config())
    assert again.display.theme == "dark"
    assert again.display.max_score == 80
    assert again.rated.hide_after_days == 5


def test_invalid_yaml_is_a_config_error():
    with pytest.raises(ConfigError):
        parse_settings("display: [1, 2\n")


def test_unknown_theme_is_a_config_error():
    with pytest.raises(ConfigError):
        parse_settings("display:\n  theme: neon\n")


def test_min_score_above_max_score_is_a_config_error():
    with pytest.raises(ConfigError):
        parse_settings("display:\n  min_score: 90\n  max_score: 10\n")


def test_in_range_bounds_are_inclusive():
    display = parse_settings("display:\n  min_score: 40\n  max_score: 80\n").display
    assert display.in_range(40) and display.in_range(80)
    assert not display.in_range(39)
    assert not display.in_range(81)


def test_unscored_listings_are_hidden_once_a_floor_is_set():
    assert parse_settings("").display.in_range(None) is True
    assert parse_settings("display:\n  min_score: 1\n").display.in_range(None) is False


def test_rated_hides_only_low_ratings_that_are_also_old():
    rated = parse_settings("rated:\n  hide_below: 3\n  hide_after_days: 30\n").rated
    now = datetime(2026, 9, 22, 12, 0)
    old = datetime(2026, 8, 1, 12, 0)
    assert rated.hidden(2, old, now) is True
    assert rated.hidden(2, now, now) is False  # low but fresh
    assert rated.hidden(5, old, now) is False  # old but high
    assert rated.hidden(3, old, now) is False  # the threshold itself is kept


def test_hide_below_1_keeps_everything():
    rated = parse_settings("rated:\n  hide_below: 1\n  hide_after_days: 0\n").rated
    assert rated.hidden(1, datetime(2020, 1, 1), datetime(2026, 9, 22)) is False


def test_settings_from_form_updates_only_what_the_form_carries():
    from jobhunt.settings import settings_from_form

    current = Settings()
    current.display.theme = "dark"
    updated = settings_from_form({"digest.limit": "5"}, current)
    assert updated.digest.limit == 5
    assert updated.display.theme == "dark"  # untouched fields keep their value


def test_settings_from_form_reads_a_missing_checkbox_as_off():
    from jobhunt.settings import settings_from_form

    assert settings_from_form({}, Settings()).updates.check is False
    assert settings_from_form({"updates.check": "1"}, Settings()).updates.check is True


def test_settings_from_form_rejects_an_impossible_value():
    from jobhunt.settings import settings_from_form

    with pytest.raises(ConfigError):
        settings_from_form({"display.max_score": "500"}, Settings())
