import json
import subprocess
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_NOTES_SOURCE_PATH = ROOT / "src" / "tau_coding" / "data" / "release-notes" / "releases.json"
RELEASE_NOTES_WHEEL_PATH = "tau_coding/data/release-notes/releases.json"
BUILTIN_RESOURCE_WHEEL_PATHS = {
    "tau_coding/data/docs/README.md",
    "tau_coding/data/docs/extensions.md",
    "tau_coding/data/examples/extensions/hello_tool.py",
}


def test_python_version_floor_matches_package_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.12"
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"
    assert pyproject["tool"]["mypy"]["python_version"] == "3.12"
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_current_version_has_release_notes() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert RELEASE_NOTES_SOURCE_PATH.is_file(), (
        f"release notes not found at {RELEASE_NOTES_SOURCE_PATH}"
    )
    release_notes = json.loads(RELEASE_NOTES_SOURCE_PATH.read_text(encoding="utf-8"))

    assert any(entry["version"] == pyproject["project"]["version"] for entry in release_notes)


def test_wheel_includes_release_notes_package_data(tmp_path: Path) -> None:
    """Regression: releases.json must be included in installed wheels."""
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, result.stdout + result.stderr
    with ZipFile(wheels[0]) as wheel:
        wheel_files = set(wheel.namelist())

    assert RELEASE_NOTES_WHEEL_PATH in wheel_files
    assert wheel_files >= BUILTIN_RESOURCE_WHEEL_PATHS
    assert not any(path.startswith("tau_coding/data/skills/") for path in wheel_files)
