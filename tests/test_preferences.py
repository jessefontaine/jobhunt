import json
import pathlib

import pytest

from jobhunt.config import Paths
from jobhunt.models import Listing, Rating, Score
from jobhunt.ratings import (
    learned_at,
    preferences_instructions,
    regenerate_preferences,
    split_preferences,
)
from jobhunt.settings import PreferenceSettings
from jobhunt.store import Store

PREFS = """# Preferences

intro text

## Manual

- I hate wet lab

## Learned

- old rule
"""


def test_split_preferences_separates_manual_and_learned():
    prefs = split_preferences(PREFS)
    assert "intro text" in prefs.head
    assert prefs.manual == "- I hate wet lab"
    assert prefs.learned == "- old rule"


def test_split_preferences_handles_missing_sections():
    prefs = split_preferences("# Preferences\n\njust text\n")
    assert "just text" in prefs.head and prefs.manual == "" and prefs.learned == ""


def test_split_preferences_keeps_sections_it_does_not_know():
    prefs = split_preferences(PREFS + "\n## Scratch\n\n- keep me\n")
    assert prefs.extra == "## Scratch\n\n- keep me"


@pytest.fixture
def env(tmp_path):
    (tmp_path / "profile").mkdir()
    paths = Paths(tmp_path)
    paths.preferences.write_text(PREFS)
    store = Store(paths.db)
    l1 = Listing(source="s", title="NeuroAI PhD", employer="Donders", url="u1", summary="dnn")
    l2 = Listing(source="s", title="Clinical trial RA", employer="UMC", url="u2")
    store.upsert_listings([l1, l2])
    store.save_rating(Rating(listing_id=l1.id, rating=5, note="dream lab"))
    store.save_rating(Rating(listing_id=l2.id, rating=1, note="too clinical"))
    return paths, store


def test_regenerate_preferences_replaces_learned_and_keeps_manual(env):
    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        seen["model"] = model
        return json.dumps(
            {
                "type": "result",
                "is_error": False,
                "structured_output": {"rules": ["Likes NeuroAI labs", "Avoids clinical"]},
            }
        )

    ok = regenerate_preferences(paths, store, runner, "sonnet")
    assert ok
    text = paths.preferences.read_text()
    prefs = split_preferences(text)
    assert prefs.manual == "- I hate wet lab"
    assert prefs.learned == "- Likes NeuroAI labs\n- Avoids clinical"
    assert "intro text" in prefs.head
    assert "NeuroAI PhD" in seen["prompt"] and "dream lab" in seen["prompt"]
    assert "5/5" in seen["prompt"] and "1/5" in seen["prompt"]
    assert "old rule" in seen["prompt"]  # prior rules offered for keep/drop
    assert seen["model"] == "sonnet"


def test_regenerate_preferences_leaves_file_untouched_on_bad_output(env):
    paths, store = env
    before = paths.preferences.read_text()
    ok = regenerate_preferences(paths, store, lambda *a: "garbage", "sonnet")
    assert not ok
    assert paths.preferences.read_text() == before


def test_regenerate_preferences_creates_sections_when_missing(env):
    paths, store = env
    paths.preferences.write_text("# Preferences\n")
    runner = lambda *a: json.dumps({"type": "result", "structured_output": {"rules": ["r1"]}})  # noqa: E731
    assert regenerate_preferences(paths, store, runner, "m")
    text = paths.preferences.read_text()
    assert "## Manual" in text and "## Learned" in text and "- r1" in text


def test_preferences_prompt_does_not_assert_who_the_person_is(env):
    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return json.dumps({"structured_output": {"rules": ["r"]}})

    regenerate_preferences(paths, store, runner, "sonnet")
    assert "the person described in the profile" in seen["prompt"]
    assert "Master's student" not in seen["prompt"]


def test_regenerate_preferences_records_learned_at(env):
    paths, store = env
    assert learned_at(store) is None
    runner = lambda *a: json.dumps({"type": "result", "structured_output": {"rules": ["r1"]}})  # noqa: E731
    assert regenerate_preferences(paths, store, runner, "m")
    assert learned_at(store) is not None
    assert store.ratings_since(learned_at(store)) == 0  # the fixture's ratings predate it


def test_failed_regeneration_does_not_record_learned_at(env):
    paths, store = env
    assert not regenerate_preferences(paths, store, lambda *a: "garbage", "sonnet")
    assert learned_at(store) is None


FANCY_MANUAL = """# Preferences

## Manual

Rules I wrote myself.

- top rule
  - a sub-bullet, indented

- after a blank line

## Learned

- old rule

## Scratch

- my own extra section
"""


def test_regenerate_preferences_leaves_the_manual_section_verbatim(env):
    paths, store = env
    paths.preferences.write_text(FANCY_MANUAL)
    runner = lambda *a: json.dumps({"structured_output": {"rules": ["new"]}})  # noqa: E731
    assert regenerate_preferences(paths, store, runner, "m")
    before = split_preferences(FANCY_MANUAL)
    after = split_preferences(paths.preferences.read_text())
    assert after.manual == before.manual
    assert after.extra == before.extra  # sections the engine does not own survive too
    assert after.learned == "- new"


def test_regenerate_preferences_is_idempotent_on_the_manual_section(env):
    paths, store = env
    paths.preferences.write_text(FANCY_MANUAL)
    runner = lambda *a: json.dumps({"structured_output": {"rules": ["new"]}})  # noqa: E731
    assert regenerate_preferences(paths, store, runner, "m")
    once = paths.preferences.read_text()
    assert regenerate_preferences(paths, store, runner, "m")
    assert paths.preferences.read_text() == once


def test_preferences_prompt_shows_manual_rules_as_fixed_context(env):
    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return json.dumps({"structured_output": {"rules": ["r"]}})

    regenerate_preferences(paths, store, runner, "m")
    assert "- I hate wet lab" in seen["prompt"]
    lower = seen["prompt"].lower()
    assert "contradict" in lower and "own rules" in lower


def _output(**kw):
    return json.dumps({"type": "result", "structured_output": kw})


def test_split_preferences_reads_the_specifics_section():
    prefs = split_preferences(PREFS + "\n## Specifics\n\n- only at Donders\n")
    assert prefs.specifics == "- only at Donders"
    assert prefs.extra == ""


def test_regenerate_preferences_writes_general_rules_and_specifics(env):
    paths, store = env
    runner = lambda *a: _output(rules=["general"], specifics=["narrow, e.g. Donders"])  # noqa: E731
    assert regenerate_preferences(paths, store, runner, "m")
    prefs = split_preferences(paths.preferences.read_text())
    assert prefs.learned == "- general"
    assert prefs.specifics == "- narrow, e.g. Donders"
    text = paths.preferences.read_text()
    assert text.index("## Learned") < text.index("## Specifics")


def test_regenerate_preferences_accepts_output_without_specifics(env):
    paths, store = env
    assert regenerate_preferences(paths, store, lambda *a: _output(rules=["r"]), "m")
    assert split_preferences(paths.preferences.read_text()).specifics == ""


def test_preferences_prompt_offers_prior_specifics_for_keep_or_drop(env):
    paths, store = env
    paths.preferences.write_text(PREFS + "\n## Specifics\n\n- old specific\n")
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"], seen["schema"] = prompt, schema
        return _output(rules=["r"], specifics=["s"])

    regenerate_preferences(paths, store, runner, "m")
    assert "old specific" in seen["prompt"]
    assert "specifics" in json.dumps(seen["schema"])


def test_split_preferences_does_not_mistake_the_empty_marker_for_a_rule():
    prefs = split_preferences(
        "# Preferences\n\n## Manual\n\n- (none yet)\n\n"
        "## Learned\n\n(none yet — regenerated from your ratings by `jobhunt rate`)\n"
    )
    assert prefs.manual == "" and prefs.learned == ""


def test_the_scaffolded_preferences_file_survives_a_regeneration():
    from jobhunt.ratings import render_preferences

    template = (
        pathlib.Path(__file__).parents[1]
        / "src/jobhunt/templates/profile/preferences.md.tmpl"
    ).read_text()
    prefs = split_preferences(template)
    assert prefs.manual == "" and prefs.learned == "" and prefs.specifics == ""
    assert render_preferences(prefs) == template  # head and headings come back untouched


def test_preferences_prompt_uses_the_configured_caps(env):
    from jobhunt.settings import PreferenceSettings

    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return _output(rules=["r"])

    caps = PreferenceSettings(max_rules=4, max_specifics=2, max_words_per_rule=12)
    regenerate_preferences(paths, store, runner, "m", caps)
    assert "at most 4 general patterns" in seen["prompt"]
    assert "at most 2 narrow observations" in seen["prompt"]
    assert "at most 12 words" in seen["prompt"]


def test_preferences_prompt_defaults_to_ten_rules(env):
    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return _output(rules=["r"])

    regenerate_preferences(paths, store, runner, "m")
    assert "at most 10 general patterns" in seen["prompt"]


def test_preferences_prompt_asks_for_overlapping_rules_to_be_merged(env):
    paths, store = env
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return _output(rules=["r"])

    regenerate_preferences(paths, store, runner, "m")
    assert "Merge" in seen["prompt"]
    assert "promote" in seen["prompt"].lower()


def test_preferences_prompt_shows_what_claude_scored_each_rated_listing(env):
    paths, store = env
    store.save_scores([Score(listing_id=store.find_by_url("u1").id, score=95, model="m")])
    seen = {}

    def runner(prompt, model, schema):
        seen["prompt"] = prompt
        return json.dumps({"structured_output": {"rules": ["r"]}})

    regenerate_preferences(paths, store, runner, "sonnet")
    assert "you scored 95" in seen["prompt"]


def test_preferences_instructions_say_what_to_do_with_those_scores():
    assert "you scored" in preferences_instructions(PreferenceSettings())
