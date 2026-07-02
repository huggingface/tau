"""Durable shell execution settings for Tau terminal commands."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from pathlib import Path
from typing import Any

from tau_coding.paths import TauPaths


class ShellConfigError(ValueError):
    """Raised when Tau shell settings are invalid."""


@dataclass(frozen=True, slots=True)
class ShellSettings:
    """Shell execution settings loaded from Tau home."""

    shell_command_prefix: str | None = None

    def to_json(self) -> dict[str, str]:
        """Serialize these settings to JSON-compatible data."""
        if self.shell_command_prefix is None:
            return {}
        return {"shellCommandPrefix": self.shell_command_prefix}


def shell_settings_path(paths: TauPaths | None = None) -> Path:
    """Return the durable shell settings path."""
    return (paths or TauPaths()).home / "settings.json"


def load_shell_settings(paths: TauPaths | None = None) -> ShellSettings:
    """Load durable shell settings, falling back to built-in defaults."""
    path = shell_settings_path(paths)
    if not path.exists():
        return ShellSettings()
    try:
        raw = loads(path.read_text(encoding="utf-8"))
    except JSONDecodeError as exc:
        raise ShellConfigError(f"Shell settings are not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ShellConfigError("Shell settings must be a JSON object")
    return shell_settings_from_json(raw)


def shell_settings_from_json(data: dict[str, Any]) -> ShellSettings:
    """Parse shell settings from JSON-compatible data."""
    allowed_fields = {"shellCommandPrefix", "shell_command_prefix"}
    unknown_fields = set(data) - allowed_fields
    if unknown_fields:
        raise ShellConfigError(f"Unknown shell settings field: {sorted(unknown_fields)[0]}")
    if "shellCommandPrefix" in data and "shell_command_prefix" in data:
        raise ShellConfigError("Use only one of shellCommandPrefix or shell_command_prefix")

    raw_prefix = data.get("shellCommandPrefix", data.get("shell_command_prefix"))
    if raw_prefix is None:
        return ShellSettings()
    if not isinstance(raw_prefix, str):
        raise ShellConfigError("shellCommandPrefix must be a string")
    prefix = raw_prefix.strip()
    return ShellSettings(shell_command_prefix=prefix or None)
