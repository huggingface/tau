"""Canonical Pi event constructors used by the test suite."""

from tau_agent.messages import AssistantMessage, ThinkingContent, ToolCall
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEndEvent,
)


def assistant_start(model: str = "fake") -> AssistantStartEvent:
    return AssistantStartEvent(partial=AssistantMessage(model=model))


def text_delta(delta: str) -> TextDeltaEvent:
    return TextDeltaEvent(content_index=0, delta=delta, partial=AssistantMessage(content=delta))


def thinking_delta(delta: str) -> ThinkingDeltaEvent:
    return ThinkingDeltaEvent(
        content_index=0,
        delta=delta,
        partial=AssistantMessage(content=[ThinkingContent(thinking=delta)]),
    )


def tool_call_end(tool_call: ToolCall) -> ToolCallEndEvent:
    return ToolCallEndEvent(
        content_index=0,
        tool_call=tool_call,
        partial=AssistantMessage(content=[tool_call]),
    )


def assistant_done(
    message: AssistantMessage | dict[str, object],
    finish_reason: str | None = None,
) -> AssistantDoneEvent:
    final = (
        message
        if isinstance(message, AssistantMessage)
        else AssistantMessage.model_validate(message)
    )
    if final.tool_calls or finish_reason in {"tool_calls", "tool_use", "toolUse"}:
        reason = "toolUse"
    elif finish_reason in {"length", "max_tokens", "MAX_TOKENS", "incomplete"}:
        reason = "length"
    else:
        reason = "stop"
    final.stop_reason = reason
    return AssistantDoneEvent(reason=reason, message=final)


def assistant_error(message: str, data: object = None) -> AssistantErrorEvent:
    del data
    error = AssistantMessage(stop_reason="error", error_message=message)
    return AssistantErrorEvent(reason="error", error=error)
