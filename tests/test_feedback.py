import platform
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from jobhunt.feedback import TEMPLATES, environment, issue_url
from jobhunt.update import EngineInstall

ROOT = Path(__file__).resolve().parents[1]
GIT_INSTALL = EngineInstall(
    url="https://github.com/x/jobhunt", commit="abcdef0" * 6, editable=False
)


def test_environment_names_the_installed_commit_python_and_platform():
    text = environment(GIT_INSTALL, "0.2.0")
    lines = text.splitlines()
    assert lines[0] == "jobhunt 0.2.0 (abcdef0)"
    assert f"Python {platform.python_version()}" in lines[1]
    assert platform.platform() in lines[1]


def test_environment_says_development_checkout_when_not_a_git_install():
    text = environment(EngineInstall(), "0.2.0")
    assert text.splitlines()[0] == "jobhunt 0.2.0 (development checkout)"


def test_issue_url_opens_the_template_with_the_environment_filled_in():
    url = issue_url("bug", GIT_INSTALL, "0.2.0")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://github.com/jessefontaine/jobhunt/issues/new"
    )
    query = parse_qs(parts.query)
    assert query["template"] == ["bug_report.yml"]
    assert query["environment"] == [environment(GIT_INSTALL, "0.2.0")]
    assert "environment=jobhunt+0.2.0+%28abcdef0%29%0A" in url  # newline survives the encoding


def test_issue_url_has_a_template_per_kind():
    assert set(TEMPLATES) == {"bug", "feature"}
    assert "template=feature_request.yml" in issue_url("feature", GIT_INSTALL, "0.2.0")
    with pytest.raises(KeyError):
        issue_url("rant", GIT_INSTALL, "0.2.0")


@pytest.mark.parametrize("kind", ["bug", "feature"])
def test_issue_form_templates_exist_and_have_an_environment_field(kind):
    """The dashboard link prefills the form field whose id is `environment`."""
    form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / TEMPLATES[kind]).read_text())
    ids = [field.get("id") for field in form["body"]]
    assert "environment" in ids
    assert form["labels"]
