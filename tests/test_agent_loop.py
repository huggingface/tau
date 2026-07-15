import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import cast

import pytest

from tau_agent import (
    AgentEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    QueueUpdateEvent,
    RetryEvent,
    SimpleCancellationToken,
    ThinkingDeltaEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    ToolUpdateCallback,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.types import JSONValue
from tau_ai import (
    CancellationToken,
    FakeProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
)


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_agent_loop_streams_text_and_appends_assistant_message() -> None:
    messages = [UserMessage(content="Say hello")]
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=assistant, finish_reason="stop"),
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

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_delta",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert messages == [UserMessage(content="Say hello"), assistant]


@pytest.mark.anyio
async def test_agent_loop_streams_thinking_deltas_without_recording_them() -> None:
    messages = [UserMessage(content="Think briefly")]
    assistant = AssistantMessage(content="Done")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderThinkingDeltaEvent(delta="hidden "),
                ProviderThinkingDeltaEvent(delta="reasoning"),
                ProviderTextDeltaEvent(delta="Done"),
                ProviderResponseEndEvent(message=assistant, finish_reason="stop"),
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

    thinking_events = [event for event in events if isinstance(event, ThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["hidden ", "reasoning"]
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "thinking_delta",
        "thinking_delta",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert messages == [UserMessage(content="Think briefly"), assistant]


@pytest.mark.anyio
async def test_agent_loop_executes_tools_and_continues_until_no_tool_calls() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call-1",
            name="read",
            ok=True,
            content=f"contents of {arguments['path']}",
            data={"path": arguments["path"]},
            details={"source": "fake"},
        )

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first_assistant = AssistantMessage(content="I'll read it.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="README.md contains project documentation.")
    messages = [UserMessage(content="Read README.md")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first_assistant, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta=final_assistant.content),
                ProviderResponseEndEvent(message=final_assistant, finish_reason="stop"),
            ],
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "turn_end",
        "turn_start",
        "message_start",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert messages == [
        UserMessage(content="Read README.md"),
        first_assistant,
        ToolResultMessage(
            tool_call_id="call-1",
            name="read",
            content="contents of README.md",
            ok=True,
            data={"path": "README.md"},
            details={"source": "fake"},
        ),
        final_assistant,
    ]
    assert len(provider.calls) == 2
    assert provider.calls[1][2] == messages[:3]


@pytest.mark.anyio
async def test_agent_loop_passes_cancellation_signal_to_tools() -> None:
    observed: list[CancellationToken | None] = []

    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
    ) -> AgentToolResult:
        del arguments
        observed.append(signal)
        return AgentToolResult(tool_call_id="call-1", name="read", ok=True, content="ok")

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first_assistant = AssistantMessage(content="I'll read it.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="Done.")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first_assistant),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final_assistant),
            ],
        ]
    )
    signal = SimpleCancellationToken()

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=[UserMessage(content="Read README.md")],
            tools=[tool],
            signal=signal,
        )
    )

    assert observed
    assert observed[0] is signal


@pytest.mark.anyio
async def test_agent_loop_records_cancelled_results_for_skipped_tool_calls() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: SimpleCancellationToken | None = None,
    ) -> AgentToolResult:
        del arguments
        if signal is not None:
            signal.cancel()
        return AgentToolResult(tool_call_id="call-1", name="read", ok=True, content="ok")

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_calls = [
        ToolCall(id="call-1", name="read", arguments={"path": "README.md"}),
        ToolCall(id="call-2", name="read", arguments={"path": "pyproject.toml"}),
    ]
    assistant = AssistantMessage(content="I'll read both.", tool_calls=tool_calls)
    messages = [UserMessage(content="Read project files")]
    provider = FakeProvider(
        [[ProviderResponseStartEvent(model="fake"), ProviderResponseEndEvent(message=assistant)]]
    )
    signal = SimpleCancellationToken()

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
            signal=signal,
        )
    )

    assert messages == [
        UserMessage(content="Read project files"),
        assistant,
        ToolResultMessage(tool_call_id="call-1", name="read", content="ok", ok=True),
        ToolResultMessage(
            tool_call_id="call-2",
            name="read",
            content="Tool call cancelled",
            ok=False,
            error="Tool call cancelled",
        ),
    ]
    assert [event.type for event in events].count("tool_execution_end") == 2


@pytest.mark.anyio
async def test_agent_loop_injects_steering_after_tool_batch() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call-1",
            name="read",
            ok=True,
            content=f"contents of {arguments['path']}",
        )

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first_assistant = AssistantMessage(content="I'll read it.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="Updated plan.")
    messages = [UserMessage(content="Read README.md")]
    steering_queue = [UserMessage(content="Also summarize it")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first_assistant, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final_assistant, finish_reason="stop"),
            ],
        ]
    )

    def get_steering_messages() -> tuple[UserMessage, ...]:
        if not steering_queue:
            return ()
        return (steering_queue.pop(0),)

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
            get_steering_messages=get_steering_messages,
            get_queue_update=lambda: QueueUpdateEvent(),
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "turn_end",
        "message_start",
        "message_end",
        "queue_update",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert provider.calls[1][2] == messages[:4]
    assert messages == [
        UserMessage(content="Read README.md"),
        first_assistant,
        ToolResultMessage(
            tool_call_id="call-1",
            name="read",
            content="contents of README.md",
            ok=True,
        ),
        UserMessage(content="Also summarize it"),
        final_assistant,
    ]


@pytest.mark.anyio
async def test_agent_loop_injects_follow_up_only_when_run_would_stop() -> None:
    first_assistant = AssistantMessage(content="Initial answer.")
    final_assistant = AssistantMessage(content="Follow-up answer.")
    messages = [UserMessage(content="Start")]
    follow_up_queue = [UserMessage(content="One more thing")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first_assistant, finish_reason="stop"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final_assistant, finish_reason="stop"),
            ],
        ]
    )

    def get_follow_up_messages() -> tuple[UserMessage, ...]:
        if not follow_up_queue:
            return ()
        return (follow_up_queue.pop(0),)

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            get_follow_up_messages=get_follow_up_messages,
            get_queue_update=lambda: QueueUpdateEvent(),
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "message_start",
        "message_end",
        "queue_update",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert len(provider.calls) == 2
    assert provider.calls[1][2] == messages[:3]
    assert messages == [
        UserMessage(content="Start"),
        first_assistant,
        UserMessage(content="One more thing"),
        final_assistant,
    ]


@pytest.mark.anyio
async def test_agent_loop_records_unknown_tool_as_failed_tool_result() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content="I'll use a tool.", tool_calls=[tool_call])
    messages = [UserMessage(content="Use a missing tool")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
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
            max_turns=1,
        )
    )

    tool_end_events = [event for event in events if isinstance(event, ToolExecutionEndEvent)]

    assert tool_end_events[0].result.ok is False
    assert tool_end_events[0].result.error == "Unknown tool: missing"
    assert messages[-1] == ToolResultMessage(
        tool_call_id="call-1",
        name="missing",
        content="Unknown tool: missing",
        ok=False,
        error="Unknown tool: missing",
    )


@pytest.mark.anyio
async def test_agent_loop_converts_provider_error_to_agent_error() -> None:
    messages = [UserMessage(content="hello")]
    provider = FakeProvider([[ProviderErrorEvent(message="provider failed")]])

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    errors = [event for event in events if isinstance(event, ErrorEvent)]

    assert errors == [ErrorEvent(message="provider failed", recoverable=False)]
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "error",
        "turn_end",
        "agent_end",
    ]
    assert messages == [UserMessage(content="hello")]


@pytest.mark.anyio
async def test_agent_loop_forwards_provider_retry_events() -> None:
    messages = [UserMessage(content="hello")]
    assistant = AssistantMessage(content="ok")
    provider = FakeProvider(
        [
            [
                ProviderRetryEvent(
                    attempt=2,
                    max_attempts=3,
                    delay_seconds=0,
                    message="Retrying provider request 2/3 after HTTP 503.",
                    data={"status_code": 503},
                ),
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant),
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

    retries = [event for event in events if isinstance(event, RetryEvent)]

    assert retries == [
        RetryEvent(
            attempt=2,
            max_attempts=3,
            delay_seconds=0,
            message="Retrying provider request 2/3 after HTTP 503.",
            data={"status_code": 503},
        )
    ]
    assert messages == [UserMessage(content="hello"), assistant]


@pytest.mark.anyio
async def test_agent_loop_has_no_default_max_turn_limit() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    looping_assistant = AssistantMessage(content="Again.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="Done.")
    messages = [UserMessage(content="loop for a while")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=looping_assistant, finish_reason="tool_calls"),
            ]
            for _ in range(9)
        ]
        + [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final_assistant),
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

    assert not [event for event in events if isinstance(event, ErrorEvent)]
    assert len(provider.calls) == 10
    assert messages[-1] == final_assistant


@pytest.mark.anyio
async def test_agent_loop_stops_after_configured_max_turns() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content="Again.", tool_calls=[tool_call])
    messages = [UserMessage(content="loop forever")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
            ],
        ]
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

    errors = [event for event in events if isinstance(event, ErrorEvent)]

    assert errors == [
        ErrorEvent(message="Agent loop stopped after reaching max_turns=1", recoverable=True)
    ]
    assert len(provider.calls) == 1


def _tool_then_stop_provider(tool_call: ToolCall, final_text: str = "done") -> FakeProvider:
    first = AssistantMessage(content="Working on it.", tool_calls=[tool_call])
    final = AssistantMessage(content=final_text)
    return FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final, finish_reason="stop"),
            ],
        ]
    )


@pytest.mark.anyio
async def test_agent_loop_emits_tool_updates_in_order_before_end() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
        *,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del arguments, signal
        assert on_update is not None
        on_update("step 1", {"n": 1})
        await asyncio.sleep(0)
        on_update("step 2", {"n": 2})
        await asyncio.sleep(0)
        return AgentToolResult(tool_call_id="call-1", name="work", ok=True, content="result")

    tool = AgentTool(
        name="work",
        description="Do work.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="work", arguments={})
    messages: list[AgentMessage] = [UserMessage(content="Do it")]

    events = await _collect(
        run_agent_loop(
            provider=_tool_then_stop_provider(tool_call),
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        )
    )

    tool_events = [
        event
        for event in events
        if event.type in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}
    ]
    assert [event.type for event in tool_events] == [
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_update",
        "tool_execution_end",
    ]
    updates = [event for event in tool_events if isinstance(event, ToolExecutionUpdateEvent)]
    assert [(u.tool_call_id, u.message, u.data) for u in updates] == [
        ("call-1", "step 1", {"n": 1}),
        ("call-1", "step 2", {"n": 2}),
    ]
    end = tool_events[-1]
    assert isinstance(end, ToolExecutionEndEvent)
    assert end.result.content == "result"


@pytest.mark.anyio
async def test_agent_loop_executor_without_on_update_still_runs() -> None:
    # Executor uses the classic (arguments, signal) signature; the inspect gate
    # must never try to pass it an on_update kwarg.
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del arguments, signal
        return AgentToolResult(tool_call_id="call-1", name="plain", ok=True, content="ok")

    tool = AgentTool(
        name="plain",
        description="No progress seam.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="plain", arguments={})
    messages: list[AgentMessage] = [UserMessage(content="Do it")]

    events = await _collect(
        run_agent_loop(
            provider=_tool_then_stop_provider(tool_call),
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        )
    )

    assert not any(isinstance(event, ToolExecutionUpdateEvent) for event in events)
    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert len(ends) == 1
    assert ends[0].result.ok is True
    assert ends[0].result.content == "ok"


@pytest.mark.anyio
async def test_agent_loop_keeps_updates_when_tool_raises() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
        *,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del arguments, signal
        assert on_update is not None
        on_update("partway there")
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    tool = AgentTool(
        name="work",
        description="Fails midway.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="work", arguments={})
    messages: list[AgentMessage] = [UserMessage(content="Do it")]

    events = await _collect(
        run_agent_loop(
            provider=_tool_then_stop_provider(tool_call),
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        )
    )

    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert [u.message for u in updates] == ["partway there"]
    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert len(ends) == 1
    assert ends[0].result.ok is False
    assert "boom" in ends[0].result.content
    # The error result is still recorded in the transcript.
    tool_results = [m for m in messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == "call-1"
    assert tool_results[0].ok is False


@pytest.mark.anyio
async def test_agent_loop_closing_stream_cancels_running_tool() -> None:
    cancelled = asyncio.Event()

    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
        *,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del arguments, signal
        assert on_update is not None
        on_update("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return AgentToolResult(tool_call_id="call-1", name="work", ok=True, content="never")

    tool = AgentTool(
        name="work",
        description="Runs forever.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="work", arguments={})
    messages: list[AgentMessage] = [UserMessage(content="Do it")]

    stream = cast(
        "AsyncGenerator[AgentEvent]",
        run_agent_loop(
            provider=_tool_then_stop_provider(tool_call),
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        ),
    )

    seen: list[AgentEvent] = []
    async for event in stream:
        seen.append(event)
        if isinstance(event, ToolExecutionUpdateEvent):
            break

    # We received the update before closing; closing must not drop it and must
    # tear the in-flight tool task down cleanly.
    await stream.aclose()

    assert any(isinstance(event, ToolExecutionUpdateEvent) for event in seen)
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
