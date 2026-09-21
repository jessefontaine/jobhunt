"""Links that open a prefilled issue form on the engine's GitHub tracker.

Only GitHub's issue *forms* (.yml templates) take query parameters per field; the dashboard
fills the `environment` field so people don't have to be asked what they are running.
"""

from __future__ import annotations

import platform
from urllib.parse import urlencode

from jobhunt.scaffold import DEFAULT_ENGINE_URL
from jobhunt.update import EngineInstall

TEMPLATES = {"bug": "bug_report.yml", "feature": "feature_request.yml"}


def environment(install: EngineInstall, version: str) -> str:
    """Two lines: the engine version and commit, then Python and the OS."""
    where = install.commit[:7] if install.commit else "development checkout"
    return f"jobhunt {version} ({where})\nPython {platform.python_version()}, {platform.platform()}"


def issue_url(kind: str, install: EngineInstall, version: str) -> str:
    """The new-issue page for `kind` ("bug" or "feature") with the environment filled in."""
    query = {"template": TEMPLATES[kind], "environment": environment(install, version)}
    return f"{DEFAULT_ENGINE_URL}/issues/new?{urlencode(query)}"
