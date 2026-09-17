"""Registry of job sources.

Add a source: write the module, list it here, enable it in sources.yaml.
"""

from __future__ import annotations

from jobhunt.sources.academictransfer import AcademicTransferSource
from jobhunt.sources.base import Source, SourceResult
from jobhunt.sources.euraxess import EuraxessSource
from jobhunt.sources.fixture import FixtureSource
from jobhunt.sources.indeed import IndeedSource
from jobhunt.sources.linkedin import LinkedInSource
from jobhunt.sources.pages import PagesSource

_SOURCES: dict[str, type] = {
    FixtureSource.name: FixtureSource,
    AcademicTransferSource.name: AcademicTransferSource,
    EuraxessSource.name: EuraxessSource,
    PagesSource.name: PagesSource,
    IndeedSource.name: IndeedSource,
    LinkedInSource.name: LinkedInSource,
}


def get_source(name: str) -> Source:
    return _SOURCES[name]()


def list_sources() -> list[str]:
    return sorted(_SOURCES)


__all__ = ["Source", "SourceResult", "get_source", "list_sources"]
