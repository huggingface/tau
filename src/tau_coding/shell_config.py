"""Durable shell execution settings for Tau terminal commands."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from pathlib import Path
from typing import Any

from tau_coding.paths import TauPaths
from tau_coding.project_trust import TrustDefault


class ShellConfigError(ValueError):
    """Raised when Tau shell settings are invalid."""


@dataclass(frozen=True, slots=True)
class ShellSettings:
    """Shell execution settings loaded from Tau home."""

    shell_command_prefix: str | None = None
    default_project_trust: TrustDefault = "ask"

    def to_json(self) -> dict[str, str]:
        """Serialize these settings to JSON-compatible data."""
        result: dict[str, str] = {}
        if self.default_project_trust != "ask":
            result["defaultProjectTrust"] = self.default_project_trust
        if self.shell_command_prefix is not None:
            result["shellCommandPrefix"] = self.shell_command_prefix
        return result


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
    # Read only settings this version understands so fields written by a newer
    # Tau installation cannot prevent an older installation from starting.
    if "shellCommandPrefix" in data and "shell_command_prefix" in data:
        raise ShellConfigError("Use only one of shellCommandPrefix or shell_command_prefix")

    raw_default = data.get("defaultProjectTrust", "ask")
    if raw_default not in {"ask", "always", "never"}:
        raise ShellConfigError("defaultProjectTrust must be ask, always, or never")

    raw_prefix = data.get("shellCommandPrefix", data.get("shell_command_prefix"))
    if raw_prefix is None:
        return ShellSettings(default_project_trust=raw_default)
    if not isinstance(raw_prefix, str):
        raise ShellConfigError("shellCommandPrefix must be a string")
    prefix = raw_prefix.strip()
    return ShellSettings(
        shell_command_prefix=prefix or None,
        default_project_trust=raw_default,
    )
