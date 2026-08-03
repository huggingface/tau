"""Markdown resource path and frontmatter helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tau_agent.types import JSONValue
from tau_coding.paths import TauPaths


class ResourceError(ValueError):
    """Raised when Tau resources are invalid or cannot be expanded."""


@dataclass(frozen=True, slots=True)
class SystemPromptResources:
    """Discovered Tau-native system-prompt file contents and sources."""

    custom_prompt: str | None = None
    custom_prompt_path: Path | None = None
    append_prompt: str | None = None
    append_prompt_path: Path | None = None
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceDiagnostic:
    """A non-fatal resource discovery problem or precedence note."""

    kind: str
    message: str
    path: Path | None = None
    name: str | None = None
    severity: str = "warning"

    def format(self) -> str:
        """Return a concise human-readable diagnostic line."""
        parts = [self.severity, self.kind]
        if self.name is not None:
            parts.append(self.name)
        label = " ".join(parts)
        if self.path is None:
            return f"{label}: {self.message}"
        return f"{label}: {self.message} ({self.path})"


@dataclass(frozen=True, slots=True)
class TauResourcePaths:
    """Filesystem locations for Tau markdown resources.

    By default Tau loads both Tau-native resources and `.agents` resources from
    the user home directory. When a cwd is provided, project-local `.tau` and
    `.agents` resources are loaded automatically as well.
    """

    root: Path = field(default_factory=lambda: Path.home() / ".tau")
    cwd: Path | None = None
    agents_root: Path | None = field(default_factory=lambda: Path.home() / ".agents")
    paths: TauPaths | None = None

    @property
    def skills_dir(self) -> Path:
        """Return the primary Tau skills directory."""
        return self.root / "skills"

    @property
    def prompts_dir(self) -> Path:
        """Return the primary Tau prompt templates directory."""
        return self.root / "prompts"

    @property
    def system_prompt_path(self) -> Path:
        """Return the user-level replacement system-prompt file."""
        return self.root / "SYSTEM.md"

    @property
    def append_system_prompt_path(self) -> Path:
        """Return the user-level appended system-prompt file."""
        return self.root / "APPEND_SYSTEM.md"

    @property
    def skills_dirs(self) -> tuple[Path, ...]:
        """Return skill directories in increasing precedence order.

        Only the ``skills`` subdirectory of an ``.agents`` root is scanned,
        never the root ``.agents`` directory itself (which may contain
        ``README.md``, ``AGENTS.md``, etc.).
        """
        paths = self._paths()
        dirs = [self.skills_dir]
        if self.agents_root is not None:
            dirs.append(self.agents_root / "skills")
        if self.cwd is not None:
            dirs.extend(
                [
                    paths.project_skills_dir(self.cwd),
                    paths.project_agents_skills_dir(self.cwd),
                ]
            )
        return tuple(_dedupe_paths(dirs))

    @property
    def themes_dirs(self) -> tuple[Path, ...]:
        """Return TUI theme directories in increasing precedence order.

        Themes are Tau-specific, so unlike skills and prompts no ``.agents``
        directories are scanned.
        """
        paths = self._paths()
        dirs = [self.root / "themes"]
        if self.cwd is not None:
            dirs.append(paths.project_themes_dir(self.cwd))
        return tuple(_dedupe_paths(dirs))

    @property
    def prompts_dirs(self) -> tuple[Path, ...]:
        """Return prompt template directories in increasing precedence order."""
        paths = self._paths()
        dirs = [self.prompts_dir]
        if self.agents_root is not None:
            dirs.append(self.agents_root / "prompts")
        if self.cwd is not None:
            dirs.extend(
                [
                    paths.project_prompts_dir(self.cwd),
                    paths.project_agents_prompts_dir(self.cwd),
                ]
            )
        return tuple(_dedupe_paths(dirs))

    def _paths(self) -> TauPaths:
        agents_home = self.agents_root or Path.home() / ".agents"
        return self.paths or TauPaths(home=self.root, agents_home=agents_home)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def discover_system_prompt_resources(
    paths: TauResourcePaths,
    *,
    custom_prompt_explicit: bool = False,
    append_prompt_explicit: bool = False,
    enabled: bool = True,
) -> SystemPromptResources:
    """Discover Tau-native prompt files with CLI/project/user precedence."""
    if not enabled:
        return SystemPromptResources()

    diagnostics: list[ResourceDiagnostic] = []
    custom_prompt, custom_path = _discover_system_prompt_file(
        paths,
        filename="SYSTEM.md",
        label="replacement",
        explicit=custom_prompt_explicit,
        diagnostics=diagnostics,
    )
    append_prompt, append_path = _discover_system_prompt_file(
        paths,
        filename="APPEND_SYSTEM.md",
        label="append",
        explicit=append_prompt_explicit,
        diagnostics=diagnostics,
    )
    return SystemPromptResources(
        custom_prompt=custom_prompt,
        custom_prompt_path=custom_path,
        append_prompt=append_prompt,
        append_prompt_path=append_path,
        diagnostics=tuple(diagnostics),
    )


def _discover_system_prompt_file(
    paths: TauResourcePaths,
    *,
    filename: str,
    label: str,
    explicit: bool,
    diagnostics: list[ResourceDiagnostic],
) -> tuple[str | None, Path | None]:
    candidates: list[tuple[str, Path]] = []
    if paths.cwd is not None:
        candidates.append(("project", paths.cwd / ".tau" / filename))
    candidates.append(("user", paths.root / filename))

    existing: list[tuple[str, Path]] = []
    for scope, path in candidates:
        try:
            if path.exists():
                existing.append((scope, path))
        except OSError as exc:
            raise ResourceError(
                f"Could not inspect {label} system prompt file {path}: {exc}"
            ) from exc

    if explicit:
        for _scope, path in existing:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="system-prompt",
                    name=label,
                    path=path,
                    severity="info",
                    message="ignored because an explicit startup value takes precedence",
                )
            )
        return None, None
    if not existing:
        return None, None

    selected_scope, selected_path = existing[0]
    try:
        content = selected_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceError(
            f"Could not read {label} system prompt file {selected_path} as UTF-8: {exc}"
        ) from exc

    diagnostics.append(
        ResourceDiagnostic(
            kind="system-prompt",
            name=label,
            path=selected_path,
            severity="info",
            message=f"selected {selected_scope} system prompt file",
        )
    )
    for _scope, path in existing[1:]:
        diagnostics.append(
            ResourceDiagnostic(
                kind="system-prompt",
                name=label,
                path=path,
                message=f"shadowed by higher-precedence file {selected_path}",
            )
        )
    return content, selected_path


def resource_paths_with_cwd(
    paths: TauResourcePaths | None,
    cwd: Path,
) -> TauResourcePaths:
    """Return resource paths with a cwd available for project-local discovery."""
    if paths is None:
        return TauResourcePaths(cwd=cwd)
    if paths.cwd is not None:
        return paths
    return TauResourcePaths(
        root=paths.root,
        cwd=cwd,
        agents_root=paths.agents_root,
        paths=paths.paths,
    )


def parse_markdown_resource(text: str) -> tuple[dict[str, str], str]:
    """Parse minimal YAML-like frontmatter from a markdown resource.

    Only simple `key: value` pairs are supported. This keeps resource parsing
    dependency-free and avoids evaluating arbitrary code.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized

    end = normalized.find("\n---", 4)
    if end == -1:
        return {}, normalized

    raw_frontmatter = normalized[4:end]
    body = normalized[end + len("\n---") :]
    if body.startswith("\n"):
        body = body[1:]

    metadata: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, body


def derive_description(content: str) -> str | None:
    """Derive a short description from markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
        return stripped
    return None


def metadata_to_json(metadata: dict[str, str]) -> dict[str, JSONValue]:
    """Convert string metadata into JSON-like values."""
    return dict(metadata)
