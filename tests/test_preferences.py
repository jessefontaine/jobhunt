import json

import pytest

from jobhunt.config import Paths
from jobhunt.models import Listing, Rating
from jobhunt.ratings import regenerate_preferences, split_preferences
from jobhunt.store import Store

PREFS = """# Preferences

intro text

## Manual

- I hate wet lab

## Learned

- old rule
"""


def test_split_preferences_separates_manual_and_learned():
    head, manual, learned = split_preferences(PREFS)
    assert "intro text" in head
    assert manual.strip() == "- I hate wet lab"
    assert learned.strip() == "- old rule"


def test_split_preferences_handles_missing_sections():
    head, manual, learned = split_preferences("# Preferences\n\njust text\n")
    assert "just text" in head and manual == "" and learned == ""


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
    head, manual, learned = split_preferences(text)
    assert manual.strip() == "- I hate wet lab"
    assert learned.strip() == "- Likes NeuroAI labs\n- Avoids clinical"
    assert "intro text" in head
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
