"""Markdown skill loading and expansion."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tau_coding.resources import (
    ResourceDiagnostic,
    ResourceError,
    TauResourcePaths,
    derive_description,
    parse_markdown_resource,
)


@dataclass(frozen=True, slots=True)
class Skill:
    """A markdown skill resource."""

    name: str
    path: Path
    content: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    """Parsed expanded skill invocation message."""

    name: str
    location: str
    content: str
    additional_instructions: str | None = None


def load_skills(paths: TauResourcePaths | None = None) -> list[Skill]:
    """Load markdown skills from Tau and `.agents` resource directories.

    Resource directories are loaded in increasing precedence order, so project
    resources override user resources with the same skill name. Duplicate names
    within the same directory remain invalid.
    """
    resource_paths = paths or TauResourcePaths()
    skills_by_name: dict[str, Skill] = {}

    for skills_dir in resource_paths.skills_dirs:
        for skill in _load_skills_from_dir(skills_dir):
            skills_by_name[skill.name] = skill

    return sorted(skills_by_name.values(), key=lambda skill: skill.name)


def load_skills_with_diagnostics(
    paths: TauResourcePaths | None = None,
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    """Load skills and return non-fatal discovery diagnostics.

    Resource directories are loaded in increasing precedence order. Higher
    precedence resources replace lower precedence resources with the same name,
    and that replacement is reported as a diagnostic.
    """
    resource_paths = paths or TauResourcePaths()
    skills_by_name: dict[str, Skill] = {}
    diagnostics: list[ResourceDiagnostic] = []

    for skills_dir in resource_paths.skills_dirs:
        skills, directory_diagnostics = _load_skills_from_dir_with_diagnostics(skills_dir)
        diagnostics.extend(directory_diagnostics)
        for skill in skills:
            previous = skills_by_name.get(skill.name)
            if previous is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="skill",
                        name=skill.name,
                        path=skill.path,
                        message=f"overrides lower-precedence resource at {previous.path}",
                    )
                )
            skills_by_name[skill.name] = skill

    return sorted(skills_by_name.values(), key=lambda skill: skill.name), diagnostics


def expand_skill_command(text: str, skills: Sequence[Skill]) -> str | None:
    """Expand `/skill:name` prompt text, or return None for non-skill text."""
    stripped = text.strip()
    if not stripped.startswith("/skill:"):
        return None

    command, separator, request = stripped.partition(" ")
    name = command.removeprefix("/skill:").strip()
    if not name:
        raise ResourceError("Skill command must include a skill name")

    skill_by_name = {skill.name: skill for skill in skills}
    skill = skill_by_name.get(name)
    if skill is None:
        raise ResourceError(f"Unknown skill: {name}")

    additional_instructions = request.strip() if separator else None
    return format_skill_invocation(skill, additional_instructions)


def format_skill_invocation(
    skill: Skill,
    additional_instructions: str | None = None,
) -> str:
    """Format a full skill invocation prompt."""
    skill_block = (
        f'<skill name="{skill.name}" location="{skill.path}">\n'
        f"References are relative to {skill.path.parent}.\n\n"
        f"{skill.content.strip()}\n"
        "</skill>"
    )
    if additional_instructions and additional_instructions.strip():
        return f"{skill_block}\n\n{additional_instructions.strip()}"
    return skill_block


def parse_skill_invocation(text: str) -> SkillInvocation | None:
    """Parse Tau's expanded skill invocation message format."""
    match = re.match(
        r'^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n</skill>(?:\n\n([\s\S]+))?$',
        text,
    )
    if match is None:
        return None
    name, location, content, additional_instructions = match.groups()
    return SkillInvocation(
        name=name,
        location=location,
        content=content,
        additional_instructions=additional_instructions,
    )


def build_skill_index(skills: Sequence[Skill]) -> str:
    """Build a concise index of available skills for future system prompt assembly."""
    if not skills:
        return "Available skills: none"
    lines = ["Available skills:"]
    for skill in sorted(skills, key=lambda item: item.name):
        description = skill.description or "No description"
        lines.append(f"- {skill.name}: {description}")
    return "\n".join(lines)


def _load_skills_from_dir(skills_dir: Path) -> list[Skill]:
    skills, diagnostics = _load_skills_from_dir_with_diagnostics(skills_dir)
    if diagnostics:
        first = diagnostics[0]
        raise ResourceError(first.message)
    return skills


def _load_skills_from_dir_with_diagnostics(
    skills_dir: Path,
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    if not skills_dir.exists() or not skills_dir.is_dir():
        return [], []

    # In .agents/skills/ directories, bare .md files are ignored — only
    # SKILL.md files inside subdirectories are valid (Agent Skills standard).
    # In .tau/skills/ directories, any .md file is treated as a skill
    # (preserving the current behavior).
    agents_mode = skills_dir.parent.name == ".agents"

    skills: list[Skill] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen: set[str] = set()
    for path in sorted(skills_dir.iterdir(), key=lambda item: item.name):
        skill_path: Path | None = None
        name = path.stem
        if path.is_dir():
            skill_path = path / "SKILL.md"
            name = path.name
            if not skill_path.exists():
                continue
        elif path.is_file() and path.suffix.lower() == ".md" and not agents_mode:
            if path.name.upper() == "AGENTS.MD":
                continue
            skill_path = path
        else:
            continue

        if name in seen:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="skill",
                    name=name,
                    path=skill_path,
                    message=f"Duplicate skill name ignored in {skills_dir}",
                )
            )
            continue
        seen.add(name)
        try:
            skills.append(_load_skill(name, skill_path))
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="skill",
                    name=name,
                    path=skill_path,
                    message=f"could not read skill: {exc}",
                    severity="error",
                )
            )
    return skills, diagnostics


def _load_skill(name: str, path: Path) -> Skill:
    raw = path.read_text(encoding="utf-8")
    metadata, content = parse_markdown_resource(raw)
    description = metadata.get("description") or derive_description(content)
    return Skill(name=name, path=path, content=content, description=description)
