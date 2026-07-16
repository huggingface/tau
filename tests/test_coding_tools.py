import asyncio
import shlex
from pathlib import Path
from time import monotonic

import pytest

from tau_coding import (
    create_bash_tool,
    create_coding_tools,
    create_edit_tool,
    create_edit_tool_definition,
    create_read_tool,
    create_read_tool_definition,
    create_write_tool,
)


class FakeCancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


@pytest.mark.anyio
async def test_create_coding_tools_returns_initial_tool_set(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)

    assert [tool.name for tool in tools] == ["read", "write", "edit", "bash"]
    edit_tool = tools[2]
    assert edit_tool.prompt_snippet is not None
    assert "Use edit for precise changes" in edit_tool.prompt_guidelines[0]


def test_tool_definitions_expose_pi_style_prompt_metadata(tmp_path: Path) -> None:
    definition = create_edit_tool_definition(cwd=tmp_path)

    assert definition.prompt_snippet.startswith("Make precise file edits")
    assert len(definition.prompt_guidelines) == 4


def test_read_tool_schema_defines_line_controls_as_integers(tmp_path: Path) -> None:
    definition = create_read_tool_definition(cwd=tmp_path)
    properties = definition.input_schema["properties"]

    assert isinstance(properties, dict)
    assert properties["offset"]["type"] == "integer"
    assert properties["limit"]["type"] == "integer"


@pytest.mark.anyio
async def test_read_tool_reads_file_with_offset_and_limit(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "notes.txt", "offset": 2, "limit": 1})

    assert result.text
    assert result.text == "two\n\n[2 more lines in file. Use offset=3 to continue.]"
    assert result.details is not None
    assert result.details["path"] == str(path)
    assert isinstance(result.details["truncation"], dict)


@pytest.mark.anyio
async def test_read_tool_treats_zero_offset_as_start_of_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "notes.txt", "offset": 0, "limit": 1})

    assert result.text
    assert result.text == "one\n\n[3 more lines in file. Use offset=2 to continue.]"


@pytest.mark.anyio
async def test_write_tool_creates_parent_directories(tmp_path: Path) -> None:
    tool = create_write_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "nested/file.txt", "content": "hello"})

    assert result.text
    assert (tmp_path / "nested" / "file.txt").read_text() == "hello"


@pytest.mark.anyio
async def test_edit_tool_applies_multiple_exact_replacements(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("alpha\nbeta\ngamma\n")
    tool = create_edit_tool(cwd=tmp_path)

    result = await tool.execute(
        "test-call",
        {
            "path": "file.txt",
            "edits": [
                {"oldText": "alpha", "newText": "one"},
                {"oldText": "gamma", "newText": "three"},
            ],
        },
    )

    assert result.text
    assert path.read_text() == "one\nbeta\nthree\n"


@pytest.mark.anyio
async def test_edit_tool_rolls_back_when_any_edit_fails(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    original = "alpha\nbeta\ngamma\n"
    path.write_text(original)
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Could not find edits\\[1\\]"):
        await tool.execute(
            "test-call",
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "alpha", "newText": "one"},
                    {"oldText": "missing", "newText": "nope"},
                ],
            },
        )

    assert path.read_text() == original


@pytest.mark.anyio
async def test_edit_tool_requires_unique_matches(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("repeat\nrepeat\n")
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await tool.execute(
            "test-call",
            {
                "path": "file.txt",
                "edits": [{"oldText": "repeat", "newText": "once"}],
            },
        )


@pytest.mark.anyio
async def test_bash_tool_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"command": "printf hello"})

    assert result.text
    assert result.text == "hello"
    assert result.details is not None
    assert result.details["exit_code"] == 0
    assert result.details["timed_out"] is False


@pytest.mark.anyio
async def test_create_coding_tools_applies_shell_command_prefix(
    tmp_path: Path,
) -> None:
    tools = create_coding_tools(
        cwd=tmp_path,
        shell_command_prefix="shopt -s expand_aliases\nalias greet='printf coding-tool-alias'",
    )
    bash_tool = next(tool for tool in tools if tool.name == "bash")

    result = await bash_tool.execute("test-call", {"command": "greet"})

    assert result.text
    assert result.text == "coding-tool-alias"
    assert result.details is not None
    assert result.details["shell_command_prefix_applied"] is True


@pytest.mark.anyio
async def test_bash_tool_applies_opt_in_shell_command_prefix(tmp_path: Path) -> None:
    rc_path = tmp_path / ".zshrc"
    marker = tmp_path / "sourced"
    rc_path.write_text(
        f"alias greet='printf alias-output'\ntouch {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    prefix = f"shopt -s expand_aliases\neval \"$(grep '^alias ' {shlex.quote(str(rc_path))})\""
    tool = create_bash_tool(cwd=tmp_path, shell_command_prefix=prefix)

    result = await tool.execute("test-call", {"command": "greet"})

    assert result.text
    assert result.text == "alias-output"
    assert result.details is not None
    assert result.details["shell_command_prefix_applied"] is True
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_reports_timeout(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"command": "sleep 1", "timeout": 0.01})

    assert result.details is not None
    assert result.details is not None
    assert result.details["timed_out"] is True
    assert "timed out" in result.text


@pytest.mark.anyio
async def test_bash_tool_timeout_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    marker = tmp_path / "marker"

    start = monotonic()
    result = await tool.execute(
        "test-call", {"command": "(sleep 0.25; touch marker) & wait", "timeout": 0.01}
    )
    duration = monotonic() - start
    await asyncio.sleep(0.35)

    assert result.details is not None
    assert result.details is not None
    assert result.details["timed_out"] is True
    assert duration < 0.5
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_cancellation_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    token = FakeCancellationToken()

    task = asyncio.create_task(
        tool.execute("test-call", {"command": "sleep 1 & wait"}, signal=token)
    )
    await asyncio.sleep(0.05)
    token.cancel()
    start = monotonic()
    result = await task
    duration = monotonic() - start

    assert result.details is not None
    assert result.details is not None
    assert result.details["cancelled"] is True
    assert "cancelled" in result.text
    assert duration < 0.5
