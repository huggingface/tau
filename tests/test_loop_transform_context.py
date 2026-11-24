"""Adversarial contract tests for the run_agent_loop transform_context hook.

The hook exists so a session can shape the payload of a single provider
request (mid-run compaction per issue #805) without touching durable loop
history. Every test here attacks one edge of that contract: payload-only
scope, fresh-copy isolation, sync/async parity, per-request application,
signal plumbing, reordered payloads, and exception propagation.
"""

from collections.abc import Mapping

import pytest

from pi_event_helpers import assistant_done, assistant_start, tool_call_end
from tau_agent import (
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    SimpleCancellationToken,
    ToolCall,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.messages import message_text
from tau_ai import FakeProvider


async def _collect(stream) -> list:  # noqa: ANN001
    return [event async for event in stream]


def _echo_tool() -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, object],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content="ok")

    return AgentTool(
        name="echo",
        label="Echo",
        description="Echo.",
        parameters={"type": "object"},
        execute_fn=execute,
    )


@pytest.mark.anyio
async def test_transform_context_shapes_request_without_touching_history() -> None:
    marker = UserMessage(content="TRANSFORMED")
    seen_inputs: list[list[AgentMessage]] = []

    def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        seen_inputs.append(messages)
        return [*messages, marker]

    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            transform_context=transform,
        )
    )

    # The provider saw the transformed payload.
    assert [message.text for message in provider.calls[0][2]] == ["Say hello", "TRANSFORMED"]
    # Durable history is untouched by the transform (the assistant reply is
    # appended by the loop itself, never the marker).
    assert [message.text for message in messages] == ["Say hello", "Hello"]
    assert marker not in messages


@pytest.mark.anyio
async def test_transform_context_receives_fresh_copy_mutation_is_isolated() -> None:
    def mutating_transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        del signal
        messages.clear()  # adversarial: destroy the input copy entirely
        messages.append(UserMessage(content="destroyed"))
        return messages

    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            transform_context=mutating_transform,
        )
    )

    # Durable history survives the hostile mutation (loop appends only the
    # assistant reply; the destroyed payload never leaks into history).
    assert [message.text for message in messages] == ["Say hello", "Hello"]
    # The provider received the destroyed payload, as the hook requested.
    assert [message.text for message in provider.calls[0][2]] == ["destroyed"]


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.anyio
async def test_transform_context_sync_and_async_are_equivalent(use_async: bool) -> None:
    suffix = "ASYNC" if use_async else "SYNC"
    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]

    if use_async:

        async def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
            del signal
            return [*messages, UserMessage(content=suffix)]

    else:

        def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
            del signal
            return [*messages, UserMessage(content=suffix)]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            transform_context=transform,
        )
    )

    assert provider.calls[0][2][-1].text == suffix


@pytest.mark.anyio
async def test_transform_context_runs_before_every_provider_request() -> None:
    seen_sizes: list[int] = []

    async def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        del signal
        seen_sizes.append(len(messages))
        return messages

    call = ToolCall(id="call-1", name="echo", arguments={})
    with_call = AssistantMessage(content=[call], model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(with_call)],
            [assistant_start(), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="work")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_echo_tool()],
            transform_context=transform,
        )
    )

    assert len(seen_sizes) == 2
    # First request: prompt only. Second request: prompt + assistant + tool result.
    assert seen_sizes == [1, 3]


@pytest.mark.anyio
async def test_transform_context_receives_the_active_signal() -> None:
    seen: list[object] = []

    async def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        seen.append(signal)
        return messages

    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    token = SimpleCancellationToken()

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=[UserMessage(content="hi")],
            tools=[],
            signal=token,
            transform_context=transform,
        )
    )

    assert seen == [token]


@pytest.mark.anyio
async def test_transform_context_may_reorder_or_drop_payload() -> None:
    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    first = UserMessage(content="first")
    second = UserMessage(content="second")
    durable: list[AgentMessage] = [first, second]

    async def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        del signal
        return list(reversed(messages))

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=durable,
            tools=[],
            transform_context=transform,
        )
    )

    replayed = provider.calls[0][2]
    # The provider received the reordered payload.
    assert replayed[0] is second
    assert replayed[1] is first
    # Durable history keeps the original objects in the original order.
    assert durable[0] is first
    assert durable[1] is second


@pytest.mark.anyio
async def test_transform_context_exception_propagates_to_consumer() -> None:
    async def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        del messages, signal
        raise RuntimeError("compaction exploded")

    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])

    with pytest.raises(RuntimeError, match="compaction exploded"):
        await _collect(
            run_agent_loop(
                provider=provider,
                model="fake",
                system="You are Tau.",
                messages=[UserMessage(content="hi")],
                tools=[],
                transform_context=transform,
            )
        )
    # The provider was never called with a broken payload.
    assert provider.calls == []


@pytest.mark.anyio
async def test_transform_context_result_is_copied_not_aliased() -> None:
    shared: list[AgentMessage] = [UserMessage(content="shared")]
    durable: list[AgentMessage] = [UserMessage(content="hi")]

    def transform(messages: list[AgentMessage], signal) -> list[AgentMessage]:  # noqa: ANN001
        del signal
        return shared

    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=durable,
            tools=[],
            transform_context=transform,
        )
    )

    # The loop must not adopt the hook's list object as its own request list.
    # (Element objects may be re-validated copies — UserMessage carries a
    # millisecond timestamp, so value-equality across construction time is
    # a flake; compare content text and list identity instead.)
    replayed = provider.calls[0][2]
    assert replayed is not shared
    assert [message_text(message) for message in replayed] == [
        message_text(message) for message in shared
    ]
    # Durable history holds the loop's own messages; the hook's list object
    # was never spliced into it.
    assert durable[-1] is assistant
    assert durable[0] is not shared[0]
    assert [message_text(message) for message in shared] == ["shared"]
