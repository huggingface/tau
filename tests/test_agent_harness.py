from collections.abc import Mapping

import pytest

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    UserMessage,
)
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.types import JSONValue
from tau_ai import (
    FakeProvider,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
)


@pytest.mark.anyio
async def test_prompt_appends_user_message_and_assistant_response() -> None:
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [[ProviderResponseStartEvent(model="fake"), ProviderResponseEndEvent(message=assistant)]]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
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
    assert events[2].message_role == "user"  # type: ignore[attr-defined]
    assert events[3].message == UserMessage(content="Hi")  # type: ignore[attr-defined]
    assert harness.messages == (UserMessage(content="Hi"), assistant)


@pytest.mark.anyio
async def test_continue_runs_without_adding_user_message() -> None:
    existing = UserMessage(content="Previous prompt")
    assistant = AssistantMessage(content="Continuing")
    provider = FakeProvider(
        [[ProviderResponseStartEvent(model="fake"), ProviderResponseEndEvent(message=assistant)]]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau."),
        messages=[existing],
    )

    _events = [event async for event in harness.continue_()]

    assert harness.messages == (existing, assistant)
    assert provider.calls[0][2] == [existing]


def test_messages_property_returns_immutable_snapshot() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau."),
        messages=[UserMessage(content="Hello")],
    )

    snapshot = harness.messages
    harness.append_message(AssistantMessage(content="Hi"))

    assert snapshot == (UserMessage(content="Hello"),)
    assert harness.messages == (UserMessage(content="Hello"), AssistantMessage(content="Hi"))


def test_harness_can_replace_messages() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau."),
        messages=[UserMessage(content="Old")],
    )

    harness.replace_messages([UserMessage(content="Summary")])

    assert harness.messages == (UserMessage(content="Summary"),)


def test_harness_can_clear_queued_messages() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau.")
    )

    harness.steer("Adjust")
    harness.follow_up("Later")
    cleared = harness.clear_queues()

    assert cleared.steering == (UserMessage(content="Adjust"),)
    assert cleared.follow_up == (UserMessage(content="Later"),)
    assert harness.pending_message_count == 0
    assert harness.queue_update_event().steering == ()
    assert harness.queue_update_event().follow_up == ()


def test_harness_can_pop_latest_follow_up_message() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Tau.")
    )

    harness.follow_up("First")
    harness.follow_up("Second")
    popped = harness.pop_latest_follow_up()

    assert popped == UserMessage(content="Second")
    assert harness.queue_update_event().follow_up == ("First",)
    assert harness.pop_latest_follow_up() == UserMessage(content="First")
    assert harness.pop_latest_follow_up() is None


@pytest.mark.anyio
async def test_subscribed_listeners_receive_events_and_can_unsubscribe() -> None:
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hello"),
                ProviderResponseEndEvent(message=assistant),
            ],
            [ProviderResponseStartEvent(model="fake"), ProviderResponseEndEvent(message=assistant)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )
    seen: list[str] = []

    async def listener(event: object) -> None:
        seen.append(event.type)  # type: ignore[attr-defined]

    unsubscribe = harness.subscribe(listener)

    _events = [event async for event in harness.prompt("Hi")]
    unsubscribe()
    _more_events = [event async for event in harness.continue_()]

    assert seen == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]


@pytest.mark.anyio
async def test_cancel_requests_cancellation_for_current_run() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="first"),
                ProviderTextDeltaEvent(delta="second"),
                ProviderResponseEndEvent(message=AssistantMessage(content="firstsecond")),
            ]
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )

    events = []
    async for event in harness.prompt("Hi"):
        events.append(event)
        if event.type == "message_delta":
            harness.cancel()

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_delta",
        "error",
        "turn_end",
        "agent_end",
    ]
    assert harness.messages == (UserMessage(content="Hi"),)


@pytest.mark.anyio
async def test_harness_rejects_overlapping_prompt_runs() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Hello")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Queued answer")),
            ],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )

    events = []
    queued = False
    async for event in harness.prompt("Hi"):
        events.append(event)
        if (
            isinstance(event, MessageStartEvent)
            and event.message_role == "assistant"
            and not queued
        ):
            with pytest.raises(RuntimeError, match="already running"):
                harness.prompt("Overlapping")
            queue_event = harness.steer("Queued instead")
            assert queue_event.steering == ("Queued instead",)
            queued = True

    assert harness.is_running is False
    assert harness.pending_message_count == 0
    assert harness.messages == (
        UserMessage(content="Hi"),
        AssistantMessage(content="Hello"),
        UserMessage(content="Queued instead"),
        AssistantMessage(content="Queued answer"),
    )


@pytest.mark.anyio
async def test_harness_drains_follow_up_messages_one_at_a_time_by_default() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="First")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Second")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Third")),
            ],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.")
    )

    events = []
    async for event in harness.prompt("Hi"):
        events.append(event)
        if isinstance(event, MessageEndEvent) and event.message.content == "First":
            harness.follow_up("Second prompt")
            harness.follow_up("Third prompt")

    assert [event.type for event in events].count("queue_update") == 2
    assert harness.messages == (
        UserMessage(content="Hi"),
        AssistantMessage(content="First"),
        UserMessage(content="Second prompt"),
        AssistantMessage(content="Second"),
        UserMessage(content="Third prompt"),
        AssistantMessage(content="Third"),
    )
    assert provider.calls[1][2] == list(harness.messages[:3])
    assert provider.calls[2][2] == list(harness.messages[:5])
    assert harness.pending_message_count == 0


@pytest.mark.anyio
async def test_harness_can_drain_all_queued_messages_together() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="First")),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage(content="Second")),
            ],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            queue_mode="all",
        )
    )

    async for event in harness.prompt("Hi"):
        if isinstance(event, MessageEndEvent) and event.message.content == "First":
            harness.follow_up("Second prompt")
            harness.follow_up("Third prompt")

    assert harness.messages == (
        UserMessage(content="Hi"),
        AssistantMessage(content="First"),
        UserMessage(content="Second prompt"),
        UserMessage(content="Third prompt"),
        AssistantMessage(content="Second"),
    )
    assert provider.calls[1][2] == list(harness.messages[:4])


@pytest.mark.anyio
async def test_harness_passes_tools_to_loop() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call-1",
            name="echo",
            ok=True,
            content=str(arguments["text"]),
        )

    tool = AgentTool(
        name="echo",
        description="Echo text.",
        input_schema={"type": "object"},
        executor=executor,
    )
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=AssistantMessage()),
            ]
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Tau.", tools=[tool])
    )

    _events = [event async for event in harness.prompt("Hi")]

    assert provider.calls[0][3] == [tool]
