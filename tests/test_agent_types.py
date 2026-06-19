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

    assert message.model_dump() == {"role": "user", "content": "hello"}


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
