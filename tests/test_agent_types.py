from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.provider_events import TextDeltaEvent
from tau_agent.types import JSONValue


def test_user_message_serializes_with_pi_wire_shape() -> None:
    message = UserMessage(content="hello", timestamp=123)

    assert message.model_dump() == {"role": "user", "content": "hello", "timestamp": 123}


def test_assistant_message_keeps_ordered_content_blocks() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    message = AssistantMessage(
        content=[TextContent(text="I'll read that."), tool_call],
        model="fake",
        timestamp=123,
    )

    assert message.role == "assistant"
    assert message.text == "I'll read that."
    assert message.tool_calls == (tool_call,)
    assert message.model_dump(by_alias=True)["content"][1] == {
        "type": "toolCall",
        "id": "call-1",
        "name": "read",
        "arguments": {"path": "README.md"},
        "thoughtSignature": None,
    }


def test_assistant_message_persists_thinking_blocks_and_signatures() -> None:
    message = AssistantMessage(
        content=[
            ThinkingContent(thinking="plan", thinking_signature="reasoning_content"),
            TextContent(text="done"),
        ],
        model="fake",
        timestamp=123,
    )

    assert message.thinking_text == "plan"
    payload = message.model_dump(by_alias=True)
    assert payload["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "thinkingSignature": "reasoning_content",
        "redacted": False,
    }


def test_tool_result_message_records_canonical_tool_output() -> None:
    message = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="file contents")],
        details={"bytes": 13},
        is_error=False,
        timestamp=123,
    )

    assert message.role == "toolResult"
    assert message.tool_name == "read"
    assert message.text == "file contents"
    assert message.details == {"bytes": 13}
    assert message.model_dump(by_alias=True)["toolCallId"] == "call-1"


def test_tool_result_message_accepts_pi_camel_case_wire_keys() -> None:
    message = ToolResultMessage.model_validate(
        {
            "role": "toolResult",
            "toolCallId": "call-1",
            "toolName": "read",
            "content": [{"type": "text", "text": "file contents"}],
            "timestamp": 123,
            "isError": False,
        }
    )

    assert message.tool_call_id == "call-1"
    assert message.tool_name == "read"
    assert message.is_error is False
    assert message.text == "file contents"


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UserMessage(content="hello", unexpected=True)  # type: ignore[call-arg]


@pytest.mark.anyio
async def test_agent_tool_executes_with_pi_arguments() -> None:
    class FakeCancellationToken:
        def is_cancelled(self) -> bool:
            return False

    observed: list[tuple[str, Mapping[str, JSONValue], object | None]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
        on_update: object | None = None,
    ) -> AgentToolResult:
        del on_update
        observed.append((tool_call_id, arguments, signal))
        return AgentToolResult(content=[TextContent(text=str(arguments["text"]))])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo text.",
        parameters={"type": "object"},
        execute_fn=execute,  # type: ignore[arg-type]
    )

    signal = FakeCancellationToken()
    result = await tool.execute("call-1", {"text": "hi"}, signal=signal)

    assert result.text == "hi"
    assert observed == [("call-1", {"text": "hi"}, signal)]


def test_agent_events_have_stable_pi_type_names() -> None:
    partial = AssistantMessage(content="hello")
    nested = TextDeltaEvent(content_index=0, delta="hello", partial=partial)
    result = AgentToolResult(content=[TextContent(text="contents")])
    message = AssistantMessage(content="Done")

    events = [
        MessageStartEvent(message=partial),
        MessageUpdateEvent(message=partial, assistant_message_event=nested),
        MessageEndEvent(message=message),
        ToolExecutionStartEvent(
            tool_call_id="call-1", tool_name="read", args={"path": "README.md"}
        ),
        ToolExecutionEndEvent(
            tool_call_id="call-1", tool_name="read", result=result, is_error=False
        ),
    ]

    assert [event.type for event in events] == [
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
    ]
