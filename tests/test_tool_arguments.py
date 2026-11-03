"""tau_agent checks tool-call arguments against the tool's declared schema."""

from collections.abc import AsyncIterator, Mapping

import pytest

from pi_event_helpers import assistant_done, assistant_start, tool_call_end
from tau_agent import (
    AgentEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.tool_arguments import argument_problems
from tau_agent.tools import ToolArgumentPreparer
from tau_agent.types import JSONValue
from tau_ai import CancellationToken, FakeProvider

READ_SCHEMA: dict[str, JSONValue] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "limit": {"type": "integer"},
        "mode": {"type": "string", "enum": ["text", "bytes"]},
        "ranges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "integer"}},
                "required": ["start"],
            },
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


async def _run(
    schema: Mapping[str, JSONValue],
    arguments: dict[str, JSONValue],
    prepare: ToolArgumentPreparer | None = None,
) -> tuple[ToolResultMessage, list[Mapping[str, JSONValue]]]:
    """Run one `read` call with `arguments`; return its result and what the tool got."""
    seen: list[Mapping[str, JSONValue]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        seen.append(arguments)
        return AgentToolResult(content=[TextContent(text="ran")], details={})

    tool = AgentTool(
        name="read",
        label="Read",
        description="Read a file.",
        parameters=schema,
        execute_fn=execute,
        prepare_arguments=prepare,
    )
    call = ToolCall(id="call-1", name="read", arguments=arguments)
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    messages: list[AgentMessage] = [UserMessage(content="Read it")]
    events: AsyncIterator[AgentEvent] = run_agent_loop(
        provider=provider,
        model="fake",
        system="You are Tau.",
        messages=messages,
        tools=[tool],
        max_turns=1,
    )
    _ = [event async for event in events]
    result = next(m for m in messages if isinstance(m, ToolResultMessage))
    return result, seen


@pytest.mark.anyio
async def test_missing_required_argument_is_refused_before_the_tool_runs() -> None:
    result, seen = await _run(READ_SCHEMA, {"limit": 5})

    assert seen == []
    assert result.is_error is True
    assert result.text == (
        'Invalid arguments for read: missing required "path". Send the call again with these fixed.'
    )


@pytest.mark.anyio
async def test_one_error_lists_every_problem() -> None:
    result, seen = await _run(
        READ_SCHEMA,
        {"limit": "10", "mode": "lines", "colour": "red", "ranges": [{"start": 1}, {}]},
    )

    assert seen == []
    assert result.is_error is True
    assert result.text == (
        "Invalid arguments for read: "
        'missing required "path"; '
        '"limit" must be integer, got string; '
        '"mode" must be one of "text", "bytes", got "lines"; '
        'unknown argument "colour"; '
        'missing required "ranges[1].start". '
        "Send the call again with these fixed."
    )


@pytest.mark.anyio
async def test_wrong_type_and_unknown_enum_value_are_named() -> None:
    result, seen = await _run(
        READ_SCHEMA, {"path": "a.py", "limit": True, "mode": "pages", "ranges": "1-3"}
    )

    assert seen == []
    assert '"limit" must be integer, got boolean' in result.text
    assert '"mode" must be one of "text", "bytes", got "pages"' in result.text
    assert '"ranges" must be array, got string' in result.text


@pytest.mark.anyio
async def test_valid_arguments_reach_the_tool_unchanged() -> None:
    arguments: dict[str, JSONValue] = {
        "path": "a.py",
        "limit": 10.0,
        "mode": "bytes",
        "ranges": [{"start": 2}],
    }

    result, seen = await _run(READ_SCHEMA, arguments)

    assert result.is_error is False
    assert result.text == "ran"
    assert seen == [arguments]
    assert isinstance(seen[0]["limit"], float)


@pytest.mark.anyio
async def test_a_tool_with_no_schema_is_not_checked() -> None:
    result, seen = await _run({}, {"anything": [1, "two"], "path": 3})

    assert result.is_error is False
    assert seen == [{"anything": [1, "two"], "path": 3}]


@pytest.mark.anyio
async def test_unparsed_arguments_are_left_to_the_tool() -> None:
    result, seen = await _run(READ_SCHEMA, {"_raw_arguments": '{"path": "a.py'})

    assert result.is_error is False
    assert seen == [{"_raw_arguments": '{"path": "a.py'}]


@pytest.mark.anyio
async def test_an_optional_argument_sent_as_null_counts_as_not_sent() -> None:
    result, seen = await _run(READ_SCHEMA, {"path": "a.py", "limit": None, "mode": None})

    assert result.is_error is False
    assert seen == [{"path": "a.py", "limit": None, "mode": None}]


@pytest.mark.anyio
async def test_a_required_argument_sent_as_null_is_refused() -> None:
    result, seen = await _run(READ_SCHEMA, {"path": None})

    assert seen == []
    assert '"path" must be string, got null' in result.text


@pytest.mark.anyio
async def test_prepared_arguments_are_checked_and_reach_the_tool() -> None:
    def prepare(arguments: object) -> Mapping[str, JSONValue]:
        assert isinstance(arguments, Mapping)
        return {"path": arguments["file"]}

    result, seen = await _run(READ_SCHEMA, {"file": "a.py"}, prepare)

    assert result.is_error is False
    assert seen == [{"path": "a.py"}]


@pytest.mark.anyio
async def test_a_failing_preparer_refuses_the_call() -> None:
    def prepare(arguments: object) -> Mapping[str, JSONValue]:
        raise ValueError("cannot read these arguments")

    result, seen = await _run(READ_SCHEMA, {"path": "a.py"}, prepare)

    assert seen == []
    assert result.is_error is True
    assert "cannot read these arguments" in result.text


def test_additional_properties_schema_and_type_lists_are_checked() -> None:
    schema: dict[str, JSONValue] = {
        "type": "object",
        "properties": {"limit": {"type": ["integer", "string"]}},
        "additionalProperties": {"type": "integer"},
    }

    assert argument_problems(schema, {"limit": "all", "extra": 1}) == []
    assert argument_problems(schema, {"limit": 1.5, "extra": "x"}) == [
        '"limit" must be integer or string, got number',
        '"extra" must be integer, got string',
    ]
