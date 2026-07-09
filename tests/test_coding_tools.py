import asyncio
import json
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
    create_todo_tools,
    create_write_tool,
)
from tau_coding.tools import ToolInputError


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

    assert [tool.name for tool in tools] == [
        "read",
        "write",
        "edit",
        "bash",
        "todo_write",
        "todo_read",
    ]
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

    result = await tool.execute({"path": "notes.txt", "offset": 2, "limit": 1})

    assert result.ok is True
    assert result.name == "read"
    assert result.content == "two\n\n[2 more lines in file. Use offset=3 to continue.]"
    assert result.data is not None
    assert result.data["path"] == str(path)
    assert isinstance(result.data["truncation"], dict)


@pytest.mark.anyio
async def test_read_tool_treats_zero_offset_as_start_of_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute({"path": "notes.txt", "offset": 0, "limit": 1})

    assert result.ok is True
    assert result.content == "one\n\n[3 more lines in file. Use offset=2 to continue.]"


@pytest.mark.anyio
async def test_write_tool_creates_parent_directories(tmp_path: Path) -> None:
    tool = create_write_tool(cwd=tmp_path)

    result = await tool.execute({"path": "nested/file.txt", "content": "hello"})

    assert result.ok is True
    assert (tmp_path / "nested" / "file.txt").read_text() == "hello"


@pytest.mark.anyio
async def test_edit_tool_applies_multiple_exact_replacements(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("alpha\nbeta\ngamma\n")
    tool = create_edit_tool(cwd=tmp_path)

    result = await tool.execute(
        {
            "path": "file.txt",
            "edits": [
                {"oldText": "alpha", "newText": "one"},
                {"oldText": "gamma", "newText": "three"},
            ],
        }
    )

    assert result.ok is True
    assert path.read_text() == "one\nbeta\nthree\n"


@pytest.mark.anyio
async def test_edit_tool_rolls_back_when_any_edit_fails(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    original = "alpha\nbeta\ngamma\n"
    path.write_text(original)
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Could not find edits\\[1\\]"):
        await tool.execute(
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "alpha", "newText": "one"},
                    {"oldText": "missing", "newText": "nope"},
                ],
            }
        )

    assert path.read_text() == original


@pytest.mark.anyio
async def test_edit_tool_requires_unique_matches(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("repeat\nrepeat\n")
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await tool.execute(
            {
                "path": "file.txt",
                "edits": [{"oldText": "repeat", "newText": "once"}],
            }
        )


@pytest.mark.anyio
async def test_bash_tool_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute({"command": "printf hello"})

    assert result.ok is True
    assert result.content == "hello"
    assert result.data is not None
    assert result.data["exit_code"] == 0
    assert result.data["timed_out"] is False


@pytest.mark.anyio
async def test_create_coding_tools_applies_shell_command_prefix(
    tmp_path: Path,
) -> None:
    tools = create_coding_tools(
        cwd=tmp_path,
        shell_command_prefix="shopt -s expand_aliases\nalias greet='printf coding-tool-alias'",
    )
    bash_tool = next(tool for tool in tools if tool.name == "bash")

    result = await bash_tool.execute({"command": "greet"})

    assert result.ok is True
    assert result.content == "coding-tool-alias"
    assert result.data is not None
    assert result.data["shell_command_prefix_applied"] is True


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

    result = await tool.execute({"command": "greet"})

    assert result.ok is True
    assert result.content == "alias-output"
    assert result.data is not None
    assert result.data["shell_command_prefix_applied"] is True
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_reports_timeout(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute({"command": "sleep 1", "timeout": 0.01})

    assert result.ok is False
    assert result.data is not None
    assert result.data["timed_out"] is True
    assert "timed out" in result.content


@pytest.mark.anyio
async def test_bash_tool_timeout_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    marker = tmp_path / "marker"

    start = monotonic()
    result = await tool.execute({"command": "(sleep 0.25; touch marker) & wait", "timeout": 0.01})
    duration = monotonic() - start
    await asyncio.sleep(0.35)

    assert result.ok is False
    assert result.data is not None
    assert result.data["timed_out"] is True
    assert duration < 0.5
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_cancellation_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    token = FakeCancellationToken()

    task = asyncio.create_task(tool.execute({"command": "sleep 1 & wait"}, signal=token))
    await asyncio.sleep(0.05)
    token.cancel()
    start = monotonic()
    result = await task
    duration = monotonic() - start

    assert result.ok is False
    assert result.data is not None
    assert result.data["cancelled"] is True
    assert "cancelled" in result.content
    assert duration < 0.5


# ---------------------------------------------------------------------------
# todo_write / todo_read
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_todo_read_returns_empty_list_initially() -> None:
    _write_tool, read_tool = create_todo_tools()

    result = await read_tool.execute({})

    assert result.ok is True
    assert result.name == "todo_read"
    assert result.content == "[]"
    assert result.data == {"count": 0}


@pytest.mark.anyio
async def test_todo_write_saves_todos_and_read_returns_them() -> None:
    write_tool, read_tool = create_todo_tools()
    todos = [
        {"id": "1", "content": "Write tests", "status": "pending", "priority": "high"},
        {"id": "2", "content": "Run linter", "status": "in_progress", "priority": "medium"},
    ]

    write_result = await write_tool.execute({"todos": todos})

    assert write_result.ok is True
    assert write_result.name == "todo_write"
    assert "2 items saved" in write_result.content
    assert write_result.data == {"count": 2}

    read_result = await read_tool.execute({})
    parsed = json.loads(read_result.content)
    assert len(parsed) == 2
    assert parsed[0] == {
        "id": "1",
        "content": "Write tests",
        "status": "pending",
        "priority": "high",
    }
    assert parsed[1]["status"] == "in_progress"


@pytest.mark.anyio
async def test_todo_write_singular_item_message() -> None:
    write_tool, _read_tool = create_todo_tools()
    todos = [{"id": "1", "content": "Only task", "status": "pending", "priority": "low"}]

    result = await write_tool.execute({"todos": todos})

    assert "1 item saved" in result.content


@pytest.mark.anyio
async def test_todo_write_replaces_list_on_second_call() -> None:
    write_tool, read_tool = create_todo_tools()
    first = [{"id": "1", "content": "First", "status": "pending", "priority": "low"}]
    second = [{"id": "2", "content": "Second", "status": "completed", "priority": "high"}]

    await write_tool.execute({"todos": first})
    await write_tool.execute({"todos": second})

    parsed = json.loads((await read_tool.execute({})).content)
    assert len(parsed) == 1
    assert parsed[0]["content"] == "Second"


@pytest.mark.anyio
async def test_todo_write_clears_list_when_given_empty_array() -> None:
    write_tool, read_tool = create_todo_tools()
    initial = [{"id": "1", "content": "Task", "status": "pending", "priority": "low"}]
    await write_tool.execute({"todos": initial})

    await write_tool.execute({"todos": []})

    read_result = await read_tool.execute({})
    assert read_result.content == "[]"
    assert read_result.data == {"count": 0}


@pytest.mark.anyio
async def test_todo_write_rejects_invalid_status() -> None:
    write_tool, _read_tool = create_todo_tools()

    with pytest.raises(ToolInputError, match="status must be one of"):
        await write_tool.execute(
            {"todos": [{"id": "1", "content": "Task", "status": "done", "priority": "high"}]}
        )


@pytest.mark.anyio
async def test_todo_write_rejects_invalid_priority() -> None:
    write_tool, _read_tool = create_todo_tools()

    with pytest.raises(ToolInputError, match="priority must be one of"):
        await write_tool.execute(
            {"todos": [{"id": "1", "content": "Task", "status": "pending", "priority": "urgent"}]}
        )


@pytest.mark.anyio
async def test_todo_write_rejects_missing_required_field() -> None:
    write_tool, _read_tool = create_todo_tools()

    with pytest.raises(ToolInputError, match="id must be a string"):
        await write_tool.execute(
            {"todos": [{"content": "No id", "status": "pending", "priority": "low"}]}
        )


@pytest.mark.anyio
async def test_todo_write_rejects_non_list_todos() -> None:
    write_tool, _read_tool = create_todo_tools()

    with pytest.raises(ToolInputError, match="todos must be an array"):
        await write_tool.execute({"todos": "not a list"})


@pytest.mark.anyio
async def test_todo_write_accepts_json_string_todos() -> None:
    write_tool, read_tool = create_todo_tools()
    expected = {"id": "1", "content": "From JSON string", "status": "pending", "priority": "low"}
    todos_json = json.dumps([expected])

    result = await write_tool.execute({"todos": todos_json})
    assert result.ok is True

    read_result = await read_tool.execute({})
    parsed = json.loads(read_result.content)
    assert parsed == [expected]


@pytest.mark.anyio
async def test_todo_tools_from_different_create_calls_do_not_share_state() -> None:
    write_a, read_a = create_todo_tools()
    _write_b, read_b = create_todo_tools()

    await write_a.execute(
        {
            "todos": [
                {"id": "1", "content": "Session A task", "status": "pending", "priority": "high"}
            ]
        }
    )

    result_b = await read_b.execute({})
    assert result_b.content == "[]"


def test_todo_write_tool_has_expected_prompt_metadata() -> None:
    write_tool, _read_tool = create_todo_tools()

    assert write_tool.prompt_snippet == "Write or update the todo list"
    assert len(write_tool.prompt_guidelines) == 1
    assert "todo_write" in write_tool.prompt_guidelines[0]


def test_todo_read_tool_has_expected_prompt_metadata() -> None:
    _write_tool, read_tool = create_todo_tools()

    assert read_tool.prompt_snippet == "Read the current todo list"
    assert len(read_tool.prompt_guidelines) == 1
    assert "todo_read" in read_tool.prompt_guidelines[0]
