"""Create a workspace directory from the templates shipped in `jobhunt.templates`.

Every template file ends in `.tmpl` (so that a nested .gitignore or pyproject.toml cannot
affect the engine repo); the suffix is stripped on write. Only pyproject.toml has a
placeholder, `{engine_url}`.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

DEFAULT_ENGINE_URL = "https://github.com/jessefontaine/jobhunt"
SUFFIX = ".tmpl"
EXECUTABLE = {"scripts/extract-cv.sh"}


def template_files() -> dict[str, Traversable]:
    """Workspace-relative path (suffix stripped) -> template resource."""
    found: dict[str, Traversable] = {}

    def walk(node: Traversable, prefix: str) -> None:
        for child in node.iterdir():
            if child.is_dir():
                if child.name != "__pycache__":
                    walk(child, f"{prefix}{child.name}/")
            elif child.name.endswith(SUFFIX):
                found[prefix + child.name[: -len(SUFFIX)]] = child

    walk(files("jobhunt.templates"), "")
    return found


def init_workspace(directory: Path, engine_url: str = DEFAULT_ENGINE_URL) -> list[Path]:
    """Write every template into `directory`; refuse a non-empty directory."""
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"{directory} already exists and is not empty")
    written: list[Path] = []
    for rel, resource in sorted(template_files().items()):
        text = resource.read_text()
        if rel == "pyproject.toml":
            text = text.replace("{engine_url}", engine_url)
        dest = directory / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        if rel in EXECUTABLE:
            dest.chmod(dest.stat().st_mode | 0o111)
        written.append(dest)
    return written
