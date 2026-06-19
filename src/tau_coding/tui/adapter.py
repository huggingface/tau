"""Translate agent events into Textual TUI display state."""

from tau_agent import (
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
)
from tau_coding.tui.state import TuiState


class TuiEventAdapter:
    """Apply portable agent events to mutable TUI display state."""

    def __init__(self, state: TuiState) -> None:
        self.state = state

    def apply(self, event: AgentEvent) -> None:
        """Apply one agent event to the display state."""
        if isinstance(event, AgentStartEvent):
            self.state.running = True
            self.state.error = None
            return

        if isinstance(event, AgentEndEvent):
            self._flush_assistant_buffer()
            self.state.running = False
            return

        if isinstance(event, MessageStartEvent):
            if event.message_role == "assistant":
                self.state.assistant_buffer = ""
            return

        if isinstance(event, MessageDeltaEvent):
            self.state.assistant_buffer += event.delta
            return

        if isinstance(event, ThinkingDeltaEvent):
            self.state.add_thinking_delta(event.delta)
            return

        if isinstance(event, QueueUpdateEvent):
            self.state.update_queue(steering=event.steering, follow_up=event.follow_up)
            return

        if isinstance(event, MessageEndEvent):
            if event.message.role == "user":
                self.state.add_user_message(event.message.content)
                return
            if event.message.role == "tool":
                return
            text = event.message.content or self.state.assistant_buffer
            if text:
                self.state.add_item("assistant", text)
            self.state.assistant_buffer = ""
            return

        if isinstance(event, ToolExecutionStartEvent):
            self._flush_assistant_buffer()
            self.state.add_tool_call(event.tool_call)
            return

        if isinstance(event, ToolExecutionUpdateEvent):
            self.state.add_item("tool", f"… {event.message}")
            return

        if isinstance(event, RetryEvent):
            self.state.add_item("status", f"… {event.message}")
            return

        if isinstance(event, ToolExecutionEndEvent):
            self.state.record_tool_result(event.result)
            return

        if isinstance(event, ErrorEvent):
            self._flush_assistant_buffer()
            if event.recoverable and event.message == "Agent run cancelled":
                self.state.add_item("status", "Agent run cancelled.")
                return
            self.state.error = event.message
            self.state.add_item("error", f"Error: {event.message}")
            if not event.recoverable:
                self.state.running = False

    def _flush_assistant_buffer(self) -> None:
        if self.state.assistant_buffer:
            self.state.add_item("assistant", self.state.assistant_buffer)
            self.state.assistant_buffer = ""
