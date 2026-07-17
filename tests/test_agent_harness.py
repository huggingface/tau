from collections.abc import Mapping

import pytest

from pi_event_helpers import assistant_done, assistant_start, text_delta, tool_call_end
from tau_agent import (
    AgentHarness,
    AgentHarnessConfig,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.types import JSONValue
from tau_ai import FakeProvider


def _texts(harness: AgentHarness) -> list[tuple[str, str]]:
    return [(message.role, getattr(message, "text", "")) for message in harness.messages]


@pytest.mark.anyio
async def test_prompt_appends_user_and_assistant_with_pi_lifecycle() -> None:
    assistant = AssistantMessage(content="Hello")
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider([[assistant_start(), assistant_done(assistant)]]),
            model="fake",
            system="You are Tau.",
        )
    )

    events = [event async for event in harness.prompt("Hi")]

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    starts = [event for event in events if isinstance(event, MessageStartEvent)]
    assert [event.message.role for event in starts] == ["user", "assistant"]
    assert _texts(harness) == [("user", "Hi"), ("assistant", "Hello")]


@pytest.mark.anyio
async def test_subscribers_receive_nested_message_updates_and_unsubscribe() -> None:
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [
            [assistant_start(), text_delta("Hello"), assistant_done(assistant)],
            [assistant_start(), assistant_done(assistant)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )
    seen: list[str] = []
    unsubscribe = harness.subscribe(lambda event: seen.append(event.type))

    _ = [event async for event in harness.prompt("Hi")]
    unsubscribe()
    _ = [event async for event in harness.continue_()]

    assert "message_update" in seen
    assert seen[-1] == "agent_end"
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_harness_rejects_overlap_and_drains_followups() -> None:
    first = AssistantMessage(content="First")
    second = AssistantMessage(content="Second")
    provider = FakeProvider(
        [
            [assistant_start(), assistant_done(first)],
            [assistant_start(), assistant_done(second)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )

    queued = False
    async for event in harness.prompt("Hi"):
        if (
            isinstance(event, MessageStartEvent)
            and event.message.role == "assistant"
            and not queued
        ):
            with pytest.raises(RuntimeError, match="already running"):
                harness.prompt("overlap")
            harness.follow_up("Later")
            queued = True

    assert _texts(harness) == [
        ("user", "Hi"),
        ("assistant", "First"),
        ("user", "Later"),
        ("assistant", "Second"),
    ]


@pytest.mark.anyio
async def test_harness_queue_mode_all_drains_messages_together() -> None:
    first = AssistantMessage(content="First")
    second = AssistantMessage(content="Second")
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    [assistant_start(), assistant_done(first)],
                    [assistant_start(), assistant_done(second)],
                ]
            ),
            model="fake",
            system="You are Tau.",
            queue_mode="all",
        )
    )

    async for event in harness.prompt("Hi"):
        if (
            isinstance(event, MessageEndEvent)
            and isinstance(event.message, AssistantMessage)
            and event.message.text == "First"
        ):
            harness.follow_up("Second prompt")
            harness.follow_up("Third prompt")

    assert [text for role, text in _texts(harness) if role == "user"] == [
        "Hi",
        "Second prompt",
        "Third prompt",
    ]


@pytest.mark.anyio
async def test_harness_passes_canonical_tools_to_loop() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=str(arguments["text"]))

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo text.",
        parameters={"type": "object"},
        execute_fn=execute,
    )
    call = ToolCall(id="call-1", name="echo", arguments={"text": "hi"})
    first = AssistantMessage(content=[call])
    final = AssistantMessage(content="Done")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(final)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.", tools=[tool])
    )

    _ = [event async for event in harness.prompt("echo")]

    result = next(message for message in harness.messages if isinstance(message, ToolResultMessage))
    assert result.tool_name == "echo"
    assert result.text == "hi"
    assert provider.calls[0][3] == [tool]


def test_queue_mutators_return_canonical_snapshots() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau.")
    )

    harness.steer("First")
    harness.steer("Second")
    harness.follow_up("Later")
    assert harness.pop_latest_steering() == UserMessage(content="Second")
    assert harness.pop_latest_follow_up() == UserMessage(content="Later")
    assert [message.text for message in harness.queued_messages.steering] == ["First"]

    cleared = harness.clear_queues()
    assert [message.text for message in cleared.steering] == ["First"]
    assert harness.pending_message_count == 0


def test_harness_repairs_interrupted_tool_calls() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau."),
        messages=[AssistantMessage(content=[TextContent(text="Reading"), call])],
    )

    assert harness.append_interrupted_tool_results() == 1
    repair = harness.messages[-1]
    assert isinstance(repair, ToolResultMessage)
    assert repair.is_error is True
    assert repair.text == "Tool call interrupted by user"
