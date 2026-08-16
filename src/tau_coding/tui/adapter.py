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
from tau_coding.events import (
    AutoRetryStartEvent,
    CodingSessionEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    QueueUpdateEvent,
    SessionAgentEndEvent,
)
from tau_coding.session import is_context_overflow_error
from tau_coding.tui.state import TuiState, _is_file_mutation_only_message


class TuiEventAdapter:
    def __init__(self, state: TuiState) -> None:
        self.state = state
        self._assistant_start_item_index: int | None = None
        self._pending_overflow_error: AssistantMessage | None = None
        self._tool_batch_ids: dict[str, int] = {}
        self._file_mutation_continuation_calls: set[str] = set()

    def apply(self, event: CodingSessionEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self.state.running = True
            self.state.error = None
            return
        if isinstance(event, AgentEndEvent):
            # A bare harness event is terminal for legacy/direct adapter callers.
            self._flush()
            self.state.running = False
            return
        if isinstance(event, SessionAgentEndEvent):
            # Session orchestration may still compact, retry, or drain queued work.
            self._flush()
            return
        if event.type == "agent_settled":
            self._flush()
            if self._pending_overflow_error is not None:
                self.state.add_assistant_error(self._pending_overflow_error)
                self._pending_overflow_error = None
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
                    if is_context_overflow_error(message):
                        # Keep the provider failure provisional while session-level
                        # overflow compaction and retry are still in progress.
                        self._pending_overflow_error = message
                    else:
                        # Successful overflow compaction makes the retry failure the
                        # only terminal error worth presenting.
                        self._pending_overflow_error = None
                        self.state.add_assistant_error(message)
                        self.state.running = False
                else:
                    self._pending_overflow_error = None
                    self.state.add_assistant_message(message, include_tool_calls=False)
                    previous_was_tool = False
                    batch_id: int | None = None
                    allows_mutation_continuation = _is_file_mutation_only_message(message)
                    for block in message.content:
                        if isinstance(block, ToolCall):
                            if not previous_was_tool:
                                batch_id = self.state.new_tool_batch_id()
                            if batch_id is not None:
                                self._tool_batch_ids[block.id] = batch_id
                            if allows_mutation_continuation:
                                self._file_mutation_continuation_calls.add(block.id)
                            previous_was_tool = True
                        else:
                            previous_was_tool = False
                self.state.assistant_buffer = ""
                self._assistant_start_item_index = None
            return
        if isinstance(event, ToolExecutionStartEvent):
            self._flush()
            self.state.add_tool_call(
                ToolCall(id=event.tool_call_id, name=event.tool_name, arguments=event.args),
                batch_id=self._tool_batch_ids.pop(event.tool_call_id, None),
                allows_file_mutation_continuation=(
                    event.tool_call_id in self._file_mutation_continuation_calls
                ),
            )
            self._file_mutation_continuation_calls.discard(event.tool_call_id)
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
        if isinstance(event, CompactionStartEvent) and event.reason == "overflow":
            self.state.add_item("status", "… Context limit reached; compacting and retrying")
            return
        if isinstance(event, CompactionEndEvent) and event.reason == "overflow":
            if (event.aborted or event.error_message) and self._pending_overflow_error is not None:
                self.state.add_assistant_error(self._pending_overflow_error)
                self._pending_overflow_error = None
            return
        if isinstance(event, AutoRetryStartEvent):
            self.state.add_item("status", f"… {event.error_message}")

    def _flush(self) -> None:
        if self.state.assistant_buffer:
            self.state.add_item("assistant", self.state.assistant_buffer)
            self.state.assistant_buffer = ""
