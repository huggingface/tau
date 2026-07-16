import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from pi_event_helpers import (
    assistant_done,
    assistant_error,
    assistant_start,
    text_delta,
    thinking_delta,
    tool_call_end,
)
from tau_agent import (
    AgentEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    SimpleCancellationToken,
    TextContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.provider_events import ThinkingDeltaEvent
from tau_agent.types import JSONValue
from tau_ai import CancellationToken, FakeProvider


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


def _tool(
    name: str,
    execute_fn,
) -> AgentTool:  # noqa: ANN001
    return AgentTool(
        name=name,
        label=name.title(),
        description=f"Run {name}.",
        parameters={"type": "object"},
        execute_fn=execute_fn,
    )


@pytest.mark.anyio
async def test_agent_loop_streams_canonical_nested_events() -> None:
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]
    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider(
        [[assistant_start(), text_delta("Hel"), text_delta("lo"), assistant_done(assistant)]]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.assistant_message_event.delta for event in updates] == ["Hel", "lo"]  # type: ignore[union-attr]
    assert messages == [messages[0], assistant]


@pytest.mark.anyio
async def test_agent_loop_nests_thinking_events_without_losing_final_message() -> None:
    messages: list[AgentMessage] = [UserMessage(content="Think briefly")]
    assistant = AssistantMessage(content="Done", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                thinking_delta("hidden "),
                thinking_delta("reasoning"),
                text_delta("Done"),
                assistant_done(assistant),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    nested = [
        event.assistant_message_event
        for event in events
        if isinstance(event, MessageUpdateEvent)
        and isinstance(event.assistant_message_event, ThinkingDeltaEvent)
    ]
    assert [event.delta for event in nested] == ["hidden ", "reasoning"]
    assert messages[-1] == assistant


@pytest.mark.anyio
async def test_agent_loop_executes_tool_and_emits_tool_result_message_lifecycle() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(
            content=[TextContent(text=f"contents of {arguments['path']}")],
            details={"path": arguments["path"]},
        )

    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first = AssistantMessage(content=[TextContent(text="Reading."), tool_call], model="fake")
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(tool_call), assistant_done(first, "toolUse")],
            [assistant_start(), text_delta("Done."), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="Read README.md")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("read", execute)],
        )
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.role == "toolResult"
    assert result.tool_name == "read"
    assert result.text == "contents of README.md"
    assert result.details == {"path": "README.md"}
    result_lifecycle = [
        event.type
        for event in events
        if isinstance(event, (MessageEndEvent,)) and event.message is result
    ]
    assert result_lifecycle == ["message_end"]
    assert [event.type for event in events].count("message_start") == 3
    assert provider.calls[1][2] == messages[:3]


@pytest.mark.anyio
async def test_agent_loop_passes_call_id_signal_and_progress_to_tool() -> None:
    observed: list[tuple[str, CancellationToken | None]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del arguments
        observed.append((tool_call_id, signal))
        assert on_update is not None
        on_update(AgentToolResult(content="working"))
        await asyncio.sleep(0)
        return AgentToolResult(content="done")

    call = ToolCall(id="call-1", name="work", arguments={})
    first = AssistantMessage(content=[call], model="fake")
    final = AssistantMessage(content="finished", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(final)],
        ]
    )
    signal = SimpleCancellationToken()

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=[UserMessage(content="work")],
            tools=[_tool("work", execute)],
            signal=signal,
        )
    )

    assert observed == [("call-1", signal)]
    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert [event.partial_result.text for event in updates] == ["working"]


@pytest.mark.anyio
async def test_agent_loop_records_unknown_tool_as_canonical_error_result() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    messages: list[AgentMessage] = [UserMessage(content="Use it")]
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert end.is_error is True
    assert end.result.text == "Tool missing not found"
    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.is_error is True
    assert result.text == "Tool missing not found"


@pytest.mark.anyio
async def test_agent_loop_converts_provider_error_to_assistant_error_message() -> None:
    messages: list[AgentMessage] = [UserMessage(content="hello")]
    provider = FakeProvider([[assistant_error("provider failed")]])

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "provider failed"


@pytest.mark.anyio
async def test_agent_loop_injects_steering_and_follow_up_messages() -> None:
    call = ToolCall(id="call-1", name="work", arguments={})

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content="ok")

    first = AssistantMessage(content=[call], model="fake")
    second = AssistantMessage(content="second", model="fake")
    third = AssistantMessage(content="third", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(second)],
            [assistant_start(), assistant_done(third)],
        ]
    )
    steering = [UserMessage(content="steer")]
    follow_up = [UserMessage(content="follow up")]

    def pop(queue: list[UserMessage]) -> tuple[UserMessage, ...]:
        return (queue.pop(0),) if queue else ()

    messages: list[AgentMessage] = [UserMessage(content="start")]
    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("work", execute)],
            get_steering_messages=lambda: pop(steering),
            get_follow_up_messages=lambda: pop(follow_up),
        )
    )

    assert [message.text for message in messages if isinstance(message, UserMessage)] == [
        "start",
        "steer",
        "follow up",
    ]
    assert len(provider.calls) == 3


@pytest.mark.anyio
async def test_agent_loop_stops_with_assistant_error_after_max_turns() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    messages: list[AgentMessage] = [UserMessage(content="loop")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "Agent stopped after max_turns=1"
    assert len(provider.calls) == 1
