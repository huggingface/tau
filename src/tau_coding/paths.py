"""Canonical filesystem paths for Tau user and project data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from os import environ
from pathlib import Path


def _default_tau_home() -> Path:
    """Return the Tau home directory, respecting TAU_HOME if set."""
    if tau_home := environ.get("TAU_HOME"):
        return Path(tau_home).expanduser()
    return Path.home() / ".tau"


def _default_agents_home() -> Path:
    """Return the agents home directory, respecting TAU_AGENTS_HOME if set."""
    if agents_home := environ.get("TAU_AGENTS_HOME"):
        return Path(agents_home).expanduser()
    return Path.home() / ".agents"


@dataclass(frozen=True, slots=True)
class TauPaths:
    """Resolved Tau filesystem locations.

    Tau keeps durable application data under the user's home directory while also
    loading project-local resources from the active working directory.
    """

    home: Path = field(default_factory=_default_tau_home)
    agents_home: Path = field(default_factory=_default_agents_home)

    @property
    def sessions_dir(self) -> Path:
        """Return the user-level session directory."""
        return self.home / "sessions"

    @property
    def logs_dir(self) -> Path:
        """Return Tau's user-level diagnostic log directory."""
        return self.home / "logs"

    @property
    def agent_calls_log_path(self) -> Path:
        """Return the JSONL diagnostic log for agent-call failures."""
        return self.logs_dir / "agent-calls.jsonl"

    @property
    def user_skills_dir(self) -> Path:
        """Return Tau's user-level skills directory."""
        return self.home / "skills"

    @property
    def user_prompts_dir(self) -> Path:
        """Return Tau's user-level prompt templates directory."""
        return self.home / "prompts"

    @property
    def user_themes_dir(self) -> Path:
        """Return Tau's user-level TUI themes directory."""
        return self.home / "themes"

    @property
    def user_agents_skills_dir(self) -> Path:
        """Return the user-level `.agents/skills` directory."""
        return self.agents_home / "skills"

    @property
    def user_agents_prompts_dir(self) -> Path:
        """Return the user-level `.agents/prompts` directory."""
        return self.agents_home / "prompts"

    def project_tau_dir(self, cwd: Path) -> Path:
        """Return the project-local Tau resource directory."""
        return cwd / ".tau"

    def project_agents_dir(self, cwd: Path) -> Path:
        """Return the project-local `.agents` resource directory."""
        return cwd / ".agents"

    def project_skills_dir(self, cwd: Path) -> Path:
        """Return the project-local Tau skills directory."""
        return self.project_tau_dir(cwd) / "skills"

    def project_prompts_dir(self, cwd: Path) -> Path:
        """Return the project-local Tau prompt templates directory."""
        return self.project_tau_dir(cwd) / "prompts"

    def project_themes_dir(self, cwd: Path) -> Path:
        """Return the project-local Tau TUI themes directory."""
        return self.project_tau_dir(cwd) / "themes"

    def project_agents_skills_dir(self, cwd: Path) -> Path:
        """Return the project-local `.agents/skills` directory."""
        return self.project_agents_dir(cwd) / "skills"

    def project_agents_prompts_dir(self, cwd: Path) -> Path:
        """Return the project-local `.agents/prompts` directory."""
        return self.project_agents_dir(cwd) / "prompts"

    def project_session_dir(self, cwd: Path) -> Path:
        """Return the user-home session directory for a project cwd."""
        resolved = cwd.resolve()
        digest = sha256(str(resolved).encode("utf-8")).hexdigest()[:6]
        slug = _slugify_path(resolved)
        return self.sessions_dir / f"{slug or 'project'}-{digest}"

    def default_session_path(self, cwd: Path) -> Path:
        """Return the default JSONL session path for a project cwd."""
        path = self.project_session_dir(cwd) / "default.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def _slugify_path(path: Path, *, max_length: int = 72) -> str:
    parts = [part for part in path.parts if part not in (path.anchor, "")]
    try:
        relative_to_home = path.relative_to(Path.home())
    except ValueError:
        pass
    else:
        parts = ["home", *relative_to_home.parts]

    slug_parts = [
        normalized
        for part in parts
        if (normalized := re.sub(r"[^a-zA-Z0-9._-]+", "-", part).strip(".-_").lower())
    ]
    slug = "-".join(slug_parts)
    if len(slug) <= max_length:
        return slug

    suffix_parts: list[str] = []
    suffix_length = 0
    for part in reversed(slug_parts):
        next_length = suffix_length + len(part) + (1 if suffix_parts else 0)
        if next_length > max_length:
            break
        suffix_parts.append(part)
        suffix_length = next_length
    return "-".join(reversed(suffix_parts)) or slug[-max_length:].strip("-")
