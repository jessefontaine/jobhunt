import json

import pytest

from jobhunt.sources import get_source, list_sources
from jobhunt.sources.base import SourceResult
from jobhunt.sources.fixture import FixtureSource


def test_fixture_source_loads_listings_from_json(tmp_path):
    path = tmp_path / "listings.json"
    path.write_text(
        json.dumps(
            [
                {
                    "title": "Job A",
                    "employer": "Uni",
                    "url": "https://x.org/a",
                    "deadline": "2026-10-01",
                },
                {"title": "Job B", "employer": "Lab", "url": "https://x.org/b"},
            ]
        )
    )
    result = FixtureSource().fetch({"path": str(path)}, http=None)
    assert isinstance(result, SourceResult)
    assert [lst.title for lst in result.listings] == ["Job A", "Job B"]
    assert all(lst.source == "fixture" for lst in result.listings)
    assert result.listings[0].deadline.isoformat() == "2026-10-01"
    assert result.errors == [] and result.manual_urls == {}


def test_fixture_source_reports_missing_file_as_error(tmp_path):
    result = FixtureSource().fetch({"path": str(tmp_path / "nope.json")}, http=None)
    assert result.listings == []
    assert len(result.errors) == 1


def test_registry_resolves_by_name():
    assert isinstance(get_source("fixture"), FixtureSource)
    assert "fixture" in list_sources()
    with pytest.raises(KeyError):
        get_source("does-not-exist")
