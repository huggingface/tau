"""Cross-provider transcript compilation tests."""

from __future__ import annotations

import re

from tau_agent import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_ai.anthropic import _build_messages_payload
from tau_ai.env import CACHE_RETENTION_NONE
from tau_ai.tool_call_ids import portable_tool_call_id


def test_portable_tool_call_id_preserves_safe_ids_and_hashes_native_ids() -> None:
    assert portable_tool_call_id("call_safe-1") == "call_safe-1"

    first = portable_tool_call_id("call_1|fc_1")
    second = portable_tool_call_id("call_1|fc_2")

    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert first == portable_tool_call_id("call_1|fc_1")
    assert first != second
    assert len(first) <= 64


def test_anthropic_compiles_codex_tool_history_with_portable_ids() -> None:
    first_native_id = "call_first|fc_first"
    second_native_id = "call_second|fc_second"
    messages = [
        UserMessage(content="Inspect two files"),
        AssistantMessage(
            api="openai-codex-responses",
            provider="openai-codex",
            model="gpt-test",
            content=[
                ThinkingContent(
                    thinking="private reasoning",
                    thinking_signature='{"type":"reasoning","id":"rs_1"}',
                ),
                ToolCall(id=first_native_id, name="read", arguments={"path": "a.py"}),
                ToolCall(id=second_native_id, name="read", arguments={"path": "b.py"}),
            ],
        ),
        ToolResultMessage(
            tool_call_id=first_native_id,
            tool_name="read",
            content=[TextContent(text="a")],
        ),
        ToolResultMessage(
            tool_call_id=second_native_id,
            tool_name="read",
            content=[TextContent(text="b")],
        ),
        UserMessage(content="Summarize"),
    ]

    payload = _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        messages=messages,
        tools=[],
        cache_retention=CACHE_RETENTION_NONE,
    )

    payload_messages = payload["messages"]
    assert isinstance(payload_messages, list)
    assistant_content = payload_messages[1]["content"]
    assert isinstance(assistant_content, list)
    assert [block["type"] for block in assistant_content] == ["tool_use", "tool_use"]

    tool_use_ids = [block["id"] for block in assistant_content]
    result_ids = [payload_messages[index]["content"][0]["tool_use_id"] for index in (2, 3)]
    assert tool_use_ids == result_ids
    assert len(set(tool_use_ids)) == 2
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in tool_use_ids)


def test_anthropic_omits_foreign_thinking_only_assistant_turn() -> None:
    messages = [
        UserMessage(content="Think about this"),
        AssistantMessage(
            api="openai-responses",
            provider="openai",
            model="gpt-test",
            content=[
                ThinkingContent(
                    thinking="private reasoning",
                    thinking_signature='{"type":"reasoning","id":"rs_1"}',
                )
            ],
            stop_reason="length",
        ),
        UserMessage(content="Continue with Claude"),
    ]

    payload = _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        messages=messages,
        tools=[],
        cache_retention=CACHE_RETENTION_NONE,
    )

    assert payload["messages"] == [
        {"role": "user", "content": "Think about this"},
        {"role": "user", "content": "Continue with Claude"},
    ]


def test_anthropic_preserves_native_anthropic_thinking_and_safe_tool_ids() -> None:
    messages = [
        AssistantMessage(
            api="anthropic-messages",
            provider="anthropic",
            model="claude-test",
            content=[
                ThinkingContent(thinking="reasoning", thinking_signature="signature"),
                ToolCall(id="toolu_safe_1", name="read", arguments={}),
            ],
        )
    ]

    payload = _build_messages_payload(
        model="claude-test",
        system="You are Tau.",
        messages=messages,
        tools=[],
        cache_retention=CACHE_RETENTION_NONE,
    )

    payload_messages = payload["messages"]
    assert isinstance(payload_messages, list)
    content = payload_messages[0]["content"]
    assert isinstance(content, list)
    assert content == [
        {"type": "thinking", "thinking": "reasoning", "signature": "signature"},
        {"type": "tool_use", "id": "toolu_safe_1", "name": "read", "input": {}},
    ]
