from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    QueueUpdateEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.types import JSONValue


def test_user_message_serializes_with_role() -> None:
    message = UserMessage(content="hello")

    # custom_type/details are omitted when unset, so plain messages keep the
    # pre-metadata wire shape (forward compat with older binaries).
    assert message.model_dump() == {"role": "user", "content": "hello"}


def test_user_message_custom_metadata_round_trips() -> None:
    message = UserMessage(
        content="<task-notification/>",
        custom_type="subagent-notification",
        details={"id": "run-1", "turns": 3},
    )

    restored = UserMessage.model_validate(message.model_dump())

    assert restored.custom_type == "subagent-notification"
    assert restored.details == {"id": "run-1", "turns": 3}


def test_user_message_loads_from_legacy_payload_without_custom_fields() -> None:
    # A session persisted before custom_type/details existed must still load.
    restored = UserMessage.model_validate({"role": "user", "content": "hello"})

    assert restored.custom_type is None
    assert restored.details is None


def test_message_entry_round_trips_custom_user_message() -> None:
    from tau_agent.session.entries import MessageEntry

    entry = MessageEntry(
        message=UserMessage(
            content="<task-notification/>",
            custom_type="subagent-notification",
            details={"id": "run-1"},
        )
    )

    restored = MessageEntry.model_validate_json(entry.model_dump_json())

    assert isinstance(restored.message, UserMessage)
    assert restored.message.custom_type == "subagent-notification"
    assert restored.message.details == {"id": "run-1"}


def test_assistant_message_can_include_tool_calls() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    message = AssistantMessage(content="I'll read that.", tool_calls=[tool_call])

    assert message.role == "assistant"
    assert message.tool_calls[0].name == "read"
    assert message.model_dump()["tool_calls"][0]["arguments"] == {"path": "README.md"}


def test_tool_result_message_records_tool_output() -> None:
    message = ToolResultMessage(
        tool_call_id="call-1",
        name="read",
        content="file contents",
        ok=True,
        data={"path": "README.md"},
        details={"bytes": 13},
        error=None,
    )

    assert message.role == "tool"
    assert message.tool_call_id == "call-1"
    assert message.data == {"path": "README.md"}
    assert message.details == {"bytes": 13}


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UserMessage(content="hello", unexpected=True)  # type: ignore[call-arg]


@pytest.mark.anyio
async def test_agent_tool_executes_with_json_arguments() -> None:
    class FakeCancellationToken:
        def is_cancelled(self) -> bool:
            return False

    observed_signal: list[object | None] = []

    async def executor(
        arguments: Mapping[str, JSONValue],
        *,
        signal: object | None = None,
    ) -> AgentToolResult:
        observed_signal.append(signal)
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

    signal = FakeCancellationToken()
    result = await tool.execute({"text": "hi"}, signal=signal)

    assert result.ok is True
    assert result.content == "hi"
    assert observed_signal == [signal]


def test_events_have_stable_type_names() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    result = AgentToolResult(tool_call_id="call-1", name="read", ok=True, content="contents")
    message = AssistantMessage(content="Done")

    events = [
        MessageDeltaEvent(delta="hello"),
        QueueUpdateEvent(steering=("adjust",), follow_up=()),
        ThinkingDeltaEvent(delta="reasoning"),
        MessageEndEvent(message=message),
        ToolExecutionStartEvent(tool_call=tool_call),
        ToolExecutionEndEvent(result=result),
        ErrorEvent(message="boom", recoverable=True),
    ]

    assert [event.type for event in events] == [
        "message_delta",
        "queue_update",
        "thinking_delta",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "error",
    ]
