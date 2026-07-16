from datetime import date
from pathlib import Path

from tau_agent import AgentTool, AgentToolResult
from tau_coding import Skill
from tau_coding.system_prompt import (
    BuildSystemPromptOptions,
    ProjectContextFile,
    build_system_prompt,
    collect_prompt_guidelines,
    format_available_tools,
    format_skills_for_prompt,
)
from tau_coding.tools import create_coding_tools


async def _unused_executor(
    tool_call_id: str,
    _arguments: object,
    signal: object | None = None,
    on_update: object | None = None,
) -> AgentToolResult:
    del tool_call_id, signal, on_update
    return AgentToolResult(content="")


def test_default_prompt_includes_tools_guidelines_date_and_cwd(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=tmp_path,
            tools=tools,
            current_date=date(2026, 6, 17),
        )
    )

    assert "You are an expert coding assistant operating inside Tau" in prompt
    assert "Available tools:\n- read: Read file contents" in prompt
    assert "- Use bash for file operations like ls, rg, find" in prompt
    assert "- Use read to examine files instead of cat or sed." in prompt
    assert "- Inspect relevant files and project instructions before editing" in prompt
    assert "- Do not overwrite or discard unrelated user changes" in prompt
    assert "- Report checks honestly; never claim a command passed unless you ran it" in prompt
    assert "Tau documentation (read only when the user asks about Tau itself" in prompt
    assert "adding models (docs/models.md)" in prompt
    assert "extensions (docs/extensions.md, examples/extensions/)" in prompt
    assert prompt.endswith(f"Current date: 2026-06-17\nCurrent working directory: {tmp_path}")


def test_tool_without_prompt_snippet_is_hidden_from_available_tools() -> None:
    tool = AgentTool(
        name="hidden",
        label="Hidden",
        description="Still sent to provider",
        parameters={"type": "object"},
        execute_fn=_unused_executor,  # type: ignore[arg-type]
    )

    assert format_available_tools([tool]) == "(none)"


def test_guidelines_are_deduplicated(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)
    duplicate = tools[0].prompt_guidelines[0]

    guidelines = collect_prompt_guidelines(tools, [duplicate])

    assert guidelines.count(duplicate) == 1


def test_custom_prompt_replaces_default_but_keeps_append_context_and_date(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=tmp_path,
            tools=create_coding_tools(cwd=tmp_path),
            custom_prompt="Custom base.",
            append_system_prompt="Extra rules.",
            context_files=(ProjectContextFile(path="/repo/AGENTS.md", content="Follow rules."),),
            current_date=date(2026, 6, 17),
        )
    )

    assert prompt.startswith("Custom base.\n\nExtra rules.")
    assert "Available tools:" not in prompt
    assert "Tau documentation" not in prompt
    assert '<project_instructions path="/repo/AGENTS.md">' in prompt
    assert "Follow rules." in prompt
    assert "Current date: 2026-06-17" in prompt


def test_empty_custom_prompt_is_still_custom(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=tmp_path,
            tools=create_coding_tools(cwd=tmp_path),
            custom_prompt="",
            append_system_prompt="Extra rules.",
            current_date=date(2026, 6, 17),
        )
    )

    assert prompt.startswith("\n\nExtra rules.")
    assert "Available tools:" not in prompt
    assert "Current date: 2026-06-17" in prompt


def test_skills_are_formatted_as_xml_and_escaped(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "review" / "SKILL.md"
    skill = Skill(
        name="review&check",
        path=skill_path,
        content="ignored",
        description="Review <code>",
    )

    formatted = format_skills_for_prompt([skill])

    assert "<available_skills>" in formatted
    assert "<name>review&amp;check</name>" in formatted
    assert "<description>Review &lt;code&gt;</description>" in formatted
    assert f"<location>{skill_path}</location>" in formatted


def test_skills_are_included_only_when_read_tool_is_available(tmp_path: Path) -> None:
    skill = Skill(name="testing", path=tmp_path / "testing.md", content="", description="Test")
    no_read_tool = AgentTool(
        name="custom",
        label="Custom",
        description="Custom",
        parameters={"type": "object"},
        execute_fn=_unused_executor,  # type: ignore[arg-type]
        prompt_snippet="Custom tool",
    )

    without_read = build_system_prompt(
        BuildSystemPromptOptions(cwd=tmp_path, tools=[no_read_tool], skills=[skill])
    )
    with_read = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=tmp_path, tools=create_coding_tools(cwd=tmp_path), skills=[skill]
        )
    )

    assert "<available_skills>" not in without_read
    assert "<available_skills>" in with_read
