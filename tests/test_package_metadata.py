import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_version_floor_matches_package_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.12"
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"
    assert pyproject["tool"]["mypy"]["python_version"] == "3.12"
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_current_version_has_release_notes() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release_notes_path = ROOT / "src" / "tau_coding" / "data" / "release-notes" / "releases.json"
    assert release_notes_path.is_file(), f"release notes not found at {release_notes_path}"
    release_notes = json.loads(release_notes_path.read_text(encoding="utf-8"))

    assert any(entry["version"] == pyproject["project"]["version"] for entry in release_notes)
