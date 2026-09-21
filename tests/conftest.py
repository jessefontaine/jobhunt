"""Fixtures shared by the workspace and web tests (test_cli.py keeps its own copies)."""

import json
import re

import pytest

from jobhunt.workspace import Workspace

LISTINGS = [
    {
        "title": "PhD vision",
        "employer": "Donders",
        "url": "https://x.org/1",
        "deadline": "2099-01-01",
        "summary": "Deep nets for vision",
    },
    {"title": "Old job", "employer": "Uni", "url": "https://x.org/2", "deadline": "2000-01-01"},
    {"title": "RA fMRI", "employer": "UvA", "url": "https://x.org/3"},
]


def _fake_runner(prompt, model, schema):
    """Scores each listing in the prompt 90, 80, …; answers a preferences prompt with one rule."""
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
def fake_runner():
    return _fake_runner


@pytest.fixture
def ws(tmp_path, fake_runner) -> Workspace:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("sources: {}\n")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text("PROFILE")
    (tmp_path / "profile" / "preferences.md").write_text("# Preferences\n\n## Manual\n\n- mine\n")
    (tmp_path / "listings.json").write_text(json.dumps(LISTINGS))
    return Workspace.open(tmp_path, runner=fake_runner)
