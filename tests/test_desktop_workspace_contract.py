from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATHS = (
    ROOT / "infra" / "compose",
    ROOT / "services" / "api",
    ROOT / "packages" / "backend-core",
    ROOT / "scripts" / "create_invite.py",
)
FORBIDDEN_DEPENDENCIES = {
    "argon2-cffi",
    "boto3",
    "psycopg",
    "redis",
}


def _dependency_name(requirement: str) -> str:
    return requirement.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0]


def test_cloud_only_paths_are_not_in_the_desktop_tree() -> None:
    remaining = [str(path.relative_to(ROOT)) for path in FORBIDDEN_PATHS if path.exists()]
    assert remaining == []


def test_python_projects_do_not_depend_on_cloud_services() -> None:
    found: dict[str, list[str]] = {}
    for pyproject in ROOT.rglob("pyproject.toml"):
        if ".venv" in pyproject.parts:
            continue
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        dependencies = document.get("project", {}).get("dependencies", [])
        names = {_dependency_name(item) for item in dependencies}
        bad = sorted(names & FORBIDDEN_DEPENDENCIES)
        if bad:
            found[str(pyproject.relative_to(ROOT))] = bad
    assert found == {}
