from pathlib import Path

from tau_agent import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from tau_agent.messages import assistant_content
from tau_coding.context_window import (
    ContextUsageEstimate,
    auto_compaction_threshold_for_context_window,
    build_compaction_summary_prompt,
    estimate_context_tokens,
    estimate_context_usage,
    estimate_message_tokens,
    estimate_text_tokens,
    serialize_messages_for_compaction,
    summarize_messages_for_compaction,
)
from tau_coding.tools import create_coding_tools


def test_text_token_estimate_is_deterministic() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a") == 1
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("abcde") == 2


def test_message_token_estimate_counts_roles_and_tool_calls() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})

    user_tokens = estimate_message_tokens(UserMessage(content="hello"))
    assistant_tokens = estimate_message_tokens(
        AssistantMessage(content=assistant_content("using tool", [tool_call]))
    )
    tool_tokens = estimate_message_tokens(
        ToolResultMessage(
            tool_call_id="call-1", tool_name="read", content=[TextContent(text="contents")]
        )
    )

    assert user_tokens > estimate_text_tokens("hello")
    assert assistant_tokens > user_tokens
    assert tool_tokens > estimate_text_tokens("contents")


def test_context_token_estimate_includes_system_messages_and_tools(tmp_path: Path) -> None:
    tools = tuple(create_coding_tools(cwd=tmp_path))

    estimate = estimate_context_tokens(
        system="You are Tau.",
        messages=(UserMessage(content="hello"), AssistantMessage(content="hi")),
        tools=tools,
    )

    assert estimate > estimate_text_tokens("You are Tau.hellohi")


def test_auto_compaction_threshold_keeps_pi_style_reserve() -> None:
    assert auto_compaction_threshold_for_context_window(128_000) == 111_616
    assert auto_compaction_threshold_for_context_window(16_384) == 1
    assert auto_compaction_threshold_for_context_window(0) is None


def test_context_usage_estimate_reports_breakdown(tmp_path: Path) -> None:
    tools = tuple(create_coding_tools(cwd=tmp_path))
    messages = (UserMessage(content="hello"), AssistantMessage(content="hi"))

    usage = estimate_context_usage(system="You are Tau.", messages=messages, tools=tools)

    assert isinstance(usage, ContextUsageEstimate)
    assert usage.message_count == 2
    assert usage.tool_count == len(tools)
    assert usage.system_tokens == estimate_text_tokens("You are Tau.")
    assert usage.message_tokens == sum(estimate_message_tokens(message) for message in messages)
    assert usage.total_tokens == usage.system_tokens + usage.message_tokens + usage.tool_tokens
    assert estimate_context_tokens(system="You are Tau.", messages=messages, tools=tools) == (
        usage.total_tokens
    )


def test_summarize_messages_for_compaction_is_deterministic() -> None:
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})

    summary = summarize_messages_for_compaction(
        (
            UserMessage(content="Read README.md"),
            AssistantMessage(content=assistant_content("I'll inspect it.", [tool_call])),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[TextContent(text="README contents")],
            ),
        )
    )

    assert summary == "\n".join(
        [
            "Automatically compacted 3 prior message(s).",
            "1. user: Read README.md",
            "2. assistant: I'll inspect it. [tool calls: read]",
            "3. tool: read ok: README contents",
        ]
    )


def test_compaction_summary_prompt_uses_pi_format_and_custom_instructions() -> None:
    prompt = build_compaction_summary_prompt(
        (
            UserMessage(content="Refactor src/app.py"),
            AssistantMessage(content="Updated src/app.py"),
        ),
        custom_instructions="Focus on files changed.",
    )

    assert "<conversation>" in prompt
    assert "Use this EXACT format:" in prompt
    assert "## Goal" in prompt
    assert "Preserve exact file paths" in prompt
    assert "Additional focus: Focus on files changed." in prompt
    assert "Refactor src/app.py" in prompt


def test_compaction_summary_prompt_updates_previous_summary() -> None:
    prompt = build_compaction_summary_prompt(
        (
            UserMessage(content="Previous conversation summary:\n## Goal\nShip compaction."),
            UserMessage(content="Now add tests."),
        )
    )

    assert "<previous-summary>\n## Goal\nShip compaction.\n</previous-summary>" in prompt
    assert "NEW conversation messages" in prompt
    assert "Now add tests." in prompt
    assert "Previous conversation summary" not in serialize_messages_for_compaction(
        (UserMessage(content="Now add tests."),)
    )
