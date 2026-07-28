"""Anthropic prompt-cache breakpoint placement."""

from __future__ import annotations

from typing import Any

from tau_agent import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.tools import AgentTool, ToolCall
from tau_ai.anthropic import (
    MAX_CACHE_BREAKPOINTS,
    _build_messages_payload,
)
from tau_ai.env import (
    CACHE_RETENTION_LONG,
    CACHE_RETENTION_NONE,
    CACHE_RETENTION_SHORT,
)

OAUTH_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."


async def _never_called(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
    raise AssertionError("tool must not execute")


def _tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        label=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}},
        execute_fn=_never_called,
    )


def _assistant_with_calls(count: int, *, offset: int = 0) -> AssistantMessage:
    return AssistantMessage(
        content=[
            TextContent(text="working"),
            *[
                ToolCall(id=f"call-{offset + index}", name="read", arguments={})
                for index in range(count)
            ],
        ],
        stop_reason="toolUse",
    )


def _results(count: int, *, offset: int = 0) -> list[ToolResultMessage]:
    return [
        ToolResultMessage(
            tool_call_id=f"call-{offset + index}",
            tool_name="read",
            content=[TextContent(text=f"result {offset + index}")],
        )
        for index in range(count)
    ]


def _payload(
    messages: list[Any],
    *,
    tools: list[AgentTool] | None = None,
    cache_retention: str = CACHE_RETENTION_SHORT,
    oauth_system_prompt: str | None = None,
) -> dict[str, Any]:
    return _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        oauth_system_prompt=oauth_system_prompt,
        messages=messages,
        tools=tools or [],
        cache_retention=cache_retention,
    )


def _count_breakpoints(payload: dict[str, Any]) -> int:
    def walk(value: object) -> int:
        if isinstance(value, dict):
            return sum(
                (1 if key == "cache_control" else 0) + walk(item) for key, item in value.items()
            )
        if isinstance(value, list):
            return sum(walk(item) for item in value)
        return 0

    return sum(walk(payload.get(field)) for field in ("system", "tools", "messages"))


def _marked_message_indexes(payload: dict[str, Any]) -> list[int]:
    indexes = []
    for index, message in enumerate(payload["messages"]):
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict) and "cache_control" in block for block in content
        ):
            indexes.append(index)
    return indexes


def test_retention_none_leaves_payload_untouched() -> None:
    """Gateways that reject cache_control must see exactly the legacy payload."""
    payload = _payload(
        [UserMessage(content="Say hello")],
        tools=[_tool("read")],
        cache_retention=CACHE_RETENTION_NONE,
    )

    assert _count_breakpoints(payload) == 0
    assert payload["system"] == "You are Tau."
    assert payload["messages"] == [{"role": "user", "content": "Say hello"}]
    assert "cache_control" not in payload["tools"][0]


def test_long_retention_requests_one_hour_ttl_everywhere() -> None:
    long_ttl = {"type": "ephemeral", "ttl": "1h"}
    payload = _payload(
        [UserMessage(content="Say hello")],
        tools=[_tool("read")],
        cache_retention=CACHE_RETENTION_LONG,
    )

    assert payload["system"][0]["cache_control"] == long_ttl
    assert payload["tools"][0]["cache_control"] == long_ttl
    assert payload["messages"][0]["content"][0]["cache_control"] == long_ttl


def test_tools_breakpoint_can_be_suppressed_on_its_own() -> None:
    """A gateway may accept cache_control everywhere except inside tool objects."""
    payload = _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        messages=[UserMessage(content="Say hello")],
        tools=[_tool("read")],
        cache_control_on_tools=False,
    )

    assert "cache_control" not in payload["tools"][0]
    assert "cache_control" in payload["system"][0]
    assert _count_breakpoints(payload) == 2


def test_string_user_content_is_promoted_to_a_marked_block() -> None:
    payload = _payload([UserMessage(content="Say hello")])

    assert payload["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "Say hello",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_only_the_last_tool_and_last_system_block_are_marked() -> None:
    payload = _payload(
        [UserMessage(content="Say hello")],
        tools=[_tool("read"), _tool("write"), _tool("bash")],
        oauth_system_prompt=OAUTH_PROMPT,
    )

    assert [("cache_control" in tool) for tool in payload["tools"]] == [False, False, True]
    system = payload["system"]
    assert system[0]["text"] == OAUTH_PROMPT
    assert "cache_control" not in system[0]
    assert "cache_control" in system[1]


def test_full_oauth_payload_stays_within_the_breakpoint_budget() -> None:
    """Anthropic rejects a fifth breakpoint outright."""
    messages = [
        UserMessage(content="first"),
        _assistant_with_calls(2),
        *_results(2),
        UserMessage(content="second"),
        _assistant_with_calls(2, offset=2),
        *_results(2, offset=2),
    ]
    payload = _payload(
        messages,
        tools=[_tool("read"), _tool("write")],
        cache_retention=CACHE_RETENTION_LONG,
        oauth_system_prompt=OAUTH_PROMPT,
    )

    assert _count_breakpoints(payload) == MAX_CACHE_BREAKPOINTS


def test_second_breakpoint_lands_on_the_previous_request_tail() -> None:
    """The prior turn's last tool result is where the last request's breakpoint sat."""
    messages = [
        UserMessage(content="first"),
        _assistant_with_calls(1),
        *_results(1),
        _assistant_with_calls(1, offset=1),
        *_results(1, offset=1),
    ]
    payload = _payload(messages)

    # Index 2 is the previous request's tail; index 4 is this request's tail.
    assert _marked_message_indexes(payload) == [2, 4]


def test_many_parallel_tool_calls_still_mark_the_previous_tail() -> None:
    """A 12-call turn exceeds the 20-block lookback, which is why B exists."""
    messages = [
        UserMessage(content="first"),
        _assistant_with_calls(12),
        *_results(12),
        _assistant_with_calls(12, offset=12),
        *_results(12, offset=12),
    ]
    payload = _payload(messages)

    marked = _marked_message_indexes(payload)
    assert marked == [13, 26]
    assert payload["messages"][13]["content"][0]["tool_use_id"] == "call-11"
    assert payload["messages"][26]["content"][0]["tool_use_id"] == "call-23"


def test_adjacent_assistant_messages_never_mark_an_assistant() -> None:
    """A retained errored turn followed by a continue leaves two assistants adjacent."""
    messages = [
        UserMessage(content="first"),
        AssistantMessage(content=[TextContent(text="partial")], stop_reason="error"),
        AssistantMessage(content=[TextContent(text="retry")], stop_reason="stop"),
    ]
    payload = _payload(messages, tools=[_tool("read")])

    # The tail is an assistant message, so only the older user prefix is marked.
    assert _marked_message_indexes(payload) == [0]
    for message in payload["messages"]:
        if message["role"] != "assistant":
            continue
        for block in message["content"]:
            assert "cache_control" not in block
    # One message breakpoint plus system and tools.
    assert _count_breakpoints(payload) == 3


def test_empty_user_message_is_not_marked() -> None:
    """An empty text block carrying cache_control is rejected by the API."""
    payload = _payload([UserMessage(content="")])

    assert payload["messages"][0]["content"] == ""
    assert _count_breakpoints(payload) == 1


def test_first_request_marks_only_its_own_tail() -> None:
    payload = _payload([UserMessage(content="first")])

    assert _marked_message_indexes(payload) == [0]


def test_boundary_skips_back_over_a_retained_failed_turn() -> None:
    """Consecutive assistants leave no user message at the true boundary."""
    messages = [
        UserMessage(content="first"),
        AssistantMessage(content=[TextContent(text="partial")], stop_reason="error"),
        AssistantMessage(content=[TextContent(text="retry")], stop_reason="stop"),
        UserMessage(content="second"),
    ]
    payload = _payload(messages)

    # Index 0 is an older prefix than the true boundary, which costs nothing.
    assert _marked_message_indexes(payload) == [0, 3]


def test_image_tail_is_marked_on_the_image_block() -> None:
    messages = [
        UserMessage(
            content=[
                TextContent(text="look"),
                ImageContent(data="aW1hZ2U=", mime_type="image/png"),
            ]
        )
    ]
    payload = _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        messages=messages,
        tools=[],
        supports_images=True,
    )

    content = payload["messages"][0]["content"]
    assert content[-1]["type"] == "image"
    assert content[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in content[0]


def test_empty_tool_result_tail_is_not_marked() -> None:
    """A tool_result carrying no content risks the same rejection as empty text."""
    messages = [
        ToolResultMessage(tool_call_id="call-0", tool_name="read", content=[]),
    ]
    payload = _payload(messages)

    assert payload["messages"][0]["content"][0]["content"] == []
    assert _marked_message_indexes(payload) == []


def test_caller_messages_are_not_mutated() -> None:
    """Breakpoints are applied to freshly built payload dicts, never session state."""
    messages = [UserMessage(content="first"), _assistant_with_calls(1), *_results(1)]
    before = [message.model_dump_json() for message in messages]

    _payload(messages, tools=[_tool("read")], cache_retention=CACHE_RETENTION_LONG)

    assert [message.model_dump_json() for message in messages] == before
