"""Locations of Tau's packaged self-documentation and examples."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_DATA_ROOT = _PACKAGE_ROOT / "data"


def tau_readme_path() -> Path:
    """Return the installed overview document for Tau-aware tasks."""
    return _DATA_ROOT / "docs" / "README.md"


def tau_docs_path() -> Path:
    """Return the installed Tau self-documentation directory."""
    return _DATA_ROOT / "docs"


def tau_examples_path() -> Path:
    """Return the installed Tau example directory."""
    return _DATA_ROOT / "examples"
