"""Pi-compatible assistant stream events owned by the portable agent layer."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from tau_agent.messages import AssistantMessage, ToolCall, WireModel


class AssistantStartEvent(WireModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class TextStartEvent(WireModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(WireModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class TextEndEvent(WireModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ThinkingStartEvent(WireModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(WireModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(WireModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ToolCallStartEvent(WireModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(WireModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ToolCallEndEvent(WireModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


DoneReason = Literal["stop", "length", "toolUse"]
ErrorReason = Literal["aborted", "error"]


class AssistantDoneEvent(WireModel):
    type: Literal["done"] = "done"
    reason: DoneReason
    message: AssistantMessage


class AssistantErrorEvent(WireModel):
    type: Literal["error"] = "error"
    reason: ErrorReason
    error: AssistantMessage


type AssistantMessageEvent = Annotated[
    AssistantStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | AssistantDoneEvent
    | AssistantErrorEvent,
    Field(discriminator="type"),
]
