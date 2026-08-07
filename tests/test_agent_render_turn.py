from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import pytest

from pi_event_helpers import (
    assistant_done,
    assistant_start,
    text_delta,
    tool_call_end,
)
from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentHarness,
    AgentHarnessConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageStartEvent,
    ModelChangeEvent,
    SimpleCancellationToken,
    TextContent,
    ToolCall,
    ToolResultMessage,
    TurnEndEvent,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.types import JSONValue
from tau_ai import FakeProvider


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


def _tool(name: str) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: SimpleCancellationToken | None = None,
        on_update=None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content=[TextContent(text=f"{name} ran")])

    return AgentTool(
        name=name,
        label=name.title(),
        description=f"Run {name}.",
        parameters={"type": "object"},
        execute_fn=execute,
    )


@pytest.mark.anyio
async def test_render_turn_swaps_model_system_and_tools_between_turns() -> None:
    beta = _tool("beta")
    renderer_calls: list[tuple[str, str, list[AgentTool]] | None] = [
        ("fake-2", "system-2", [beta]),
        ("fake-3", "system-3", []),
        None,
    ]

    def render_turn() -> tuple[str, str, list[AgentTool]] | None:
        return renderer_calls.pop(0)

    first = AssistantMessage(
        content=[
            TextContent(text="Calling beta."),
            ToolCall(id="c1", name="beta", arguments={}),
        ],
        model="fake",
    )
    again = AssistantMessage(
        content=[
            TextContent(text="Calling beta again."),
            ToolCall(id="c2", name="beta", arguments={}),
        ],
        model="fake",
    )
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                tool_call_end(ToolCall(id="c1", name="beta", arguments={})),
                assistant_done(first, "toolUse"),
            ],
            [
                assistant_start(),
                tool_call_end(ToolCall(id="c2", name="beta", arguments={})),
                assistant_done(again, "toolUse"),
            ],
            [assistant_start(), text_delta("Done."), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="Go")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=messages,
            tools=[_tool("alpha")],
            render_turn=render_turn,
        )
    )

    assert [call[0] for call in provider.calls] == ["fake-2", "fake-3", "fake-3"]
    assert [call[1] for call in provider.calls] == ["system-2", "system-3", "system-3"]
    assert [call[3] for call in provider.calls] == [[beta], [], []]

    assert [e.model for e in events if isinstance(e, ModelChangeEvent)] == [
        "fake-2",
        "fake-3",
    ]

    results = [
        message
        for message in messages
        if isinstance(message, ToolResultMessage) and message.tool_name == "beta"
    ]
    assert len(results) == 2
    assert results[0].is_error is False
    assert results[0].text == "beta ran"
    assert results[1].is_error is True
    assert "not found" in results[1].text


@pytest.mark.anyio
async def test_render_turn_none_keeps_current_configuration() -> None:
    def render_turn() -> None:
        return None

    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider([[assistant_start(), text_delta("Done."), assistant_done(final)]])
    messages: list[AgentMessage] = [UserMessage(content="Go")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=messages,
            tools=[_tool("alpha")],
            render_turn=render_turn,
        )
    )

    assert provider.calls[0][0] == "fake"
    assert provider.calls[0][1] == "system-1"
    assert [tool.name for tool in provider.calls[0][3]] == ["alpha"]
    assert not any(isinstance(event, ModelChangeEvent) for event in events)


@pytest.mark.anyio
async def test_render_turn_supports_async_renderers() -> None:
    async def render_turn() -> tuple[str, str, list[AgentTool]]:
        return ("fake-2", "system-2", [])

    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider([[assistant_start(), text_delta("Done."), assistant_done(final)]])
    messages: list[AgentMessage] = [UserMessage(content="Go")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=messages,
            tools=[],
            render_turn=render_turn,
        )
    )

    assert provider.calls[0][0] == "fake-2"
    assert provider.calls[0][1] == "system-2"


@pytest.mark.anyio
async def test_harness_forwards_render_turn_to_loop() -> None:
    beta = _tool("beta")
    renderer_calls = 0

    def render_turn() -> tuple[str, str, list[AgentTool]] | None:
        nonlocal renderer_calls
        renderer_calls += 1
        return ("fake-2", "system-2", [beta]) if renderer_calls == 1 else None

    first = AssistantMessage(
        content=[
            TextContent(text="Calling beta."),
            ToolCall(id="c1", name="beta", arguments={}),
        ],
        model="fake",
    )
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                tool_call_end(ToolCall(id="c1", name="beta", arguments={})),
                assistant_done(first, "toolUse"),
            ],
            [assistant_start(), text_delta("Done."), assistant_done(final)],
        ]
    )
    harness = AgentHarness(
        config=AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="system-1",
            tools=[],
            render_turn=render_turn,
        )
    )

    events = await _collect(harness.prompt("Go"))

    assert [call[0] for call in provider.calls] == ["fake-2", "fake-2"]
    assert [call[3] for call in provider.calls] == [[beta], [beta]]
    assert renderer_calls == 2
    assert [e.model for e in events if isinstance(e, ModelChangeEvent)] == ["fake-2"]


def _raising_renderer() -> tuple[str, str, list[AgentTool]] | None:
    raise RuntimeError("renderer exploded")


def _malformed_renderer() -> tuple[str, str, list[AgentTool]] | None:
    return ("only-model", "missing-tools")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "renderer",
    [_raising_renderer, _malformed_renderer],
    ids=["raising", "malformed-tuple"],
)
async def test_render_turn_failure_terminates_stream_gracefully(
    renderer,
) -> None:
    """A broken renderer must not abort the stream without a terminal event."""
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider([[assistant_start(), text_delta("Done."), assistant_done(final)]])
    messages: list[AgentMessage] = [UserMessage(content="Go")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=messages,
            tools=[_tool("alpha")],
            render_turn=renderer,
        )
    )

    assert not provider.calls
    end = events[-1]
    assert isinstance(end, AgentEndEvent)
    error = end.messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert "render_turn failed" in error.error_message
    assert error.model == "fake"
    turn_ends = [e for e in events if isinstance(e, TurnEndEvent)]
    assert len(turn_ends) == 1
    assert turn_ends[0].message == error


@pytest.mark.anyio
async def test_render_turn_skipped_on_max_turns_boundary_turn() -> None:
    """The renderer must not run on a turn that makes no request."""
    first = AssistantMessage(
        content=[ToolCall(id="c1", name="alpha", arguments={})],
        model="fake",
    )
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                tool_call_end(ToolCall(id="c1", name="alpha", arguments={})),
                assistant_done(first, "toolUse"),
            ],
            [assistant_start(), text_delta("Done."), assistant_done(final)],
        ]
    )
    renderer_calls = 0

    def render_turn() -> tuple[str, str, list[AgentTool]] | None:
        nonlocal renderer_calls
        renderer_calls += 1
        return ("fake-2", "system-2", []) if renderer_calls > 1 else None

    messages: list[AgentMessage] = [UserMessage(content="Go")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=messages,
            tools=[_tool("alpha")],
            max_turns=1,
            render_turn=render_turn,
        )
    )

    assert renderer_calls == 1
    assert [call[0] for call in provider.calls] == ["fake"]
    assert not any(isinstance(e, ModelChangeEvent) for e in events)
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert len(ends) == 1
    error = ends[0].messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.error_message == "Agent stopped after max_turns=1"
    assert error.model == "fake"


@pytest.mark.anyio
async def test_model_change_event_precedes_turn_messages() -> None:
    """ModelChangeEvent must be emitted before the turn it configures."""
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider([[assistant_start(), text_delta("Done."), assistant_done(final)]])

    def render_turn() -> tuple[str, str, list[AgentTool]]:
        return ("fake-2", "system-2", [])

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="system-1",
            messages=[UserMessage(content="Go")],
            tools=[],
            render_turn=render_turn,
        )
    )

    model_change_idx = next(i for i, e in enumerate(events) if isinstance(e, ModelChangeEvent))
    first_assistant_start_idx = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, MessageStartEvent) and isinstance(e.message, AssistantMessage)
    )
    assert model_change_idx < first_assistant_start_idx
    assert events[model_change_idx].model == "fake-2"
    assert provider.calls[0][0] == "fake-2"
