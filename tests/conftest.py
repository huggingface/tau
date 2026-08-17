import webbrowser
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def prevent_browser_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail tests that accidentally launch an external browser."""

    def fail_browser_open(url: str, *_args: object, **_kwargs: object) -> bool:
        pytest.fail(f"Test attempted to open a browser URL: {url}")

    monkeypatch.setattr(webbrowser, "open", fail_browser_open)


def isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point home-directory lookups at the pytest temp directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() resolves via USERPROFILE on Windows, so HOME alone does not
    # isolate tests from the developer's real ~/.tau settings.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
