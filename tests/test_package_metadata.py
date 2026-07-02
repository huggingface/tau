import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_version_floor_matches_package_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.12"
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"
    assert pyproject["tool"]["mypy"]["python_version"] == "3.12"
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
