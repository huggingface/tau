import pytest

from pi_event_helpers import assistant_done
from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.session import JsonlSessionStorage, MessageEntry
from tau_ai import FakeProvider
from tau_ai.anthropic import _AnthropicToolBuilder
from tau_ai.mistral import _ToolCallBuilder as MistralBuilder
from tau_ai.openai_codex import _ToolCallBuilder as CodexBuilder
from tau_ai.openai_compatible import _ResponsesToolCallBuilder, _ToolCallBuilder


def build_call(kind, raw):
    if kind == "codex":
        builder = CodexBuilder(call_id="call", item_id="item", name="read")
    else:
        builder = {
            "anthropic": _AnthropicToolBuilder,
            "mistral": MistralBuilder,
            "chat": _ToolCallBuilder,
            "responses": _ResponsesToolCallBuilder,
        }[kind]()
        builder.name = "read"
    builder.arguments_parts = [raw[:3], raw[3:]]
    return builder.build() if kind == "codex" else builder.build(0)


@pytest.mark.parametrize("kind", ["anthropic", "mistral", "codex", "chat", "responses"])
@pytest.mark.parametrize("raw", ['{"path":', '["a"]', "null", "42", " "])
def test_builders_mark_invalid_objects(kind, raw):
    call = build_call(kind, raw)
    assert call.arguments == {}
    assert call.malformed_arguments_text == raw


@pytest.mark.parametrize("kind", ["anthropic", "mistral", "codex", "chat", "responses"])
@pytest.mark.parametrize("raw, expected", [("", {}), ("{}", {}), ('{"path":"a"}', {"path": "a"})])
def test_builders_preserve_valid_and_empty_arguments(kind, raw, expected):
    call = build_call(kind, raw)
    assert call.arguments == expected
    assert call.malformed_arguments_text is None


@pytest.mark.anyio
async def test_bad_arguments_feedback_allows_corrected_call_and_survives_jsonl(tmp_path):
    executed = []
    observed = []

    async def execute(call_id, arguments, signal, on_update):
        executed.append(arguments)
        return AgentToolResult(content=[TextContent(text="file contents")])

    async def before(call):
        observed.append(call)
        return False, None

    bad = build_call("chat", '{"path":')
    good = ToolCall(id="fixed", name="read", arguments={"path": "a"})
    provider = FakeProvider(
        [
            [assistant_done(AssistantMessage(content=[bad]))],
            [assistant_done(AssistantMessage(content=[good]))],
            [assistant_done(AssistantMessage(content="done"))],
        ]
    )
    messages = []
    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="",
            messages=messages,
            tools=[
                AgentTool(
                    name="read", label="Read", description="Read", parameters={}, execute_fn=execute
                )
            ],
            before_tool_call=before,
        )
    ]
    assert executed == [{"path": "a"}]
    assert observed == [bad, good]
    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.is_error
    assert not next(
        message
        for message in messages
        if isinstance(message, ToolResultMessage) and message.tool_call_id == "fixed"
    ).is_error
    assert '{"path":' in result.text
    assert "correct" in result.text and "JSON" in result.text
    start = next(i for i, event in enumerate(events) if event.type == "tool_execution_start")
    assert [event.type for event in events[start : start + 4]] == [
        "tool_execution_start",
        "tool_execution_end",
        "message_start",
        "message_end",
    ]
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    for message in messages:
        await storage.append(MessageEntry(message=message))
    raw = (tmp_path / "session.jsonl").read_text(encoding="utf-8")
    assert "malformedArgumentsText" not in raw
    assert "malformed_arguments_text" not in raw
    restored = await storage.read_all()
    assert restored[0].message.tool_calls[0].malformed_arguments_text is None
    assert restored[1].message == result


@pytest.mark.anyio
async def test_gate_takes_precedence_over_bad_arguments():
    async def before(call):
        assert call.malformed_arguments_text == "broken"
        return True, "Blocked by extension"

    events = [
        event
        async for event in run_agent_loop(
            provider=FakeProvider(
                [[assistant_done(AssistantMessage(content=[build_call("chat", "broken")]))]]
            ),
            model="fake",
            system="",
            messages=[],
            tools=[],
            before_tool_call=before,
        )
    ]
    result = next(
        event.message
        for event in events
        if event.type == "message_end" and isinstance(event.message, ToolResultMessage)
    )
    assert result.text == "Blocked by extension"
