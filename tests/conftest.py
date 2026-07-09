from pathlib import Path

import pytest


def isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point home-directory lookups at the pytest temp directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() resolves via USERPROFILE on Windows, so HOME alone does not
    # isolate tests from the developer's real ~/.tau settings.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
