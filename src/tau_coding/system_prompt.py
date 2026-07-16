"""System prompt assembly for Tau coding sessions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from tau_agent.tools import AgentTool
from tau_coding.self_docs import tau_docs_path, tau_examples_path, tau_readme_path
from tau_coding.skills import Skill


@dataclass(frozen=True, slots=True)
class ProjectContextFile:
    """A project instruction file included in the system prompt."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class BuildSystemPromptOptions:
    """Options used to build Tau's system prompt."""

    cwd: Path
    tools: Sequence[AgentTool] = ()
    skills: Sequence[Skill] = ()
    custom_prompt: str | None = None
    append_system_prompt: str | None = None
    context_files: Sequence[ProjectContextFile] = ()
    current_date: date | None = None
    extra_guidelines: Sequence[str] = field(default_factory=tuple)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Build a deterministic Pi-style system prompt for Tau."""
    current_date = options.current_date or date.today()
    cwd = _format_path(options.cwd)
    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""

    if options.custom_prompt is not None:
        prompt = options.custom_prompt
        prompt += append_section
        prompt += format_project_context(options.context_files)
        if _has_tool(options.tools, "read"):
            prompt += format_skills_for_prompt(options.skills)
        prompt += f"\nCurrent date: {current_date.isoformat()}"
        prompt += f"\nCurrent working directory: {cwd}"
        return prompt

    prompt = (
        "You are an expert coding assistant operating inside Tau, a coding agent harness. "
        "You help users by reading files, executing commands, editing code, and writing new files."
        f"\n\nAvailable tools:\n{format_available_tools(options.tools)}"
        "\n\nIn addition to the tools above, you may have access to other custom tools "
        "depending on the project."
        f"\n\nGuidelines:\n{format_guidelines(options.tools, options.extra_guidelines)}"
        f"\n\n{format_tau_documentation()}"
    )

    prompt += append_section
    prompt += format_project_context(options.context_files)
    if _has_tool(options.tools, "read"):
        prompt += format_skills_for_prompt(options.skills)
    prompt += f"\nCurrent date: {current_date.isoformat()}"
    prompt += f"\nCurrent working directory: {cwd}"
    return prompt


def format_tau_documentation() -> str:
    """Format Pi-style routing hints to Tau's installed reference material."""
    readme_path = _format_path(tau_readme_path())
    docs_path = _format_path(tau_docs_path())
    examples_path = _format_path(tau_examples_path())
    return (
        "Tau documentation (read only when the user asks about Tau itself, its SDK, "
        "extensions, skills, providers, models, commands, or TUI):\n"
        f"- Main documentation: {readme_path}\n"
        f"- Additional docs: {docs_path}\n"
        f"- Examples: {examples_path} (extensions and custom tools)\n"
        "- When reading Tau docs or examples, resolve docs/... under Additional docs and "
        "examples/... under Examples, not the current working directory\n"
        "- When asked about: extensions (docs/extensions.md, examples/extensions/), "
        "skills and prompt templates (docs/skills.md), providers and adding models "
        "(docs/models.md), CLI and slash commands (docs/cli.md), TUI usage "
        "(docs/tui.md), Tau architecture and packages (docs/architecture.md)\n"
        "- When working on Tau topics, read the docs and examples, and follow .md "
        "cross-references before implementing\n"
        "- Always read relevant Tau .md files completely and follow links to related docs"
    )


def format_available_tools(tools: Sequence[AgentTool]) -> str:
    """Format visible tools using prompt snippets."""
    lines = [f"- {tool.name}: {tool.prompt_snippet}" for tool in tools if tool.prompt_snippet]
    return "\n".join(lines) if lines else "(none)"


def collect_prompt_guidelines(
    tools: Sequence[AgentTool], extra_guidelines: Sequence[str] = ()
) -> list[str]:
    """Collect and de-duplicate system prompt guidelines."""
    names = {tool.name for tool in tools}
    guidelines: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = value.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        guidelines.append(normalized)

    has_bash = "bash" in names
    has_exploration_tools = bool({"grep", "find", "ls"} & names)
    if has_bash and not has_exploration_tools:
        add("Use bash for file operations like ls, rg, find")
    elif has_bash and has_exploration_tools:
        add(
            "Prefer grep/find/ls tools over bash for file exploration (faster, respects .gitignore)"
        )

    for tool in tools:
        for guideline in tool.prompt_guidelines:
            add(guideline)
    for guideline in extra_guidelines:
        add(guideline)

    add("Inspect relevant files and project instructions before editing")
    add("Make focused changes that preserve the project's architecture and style")
    add("Do not overwrite or discard unrelated user changes")
    add("Use the project's documented commands and package manager")
    add("Run relevant tests, formatting, linting, and type checks after changes")
    add("Report checks honestly; never claim a command passed unless you ran it")
    add("Ask before destructive operations or materially ambiguous design choices")
    add("Be concise in your responses")
    add("Show file paths clearly when working with files")
    return guidelines


def format_guidelines(tools: Sequence[AgentTool], extra_guidelines: Sequence[str] = ()) -> str:
    """Format prompt guidelines as markdown bullets."""
    return "\n".join(
        f"- {guideline}" for guideline in collect_prompt_guidelines(tools, extra_guidelines)
    )


def format_project_context(context_files: Sequence[ProjectContextFile]) -> str:
    """Format project context files using Pi's XML-like wrapper."""
    if not context_files:
        return ""

    lines = [
        "\n\n<project_context>",
        "",
        "Project-specific instructions and guidelines:",
        "",
    ]
    for context_file in context_files:
        lines.append(f'<project_instructions path="{escape(context_file.path)}">')
        lines.append(context_file.content)
        lines.append("</project_instructions>")
        lines.append("")
    lines.append("</project_context>")
    return "\n".join(lines)


def format_skills_for_prompt(skills: Sequence[Skill]) -> str:
    """Format skills for inclusion in a system prompt using Pi's XML style."""
    if not skills:
        return ""

    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Read the full skill file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory "
        "(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in sorted(skills, key=lambda item: item.name):
        description = skill.description or "No description"
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(skill.name)}</name>",
                f"    <description>{escape(description)}</description>",
                f"    <location>{escape(str(skill.path))}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _has_tool(tools: Sequence[AgentTool], name: str) -> bool:
    return any(tool.name == name for tool in tools)


def _format_path(path: Path) -> str:
    return str(path).replace("\\", "/")
