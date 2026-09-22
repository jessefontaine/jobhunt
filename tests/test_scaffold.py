import os

import pytest

from jobhunt.scaffold import DEFAULT_ENGINE_URL, init_workspace, template_files

EXPECTED = {
    "pyproject.toml",
    "README.md",
    "CLAUDE.md",
    ".gitignore",
    ".claude/skills/jobhunt/SKILL.md",
    ".claude/skills/jobhunt-sources/SKILL.md",
    "config/sources.yaml",
    "config/settings.yaml",
    "profile/profile.md",
    "profile/preferences.md",
    "docs/.gitkeep",
    "scripts/extract-cv.sh",
    "data/.gitkeep",
    "digests/.gitkeep",
}


def test_templates_cover_the_workspace_layout():
    found = template_files()
    assert set(found) == EXPECTED
    for rel, res in found.items():
        if not rel.endswith(".gitkeep"):
            assert res.read_text().strip(), f"{rel} template is empty"


def test_init_workspace_writes_every_template(tmp_path):
    ws = tmp_path / "ws"
    written = init_workspace(ws)
    assert {str(p.relative_to(ws)) for p in written} == EXPECTED
    assert (ws / "config" / "sources.yaml").exists()
    assert os.access(ws / "scripts" / "extract-cv.sh", os.X_OK)


def test_init_workspace_substitutes_engine_url(tmp_path):
    init_workspace(tmp_path / "a")
    assert f'jobhunt @ git+{DEFAULT_ENGINE_URL}' in (tmp_path / "a" / "pyproject.toml").read_text()
    init_workspace(tmp_path / "b", engine_url="https://example.org/fork")
    text = (tmp_path / "b" / "pyproject.toml").read_text()
    assert "jobhunt @ git+https://example.org/fork" in text
    assert "{engine_url}" not in text


def test_init_workspace_refuses_non_empty_dir(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        init_workspace(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / "keep.txt"]  # nothing written


def test_init_workspace_accepts_existing_empty_dir(tmp_path):
    init_workspace(tmp_path)
    assert (tmp_path / "profile" / "profile.md").exists()


def test_no_template_has_placeholders_except_pyproject():
    for rel, res in template_files().items():
        if rel != "pyproject.toml" and not rel.endswith(".gitkeep"):
            assert "{engine_url}" not in res.read_text(), rel


def test_extract_cv_script_takes_the_pdf_path_as_an_argument():
    # guards against reverting to a hardcoded PDF name
    assert "$1" in template_files()["scripts/extract-cv.sh"].read_text()


def test_the_new_workspace_settings_file_loads():
    from jobhunt.settings import parse_settings

    settings = parse_settings(template_files()["config/settings.yaml"].read_text())
    assert settings.display.theme == "auto"


def test_the_sources_skill_never_tells_claude_to_patch_the_engine():
    text = template_files()[".claude/skills/jobhunt-sources/SKILL.md"].read_text()
    assert "pages:" in text  # the way a user adds a site without engine code
    assert ".venv" in text  # …and the warning not to edit the installed engine


def test_the_workspace_skill_covers_talking_about_roles_and_preferences():
    text = template_files()[".claude/skills/jobhunt/SKILL.md"].read_text()
    assert "jobhunt add" in text
    assert "jobhunt show" in text
    assert "## Manual" in text
