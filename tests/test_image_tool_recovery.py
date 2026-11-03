"""Provider rejection of image tools is recoverable without changing durable history."""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from pi_event_helpers import assistant_done, assistant_error
from tau_agent import (
    AgentEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ImageContent,
    MessageEndEvent,
    SimpleCancellationToken,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.loop import _provider_context, run_agent_loop
from tau_agent.session import JsonlSessionStorage
from tau_agent.types import JSONValue
from tau_ai import CancellationToken, FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.events import SessionAgentEndEvent
from tau_coding.rendering import FinalTextRenderer, JsonEventRenderer, TranscriptRenderer
from tau_coding.tui import TuiEventAdapter, TuiState


def _image_history() -> list[AgentMessage]:
    return [
        UserMessage(content="Read image.png"),
        AssistantMessage(content=[ToolCall(id="image", name="read", arguments={})]),
        ToolResultMessage(
            tool_call_id="image",
            tool_name="read",
            content=[
                TextContent(text="Read image.png"),
                ImageContent(data="AA==", mime_type="image/png"),
            ],
            details={"path": "image.png"},
        ),
    ]


async def _run(
    provider: FakeProvider,
    messages: list[AgentMessage],
    *,
    signal: CancellationToken | None = None,
    max_turns: int | None = None,
) -> list[AgentEvent]:
    return [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="",
            messages=messages,
            tools=[],
            signal=signal,
            max_turns=max_turns,
        )
    ]


@pytest.mark.anyio
async def test_image_rejection_reaches_model_as_tool_error_and_preserves_history() -> None:
    messages = _image_history()
    original = messages[2].model_copy(deep=True)
    provider = FakeProvider(
        [
            [assistant_error("image exceeds maximum size")],
            [assistant_done(AssistantMessage(content="Use a thumbnail."))],
        ]
    )

    events = await _run(provider, messages)

    assert len(provider.calls) == 2
    recovered = provider.calls[1][2][2]
    assert isinstance(recovered, ToolResultMessage)
    assert recovered.tool_call_id == "image"
    assert recovered.tool_name == "read"
    assert recovered.is_error
    assert recovered.details == {"path": "image.png"}
    assert "image exceeds maximum size" in recovered.text
    assert all(isinstance(block, TextContent) for block in recovered.content)
    assert messages[2] == original
    assert messages[-1].text == "Use a thumbnail."
    assert [event.type for event in events].count("agent_end") == 1
    assert [event.type for event in events].count("turn_end") == 2
    # JSONL replay must reconstruct the same safe context, without in-place edits.
    restored = [
        type(message).model_validate_json(message.model_dump_json()) for message in messages
    ]
    assert _provider_context(restored) == _provider_context(messages)
    assert _provider_context(restored)[2] == recovered


@pytest.mark.anyio
async def test_image_recovery_stops_on_third_consecutive_error() -> None:
    messages = _image_history()
    provider = FakeProvider([[assistant_error(f"API unavailable {i}")] for i in range(4)])

    events = await _run(provider, messages)

    assert len(provider.calls) == 3
    assert messages[-1].error_message == "API unavailable 2"
    assert "API unavailable 1" in provider.calls[2][2][2].text
    assert events[-1].type == "agent_end"
    assert [event.type for event in events].count("turn_start") == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    "partial,aborted,has_image", [(True, False, True), (False, True, True), (False, False, False)]
)
async def test_nonrecoverable_errors_do_not_retry(
    partial: bool, aborted: bool, has_image: bool
) -> None:
    messages = _image_history()
    if not has_image:
        messages[2] = ToolResultMessage(tool_call_id="image", tool_name="read", content="text")
    error = assistant_error("failed")
    if partial:
        error.error.content = [TextContent(text="Already streamed")]
    if aborted:
        error.error.stop_reason = "aborted"
    provider = FakeProvider([[error]])

    await _run(provider, messages)

    assert len(provider.calls) == 1
    assert _provider_context(messages)[2] == messages[2]


@pytest.mark.anyio
async def test_recovery_respects_max_turns() -> None:
    provider = FakeProvider([[assistant_error("too large")]])
    messages = _image_history()

    await _run(provider, messages, max_turns=1)

    assert len(provider.calls) == 1
    assert messages[-1].error_message == "too large"
    assert not messages[-1].is_tool_result_recovery


@pytest.mark.anyio
async def test_recovery_respects_cancellation() -> None:
    signal = SimpleCancellationToken()

    class CancellingProvider(FakeProvider):
        def stream_response(self, **kwargs) -> AsyncIterator:  # noqa: ANN003
            source = super().stream_response(**kwargs)

            async def stream() -> AsyncIterator:
                async for event in source:
                    signal.cancel()
                    yield event

            return stream()

    provider = CancellingProvider([[assistant_error("too large")]])
    await _run(provider, _image_history(), signal=signal)
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_success_resets_error_streak_and_tools_are_not_rerun() -> None:
    executions = 0

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        nonlocal executions
        executions += 1
        return AgentToolResult(content=[ImageContent(data="AA==", mime_type="image/png")])

    call = AssistantMessage(content=[ToolCall(id="second", name="read", arguments={})])
    provider = FakeProvider(
        [
            [assistant_error("first image rejected")],
            [assistant_error("still unavailable")],
            [assistant_done(call)],
            [assistant_error("second image rejected")],
            [assistant_error("temporarily unavailable")],
            [assistant_done(AssistantMessage(content="Done"))],
        ]
    )
    messages = _image_history()
    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="",
            messages=messages,
            tools=[
                AgentTool(
                    name="read", label="Read", description="Read", parameters={}, execute_fn=execute
                )
            ],
        )
    ]

    assert executions == 1
    assert len(provider.calls) == 6
    assert messages[-1].text == "Done"
    assert events[-1].type == "agent_end"


def test_multiple_image_results_are_recovered_without_touching_other_tools() -> None:
    messages = _image_history()
    assistant = messages[1]
    assert isinstance(assistant, AssistantMessage)
    assistant.content.extend(
        [
            ToolCall(id="text", name="read", arguments={}),
            ToolCall(id="other", name="read", arguments={}),
        ]
    )
    text = ToolResultMessage(tool_call_id="text", tool_name="read", content="keep this")
    other = ToolResultMessage(
        tool_call_id="other",
        tool_name="read",
        content=[ImageContent(data="AA==", mime_type="image/png")],
    )
    messages.extend([text, other, assistant_error("image batch too large").error])

    context = _provider_context(messages)

    assert context[3] == text
    for index in (2, 4):
        assert context[index].is_error
        assert "image batch too large" in context[index].text
        assert all(isinstance(block, TextContent) for block in context[index].content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "renderer_type", [FinalTextRenderer, JsonEventRenderer, TranscriptRenderer]
)
@pytest.mark.parametrize("succeeds", [True, False])
async def test_renderers_only_fail_for_terminal_errors(
    renderer_type, succeeds: bool, capsys
) -> None:  # noqa: ANN001
    provider = FakeProvider(
        [
            [assistant_error("too large")],
            [assistant_done(AssistantMessage(content="Recovered"))]
            if succeeds
            else [assistant_error("offline")],
            [assistant_error("still offline")],
        ]
    )
    events = await _run(provider, _image_history())
    renderer = renderer_type()
    state = TuiState()
    adapter = TuiEventAdapter(state)
    for event in events:
        renderer.render(event)
        adapter.apply(event)
        if (
            isinstance(event, MessageEndEvent)
            and isinstance(event.message, AssistantMessage)
            and event.message.is_tool_result_recovery
        ):
            assert state.running
            assert state.error is None
    assert renderer.finish() is succeeds
    assert (state.error is None) is succeeds
    assert not state.running
    assert len([item for item in state.items if item.role == "error"]) == (0 if succeeds else 1)
    restored = TuiState()
    restored.load_messages(events[-1].messages)
    assert (restored.error is None) is succeeds
    capsys.readouterr()


@pytest.mark.anyio
@pytest.mark.parametrize("continue_run", [True, False])
async def test_coding_session_persists_recovery_without_triggering_overflow_retry(
    tmp_path: Path, continue_run: bool
) -> None:
    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        return AgentToolResult(content=[ImageContent(data="AA==", mime_type="image/png")])

    call = AssistantMessage(content=[ToolCall(id="image", name="read", arguments={})])
    provider = FakeProvider(
        [
            [assistant_done(call)],
            [assistant_error("request too large: context window exceeded")],
            [assistant_done(AssistantMessage(content="Recovered"))],
        ]
    )
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    config = CodingSessionConfig(
        provider=provider,
        model="fake",
        system="",
        storage=storage,
        cwd=tmp_path,
        tools=[
            AgentTool(
                name="read", label="Read", description="Read", parameters={}, execute_fn=execute
            )
        ],
    )
    session = await CodingSession.load(config)
    try:
        stream = session.continue_() if continue_run else session.prompt("Read image.png")
        events = [event async for event in stream]
        assert len(provider.calls) == 3
        assert not any(event.type in {"compaction_start", "auto_retry_start"} for event in events)
        end = next(event for event in events if isinstance(event, SessionAgentEndEvent))
        assert not end.will_retry
        assert session.messages[-1].text == "Recovered"
    finally:
        await session.aclose()
    restored = await CodingSession.load(config)
    try:
        failures = [
            message
            for message in restored.messages
            if isinstance(message, AssistantMessage) and message.stop_reason == "error"
        ]
        assert len(failures) == 1
        assert failures[0].is_tool_result_recovery
        result = _provider_context(list(restored.messages))[1 if continue_run else 2]
        assert isinstance(result, ToolResultMessage)
        assert result.is_error
        assert "context window exceeded" in result.text
        assert all(isinstance(block, TextContent) for block in result.content)
    finally:
        await restored.aclose()


def test_old_images_are_not_removed_for_unrelated_later_errors() -> None:
    messages = _image_history()
    messages.extend(
        [
            AssistantMessage(content="I saw it."),
            UserMessage(content="Next"),
            assistant_error("offline").error,
        ]
    )
    assert _provider_context(messages)[2] == messages[2]
