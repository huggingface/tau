"""Pure provider/tool agent loop."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence

from tau_agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    RetryEvent,
    ThinkingDeltaEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage
from tau_agent.tools import AgentTool, AgentToolResult, ToolCall, ToolUpdateCallback
from tau_agent.types import JSONValue
from tau_ai.events import (
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
)
from tau_ai.provider import CancellationToken, ModelProvider


async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    max_turns: int | None = None,
    signal: CancellationToken | None = None,
    get_steering_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    get_follow_up_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    get_queue_update: Callable[[], QueueUpdateEvent] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the pure agent loop and stream provider-neutral agent events.

    The passed `messages` list is the transcript owned by the caller. The loop
    appends assistant messages and tool result messages to it as the run
    progresses. This keeps the loop stateless while allowing a future harness to
    own transcript state.
    """
    yield AgentStartEvent()

    if max_turns is not None and max_turns < 1:
        yield ErrorEvent(message="max_turns must be at least 1", recoverable=False)
        yield AgentEndEvent()
        return

    tool_by_name = {tool.name: tool for tool in tools}
    turn = 1

    while max_turns is None or turn <= max_turns:
        if signal is not None and signal.is_cancelled():
            yield ErrorEvent(message="Agent run cancelled", recoverable=True)
            break

        yield TurnStartEvent(turn=turn)
        assistant_message: AssistantMessage | None = None
        saw_provider_error = False

        async for provider_event in provider.stream_response(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            signal=signal,
        ):
            if isinstance(provider_event, ProviderResponseStartEvent):
                yield MessageStartEvent()
            elif isinstance(provider_event, ProviderTextDeltaEvent):
                yield MessageDeltaEvent(delta=provider_event.delta)
            elif isinstance(provider_event, ProviderThinkingDeltaEvent):
                yield ThinkingDeltaEvent(delta=provider_event.delta)
            elif isinstance(provider_event, ProviderRetryEvent):
                yield RetryEvent(
                    attempt=provider_event.attempt,
                    max_attempts=provider_event.max_attempts,
                    delay_seconds=provider_event.delay_seconds,
                    message=provider_event.message,
                    data=provider_event.data,
                )
            elif isinstance(provider_event, ProviderResponseEndEvent):
                assistant_message = provider_event.message
                messages.append(assistant_message)
                yield MessageEndEvent(message=assistant_message)
            elif isinstance(provider_event, ProviderErrorEvent):
                saw_provider_error = True
                yield ErrorEvent(
                    message=provider_event.message,
                    recoverable=False,
                    data=provider_event.data,
                )

        if assistant_message is None:
            if signal is not None and signal.is_cancelled():
                yield ErrorEvent(message="Agent run cancelled", recoverable=True)
                yield TurnEndEvent(turn=turn)
                break
            yield TurnEndEvent(turn=turn)
            if saw_provider_error:
                break
            yield ErrorEvent(message="Provider stream ended without an assistant message")
            break

        if not assistant_message.tool_calls:
            yield TurnEndEvent(turn=turn)
            queue_events = _drain_queued_messages(
                messages,
                get_steering_messages,
                get_queue_update,
            )
            if queue_events:
                for queue_event in queue_events:
                    yield queue_event
                turn += 1
                continue
            queue_events = _drain_queued_messages(
                messages,
                get_follow_up_messages,
                get_queue_update,
            )
            if queue_events:
                for queue_event in queue_events:
                    yield queue_event
                turn += 1
                continue
            break

        async for tool_event in _execute_tool_calls(
            assistant_message.tool_calls,
            tool_by_name,
            messages,
            signal,
        ):
            yield tool_event

        yield TurnEndEvent(turn=turn)
        for queue_event in _drain_queued_messages(
            messages,
            get_steering_messages,
            get_queue_update,
        ):
            yield queue_event
        turn += 1
    else:
        yield ErrorEvent(
            message=f"Agent loop stopped after reaching max_turns={max_turns}",
            recoverable=True,
        )

    yield AgentEndEvent()


def _drain_queued_messages(
    messages: list[AgentMessage],
    get_messages: Callable[[], Sequence[AgentMessage]] | None,
    get_queue_update: Callable[[], QueueUpdateEvent] | None,
) -> tuple[AgentEvent, ...]:
    if get_messages is None:
        return ()
    queued_messages = tuple(get_messages())
    if not queued_messages:
        return ()

    messages.extend(queued_messages)
    events: list[AgentEvent] = []
    for message in queued_messages:
        events.append(MessageStartEvent(message_role=message.role))
        events.append(MessageEndEvent(message=message))
    if get_queue_update is not None:
        events.append(get_queue_update())
    return tuple(events)


async def _execute_tool_calls(
    tool_calls: list[ToolCall],
    tool_by_name: Mapping[str, AgentTool],
    messages: list[AgentMessage],
    signal: CancellationToken | None,
) -> AsyncIterator[AgentEvent]:
    for index, tool_call in enumerate(tool_calls):
        if signal is not None and signal.is_cancelled():
            for cancelled_tool_call in tool_calls[index:]:
                result = _cancelled_tool_result(cancelled_tool_call)
                messages.append(_tool_result_message(result))
                yield ToolExecutionEndEvent(result=result)
            yield ErrorEvent(message="Agent run cancelled", recoverable=True)
            return

        yield ToolExecutionStartEvent(tool_call=tool_call)

        tool = tool_by_name.get(tool_call.name)
        if tool is None:
            result = _unknown_tool_result(tool_call)
        else:
            produced: AgentToolResult | None = None
            async for item in _execute_tool(tool, tool_call, signal):
                if isinstance(item, ToolExecutionUpdateEvent):
                    yield item
                else:
                    produced = item
            if produced is None:  # pragma: no cover - _execute_tool always ends with a result
                produced = _cancelled_tool_result(tool_call)
            result = produced

        messages.append(_tool_result_message(result))
        yield ToolExecutionEndEvent(result=result)


async def _execute_tool(
    tool: AgentTool,
    tool_call: ToolCall,
    signal: CancellationToken | None,
) -> AsyncIterator[ToolExecutionUpdateEvent | AgentToolResult]:
    """Run a tool, yielding live progress updates then its final result.

    Progress arrives through a synchronous ``on_update`` callback the tool may
    call while it runs. Those calls are bridged onto this async stream via an
    unbounded queue and a task/queue race, so updates are yielded in order
    *while* the tool is still executing. The stream always ends with exactly one
    `AgentToolResult` (never dropped, even on tool error or cancellation).
    """
    queue: asyncio.Queue[ToolExecutionUpdateEvent] = asyncio.Queue()

    def on_update(message: str, data: dict[str, JSONValue] | None = None) -> None:
        queue.put_nowait(
            ToolExecutionUpdateEvent(
                tool_call_id=tool_call.id,
                message=message,
                data=data,
            )
        )

    task = asyncio.ensure_future(_run_tool(tool, tool_call, signal, on_update))
    try:
        while not task.done():
            getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                # An update was queued; the getter safely holds it.
                yield getter.result()
            else:
                # The tool finished first; the getter never dequeued anything,
                # so cancelling it cannot drop an update.
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
        # Drain trailing updates enqueued after the tool finished but before the
        # final poll, then emit the result last.
        while not queue.empty():
            yield queue.get_nowait()
        yield task.result()
    finally:
        # Never orphan the tool task: this also runs when the consuming
        # generator is closed mid-flight (GeneratorExit) or cancelled.
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _run_tool(
    tool: AgentTool,
    tool_call: ToolCall,
    signal: CancellationToken | None,
    on_update: ToolUpdateCallback,
) -> AgentToolResult:
    try:
        result = await tool.execute(tool_call.arguments, signal=signal, on_update=on_update)
    except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
        return AgentToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            ok=False,
            content=str(exc),
            error=str(exc),
        )

    if result.tool_call_id != tool_call.id:
        return result.model_copy(update={"tool_call_id": tool_call.id})
    return result


def _unknown_tool_result(tool_call: ToolCall) -> AgentToolResult:
    message = f"Unknown tool: {tool_call.name}"
    return AgentToolResult(
        tool_call_id=tool_call.id,
        name=tool_call.name,
        ok=False,
        content=message,
        error=message,
    )


def _cancelled_tool_result(tool_call: ToolCall) -> AgentToolResult:
    message = "Tool call cancelled"
    return AgentToolResult(
        tool_call_id=tool_call.id,
        name=tool_call.name,
        ok=False,
        content=message,
        error=message,
    )


def _tool_result_message(result: AgentToolResult) -> ToolResultMessage:
    data: dict[str, JSONValue] | None = result.data
    content = result.content
    if not result.ok and result.error and result.error not in content:
        content = f"{content}\n\nError: {result.error}"
    if data is not None and not content:
        content = str(data)

    return ToolResultMessage(
        tool_call_id=result.tool_call_id,
        name=result.name,
        content=content,
        ok=result.ok,
        data=result.data,
        details=result.details,
        error=result.error,
    )
