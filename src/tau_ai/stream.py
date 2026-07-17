"""Translate Tau's transitional provider parser output into Pi stream events."""

from __future__ import annotations

from collections.abc import AsyncIterator

from tau_agent.messages import (
    AssistantMessage,
    AssistantMessageDiagnostic,
    TextContent,
    ThinkingContent,
    Usage,
)
from tau_ai._provider_events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
)
from tau_ai.events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)


def _snapshot(message: AssistantMessage) -> AssistantMessage:
    return message.model_copy(deep=True)


def _finish_reason(value: str | None, *, has_tools: bool) -> str:
    if has_tools or value in {"tool_calls", "tool_use", "toolUse"}:
        return "toolUse"
    if value in {"length", "max_tokens", "MAX_TOKENS", "incomplete"}:
        return "length"
    return "stop"


async def canonicalize_provider_stream(
    source: AsyncIterator[ProviderEvent],
    *,
    api: str,
    provider: str,
    model: str,
) -> AsyncIterator[AssistantMessageEvent]:
    """Canonicalize one old internal parser stream.

    Provider parsers remain isolated behind this private bridge while they are
    migrated incrementally. The public provider protocol exposes only Pi events.
    """
    partial = AssistantMessage(api=api, provider=provider, model=model)
    text_index: int | None = None
    thinking_index: int | None = None
    started = False
    terminal = False

    async for event in source:
        if isinstance(event, ProviderRetryEvent):
            # Retries are provider-internal at the Pi AI boundary.
            continue
        if isinstance(event, ProviderResponseStartEvent):
            if not started:
                started = True
                yield AssistantStartEvent(partial=_snapshot(partial))
            continue
        if not started:
            started = True
            yield AssistantStartEvent(partial=_snapshot(partial))

        if isinstance(event, ProviderTextDeltaEvent):
            if text_index is None:
                text_index = len(partial.content)
                partial.content.append(TextContent(text=""))
                yield TextStartEvent(content_index=text_index, partial=_snapshot(partial))
            block = partial.content[text_index]
            assert isinstance(block, TextContent)
            block.text += event.delta
            yield TextDeltaEvent(
                content_index=text_index,
                delta=event.delta,
                partial=_snapshot(partial),
            )
        elif isinstance(event, ProviderThinkingDeltaEvent):
            if thinking_index is None:
                thinking_index = len(partial.content)
                partial.content.append(ThinkingContent(thinking=""))
                yield ThinkingStartEvent(
                    content_index=thinking_index,
                    partial=_snapshot(partial),
                )
            block = partial.content[thinking_index]
            assert isinstance(block, ThinkingContent)
            block.thinking += event.delta
            yield ThinkingDeltaEvent(
                content_index=thinking_index,
                delta=event.delta,
                partial=_snapshot(partial),
            )
        elif isinstance(event, ProviderToolCallEvent):
            index = len(partial.content)
            partial.content.append(event.tool_call.model_copy(deep=True))
            yield ToolCallStartEvent(content_index=index, partial=_snapshot(partial))
            yield ToolCallEndEvent(
                content_index=index,
                tool_call=event.tool_call,
                partial=_snapshot(partial),
            )
        elif isinstance(event, ProviderResponseEndEvent):
            if text_index is not None:
                block = partial.content[text_index]
                assert isinstance(block, TextContent)
                yield TextEndEvent(
                    content_index=text_index,
                    content=block.text,
                    partial=_snapshot(partial),
                )
            if thinking_index is not None:
                block = partial.content[thinking_index]
                assert isinstance(block, ThinkingContent)
                yield ThinkingEndEvent(
                    content_index=thinking_index,
                    content=block.thinking,
                    partial=_snapshot(partial),
                )

            # Preserve the exact streamed content order. The parser's final
            # message remains authoritative only for response metadata/usage.
            final = event.message.model_copy(deep=True)
            final.api = api
            final.provider = provider
            final.model = model
            final.content = [block.model_copy(deep=True) for block in partial.content]
            if not final.content and event.message.content:
                final.content = [block.model_copy(deep=True) for block in event.message.content]
            final.stop_reason = _finish_reason(
                event.finish_reason,
                has_tools=bool(final.tool_calls),
            )  # type: ignore[assignment]
            yield AssistantDoneEvent(reason=final.stop_reason, message=final)  # type: ignore[arg-type]
            terminal = True
        elif isinstance(event, ProviderErrorEvent):
            error = partial.model_copy(deep=True)
            error.stop_reason = "error"
            error.error_message = event.message
            error.diagnostics = [
                AssistantMessageDiagnostic(type="provider_error", details=event.data)
            ]
            yield AssistantErrorEvent(reason="error", error=error)
            terminal = True

    if not started:
        yield AssistantStartEvent(partial=_snapshot(partial))
    if not terminal:
        error = partial.model_copy(deep=True)
        error.stop_reason = "error"
        error.error_message = "Provider stream ended without a terminal event"
        error.usage = Usage()
        yield AssistantErrorEvent(reason="error", error=error)
