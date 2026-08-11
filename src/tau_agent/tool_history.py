"""Provider-safe repair of malformed tool-call message history."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

_INTERRUPTED_TOOL_RESULT = "Tool call interrupted by user"


@dataclass(frozen=True, slots=True)
class ToolHistoryRepair:
    """A provider-safe transcript plus a summary of deterministic repairs."""

    messages: tuple[AgentMessage, ...]
    changed: bool = False
    synthesized_results: int = 0
    dropped_orphan_results: int = 0
    dropped_duplicate_results: int = 0
    reordered_results: int = 0

    def diagnostic_data(self) -> dict[str, int]:
        """Return JSON-safe counters for durable session diagnostics."""
        return {
            "synthesizedResults": self.synthesized_results,
            "droppedOrphanResults": self.dropped_orphan_results,
            "droppedDuplicateResults": self.dropped_duplicate_results,
            "reorderedResults": self.reordered_results,
        }


def repair_tool_history(messages: tuple[AgentMessage, ...]) -> ToolHistoryRepair:
    """Return history where every tool call has exactly one adjacent result.

    Existing result messages are moved beside their calls. Missing results get a
    deterministic interruption error. Results with no call are omitted because
    a missing call's arguments cannot be reconstructed safely. When duplicate
    results exist, a real result is preferred over Tau's synthetic interruption.
    """
    calls: dict[str, ToolCall] = {}
    expected_result_positions: dict[str, int] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, AssistantMessage):
            continue
        for call_offset, call in enumerate(message.tool_calls, start=1):
            calls.setdefault(call.id, call)
            expected_result_positions.setdefault(call.id, message_index + call_offset)

    results_by_id: dict[str, list[ToolResultMessage]] = defaultdict(list)
    for message in messages:
        if isinstance(message, ToolResultMessage):
            results_by_id[message.tool_call_id].append(message)

    selected_results: dict[str, ToolResultMessage] = {}
    synthesized_results = 0
    dropped_duplicate_results = 0
    for call_id, call in calls.items():
        candidates = results_by_id.get(call_id, [])
        if candidates:
            selected_results[call_id] = next(
                (result for result in candidates if not _is_interruption_result(result)),
                candidates[0],
            )
            dropped_duplicate_results += len(candidates) - 1
            continue
        selected_results[call_id] = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[TextContent(text=_INTERRUPTED_TOOL_RESULT)],
            is_error=True,
        )
        synthesized_results += 1

    original_positions = {id(message): index for index, message in enumerate(messages)}
    repaired: list[AgentMessage] = []
    emitted_call_ids: set[str] = set()
    reordered_results = 0
    for message in messages:
        if isinstance(message, ToolResultMessage):
            continue
        repaired.append(message)
        if not isinstance(message, AssistantMessage):
            continue
        for call in message.tool_calls:
            if call.id in emitted_call_ids:
                continue
            emitted_call_ids.add(call.id)
            result = selected_results[call.id]
            repaired.append(result)
            original_position = original_positions.get(id(result))
            if original_position is not None and original_position != expected_result_positions.get(
                call.id
            ):
                reordered_results += 1

    orphan_results = sum(
        len(results) for call_id, results in results_by_id.items() if call_id not in calls
    )
    repaired_messages = tuple(repaired)
    return ToolHistoryRepair(
        messages=repaired_messages,
        changed=repaired_messages != messages,
        synthesized_results=synthesized_results,
        dropped_orphan_results=orphan_results,
        dropped_duplicate_results=dropped_duplicate_results,
        reordered_results=reordered_results,
    )


def _is_interruption_result(message: ToolResultMessage) -> bool:
    return message.is_error and message.text == _INTERRUPTED_TOOL_RESULT
