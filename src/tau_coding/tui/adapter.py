"""Translate Pi-compatible session events into Textual display state."""

from tau_agent.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import AssistantMessage, CustomMessage, ToolCall, UserMessage
from tau_ai.events import TextDeltaEvent, ThinkingDeltaEvent
from tau_coding.events import AutoRetryStartEvent, CodingSessionEvent, QueueUpdateEvent
from tau_coding.tui.state import TuiState


class TuiEventAdapter:
    def __init__(self, state: TuiState) -> None:
        self.state = state
        self._assistant_start_item_index: int | None = None

    def apply(self, event: CodingSessionEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self.state.running = True
            self.state.error = None
            return
        if isinstance(event, AgentEndEvent):
            self._flush()
            self.state.running = False
            return
        if event.type == "agent_settled":
            self._flush()
            self.state.running = False
            return
        if isinstance(event, QueueUpdateEvent):
            self.state.update_queue(steering=event.steering, follow_up=event.follow_up)
            return
        if isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                self.state.assistant_buffer = event.message.text
                self._assistant_start_item_index = len(self.state.items)
            return
        if isinstance(event, MessageUpdateEvent):
            nested = event.assistant_message_event
            if isinstance(nested, TextDeltaEvent):
                self.state.assistant_buffer += nested.delta
            elif isinstance(nested, ThinkingDeltaEvent):
                self.state.add_thinking_delta(nested.delta)
            return
        if isinstance(event, MessageEndEvent):
            message = event.message
            if isinstance(message, UserMessage):
                self.state.add_user_message(message.text)
            elif isinstance(message, CustomMessage):
                self.state.add_user_message(
                    message.text,
                    custom_type=message.custom_type,
                    details=message.details if isinstance(message.details, dict) else None,
                )
            elif isinstance(message, AssistantMessage):
                # Replace provisional delta rows with the final canonical
                # message so persisted block boundaries and ordering win.
                start = self._assistant_start_item_index
                if start is not None:
                    del self.state.items[start:]
                if message.stop_reason in {"error", "aborted"}:
                    self.state.add_assistant_error(message)
                    self.state.running = False
                else:
                    self.state.add_assistant_message(message, include_tool_calls=False)
                self.state.assistant_buffer = ""
                self._assistant_start_item_index = None
            return
        if isinstance(event, ToolExecutionStartEvent):
            self._flush()
            self.state.add_tool_call(
                ToolCall(id=event.tool_call_id, name=event.tool_name, arguments=event.args)
            )
            return
        if isinstance(event, ToolExecutionUpdateEvent):
            self.state.record_tool_update(event.tool_call_id, event.partial_result.text)
            return
        if isinstance(event, ToolExecutionEndEvent):
            self.state.record_tool_result(
                event.tool_call_id,
                event.tool_name,
                event.result,
                event.is_error,
            )
            return
        if isinstance(event, AutoRetryStartEvent):
            self.state.add_item("status", f"… {event.error_message}")

    def _flush(self) -> None:
        if self.state.assistant_buffer:
            self.state.add_item("assistant", self.state.assistant_buffer)
            self.state.assistant_buffer = ""
