from pathlib import Path

from tau_agent import (
    AgentEndEvent,
    AgentStartEvent,
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
    ToolExecutionUpdateEvent,
    UserMessage,
)
from tau_agent.provider_events import TextDeltaEvent, ThinkingDeltaEvent
from tau_coding.events import (
    AgentSettledEvent,
    AutoRetryStartEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    QueueUpdateEvent,
    SessionAgentEndEvent,
)
from tau_coding.skills import Skill, format_skill_invocation
from tau_coding.tui import TuiEventAdapter, TuiState
from tau_coding.tui.state import (
    BASH_COMMAND_PREVIEW_CHARS,
    format_tool_call_block,
    format_tool_call_invocation,
    format_tool_result_block,
)


def _update(event) -> MessageUpdateEvent:  # noqa: ANN001
    return MessageUpdateEvent(message=event.partial, assistant_message_event=event)


def test_tui_adapter_tracks_running_state() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(AgentStartEvent())
    assert state.running is True

    adapter.apply(AgentEndEvent())
    assert state.running is False


def test_tui_adapter_waits_for_session_settlement_after_low_level_agent_end() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(AgentStartEvent())
    adapter.apply(SessionAgentEndEvent())
    assert state.running is True

    adapter.apply(AgentSettledEvent())
    assert state.running is False


def test_tui_adapter_replaces_recovered_overflow_with_terminal_retry_error() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    overflow = AssistantMessage(
        stop_reason="error",
        error_message="prompt is too long: context window exceeded",
    )
    retry_error = AssistantMessage(
        stop_reason="error",
        error_message="authentication failed: API token expired",
    )

    adapter.apply(AgentStartEvent())
    adapter.apply(MessageEndEvent(message=overflow))
    adapter.apply(SessionAgentEndEvent())
    adapter.apply(CompactionStartEvent(reason="overflow"))
    adapter.apply(CompactionEndEvent(reason="overflow", will_retry=True))
    adapter.apply(AgentStartEvent())
    adapter.apply(MessageEndEvent(message=retry_error))
    adapter.apply(SessionAgentEndEvent())
    adapter.apply(AgentSettledEvent())

    error_items = [item for item in state.items if item.role == "error"]
    assert len(error_items) == 1
    assert error_items[0].text == "Error: authentication failed: API token expired"
    assert state.error == "authentication failed: API token expired"
    assert "context window exceeded" not in state.error


def test_tui_adapter_builds_assistant_item_from_nested_stream_events() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    partial = AssistantMessage()

    adapter.apply(MessageStartEvent(message=partial))
    adapter.apply(_update(TextDeltaEvent(content_index=0, delta="Hel", partial=partial)))
    adapter.apply(_update(TextDeltaEvent(content_index=0, delta="lo", partial=partial)))
    assert state.assistant_buffer == "Hello"

    adapter.apply(MessageEndEvent(message=AssistantMessage(content="Hello")))

    assert state.assistant_buffer == ""
    assert [(item.role, item.text) for item in state.items] == [("assistant", "Hello")]


def test_tui_adapter_builds_user_and_compact_skill_items() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review\nFull instructions.",
        description="Review code",
    )
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageEndEvent(message=UserMessage(content="Hello Tau")))
    adapter.apply(
        MessageEndEvent(message=UserMessage(content=format_skill_invocation(skill, "check auth")))
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("user", "Hello Tau"),
        ("skill", "Using skill: review"),
        ("user", "check auth"),
    ]


def test_tui_adapter_groups_nested_thinking_deltas() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    partial = AssistantMessage()

    adapter.apply(_update(ThinkingDeltaEvent(content_index=0, delta="hidden ", partial=partial)))
    adapter.apply(_update(ThinkingDeltaEvent(content_index=0, delta="reasoning", partial=partial)))

    assert [(item.role, item.text) for item in state.items] == [("thinking", "hidden reasoning")]
    assert state.show_thinking is False


def test_tui_state_restores_persisted_assistant_blocks_in_order() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ThinkingContent(thinking="plan"),
                    TextContent(text="before"),
                    ToolCall(id="call-1", name="read", arguments={"path": "README.md"}),
                    ThinkingContent(thinking="continue"),
                    TextContent(text="done"),
                ]
            )
        ]
    )

    assert [item.role for item in state.items] == [
        "thinking",
        "assistant",
        "tool",
        "thinking",
        "assistant",
    ]


def test_tui_adapter_records_tool_progress_and_result() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionStartEvent(tool_call_id="call-1", tool_name="read", args={"path": "notes.md"})
    )
    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="call-1",
            tool_name="read",
            args={"path": "notes.md"},
            partial_result=AgentToolResult(content="reading"),
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="read",
            result=AgentToolResult(content="done"),
            is_error=False,
        )
    )

    assert [
        (item.role, item.text, item.tool_result_text, item.update_text) for item in state.items
    ] == [("tool", "→ read notes.md", "✓ read\ndone", None)]


def test_tui_adapter_renders_skill_file_reads_with_skill_style() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review",
        description="Review code",
    )
    state = TuiState(skills=(skill,))
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call_id="call-1",
            tool_name="read",
            args={"path": "/workspace/.tau/skills/review.md"},
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="read",
            result=AgentToolResult(content="# Review\nFull instructions."),
            is_error=False,
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("skill", "Loading skill: review", "✓ read\n# Review\nFull instructions.")
    ]


def test_tui_adapter_records_retry_and_queue_status() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        AutoRetryStartEvent(
            attempt=2,
            max_attempts=3,
            delay_ms=0,
            error_message="Retrying provider request 2/3 after HTTP 503.",
        )
    )
    adapter.apply(QueueUpdateEvent(steering=("adjust",), follow_up=("after",)))

    assert [(item.role, item.text) for item in state.items] == [
        ("status", "… Retrying provider request 2/3 after HTTP 503.")
    ]
    assert state.queued_steering == ("adjust",)
    assert state.queued_follow_up == ("after",)


def test_tui_adapter_records_assistant_error_and_aborted_message() -> None:
    state = TuiState(running=True, assistant_buffer="partial")
    adapter = TuiEventAdapter(state)

    adapter.apply(
        MessageEndEvent(
            message=AssistantMessage(stop_reason="error", error_message="provider failed")
        )
    )

    assert state.error == "provider failed"
    assert [(item.role, item.text) for item in state.items] == [("error", "Error: provider failed")]
    assert state.assistant_buffer == ""


def test_tui_state_restores_partial_assistant_response_and_error() -> None:
    state = TuiState()

    state.load_messages(
        [
            AssistantMessage(
                content="partial response",
                stop_reason="error",
                error_message="provider failed",
            )
        ]
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("assistant", "partial response"),
        ("error", "Error: provider failed"),
    ]
    assert state.error == "provider failed"


def test_tool_formatters_keep_human_readable_output() -> None:
    from tau_agent import ToolCall

    assert (
        format_tool_call_block(
            ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "tests/test_tui_app.py", "offset": 1, "limit": 80},
            )
        )
        == "→ read tests/test_tui_app.py:1-80"
    )
    content = "\n".join(f"line {index}" for index in range(1, 12))
    block = format_tool_result_block(name="read", ok=True, content=content)
    assert "line 8" in block
    assert "line 9" not in block
    assert "3 more lines" in block


def test_bash_tool_formatter_keeps_short_commands_visible() -> None:
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"command": 'rg -n "ToolCall" src', "timeout": 10},
    )

    assert format_tool_call_block(call) == '$ rg -n "ToolCall" src (timeout 10s)'
    assert format_tool_call_invocation(call, expanded=True) == (
        '$ rg -n "ToolCall" src (timeout 10s)'
    )


def test_bash_tool_formatter_prefers_description_until_expanded() -> None:
    command = "git diff --check && git commit -m 'Finish work'"
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={
            "command": command,
            "description": "  Validating and\ncommitting changes  ",
            "timeout": 120,
        },
    )

    assert format_tool_call_block(call) == "→ Validating and committing changes (timeout 120s)"
    assert format_tool_call_block(call, compact=False) == f"$ {command} (timeout 120s)"
    assert format_tool_call_invocation(call, expanded=True) == f"$ {command} (timeout 120s)"


def test_bash_tool_formatter_collapses_heredoc_and_expands_exact_command() -> None:
    command = "python - <<'PY'\nprint('one')\nprint('two')\nPY"
    call = ToolCall(id="call-1", name="bash", arguments={"command": command})

    assert format_tool_call_block(call) == ("$ python - <<'PY' … [inline script: 2 lines]")
    assert format_tool_call_invocation(call, expanded=True) == f"$ {command}"


def test_bash_tool_formatter_collapses_multiline_and_long_commands() -> None:
    multiline = ToolCall(
        id="call-1",
        name="bash",
        arguments={"command": "git status &&\ngit diff"},
    )
    long_command = "echo " + "x" * BASH_COMMAND_PREVIEW_CHARS
    long_call = ToolCall(id="call-2", name="bash", arguments={"command": long_command})

    assert format_tool_call_block(multiline) == "$ git status && … [command: 2 lines]"
    assert format_tool_call_block(long_call) == (
        f"$ {long_command[:BASH_COMMAND_PREVIEW_CHARS]}… [command: {len(long_command)} chars]"
    )


def test_bash_tool_formatter_identifies_long_inline_code() -> None:
    code = "print('x');" * 20
    command = f'uv run python -c "{code}"'
    call = ToolCall(id="call-1", name="bash", arguments={"command": command})

    assert format_tool_call_block(call) == (
        f"$ uv run python -c … [inline code: {len(code) + 2} chars]"
    )


def test_tui_state_recovers_full_bash_command_when_tool_output_expands() -> None:
    command = "python - <<'PY'\nprint('hello')\nPY"
    state = TuiState(tool_call_renderer=lambda _name, _arguments: None)
    state.add_tool_call(ToolCall(id="call-1", name="bash", arguments={"command": command}))
    item = state.items[0]
    item.started_at = None

    assert item.text == "$ python - <<'PY' … [inline script: 1 line]"
    assert state.resolve_tool_invocation(item) is None
    assert state.resolve_tool_invocation(item, expanded=True) == f"$ {command}"


def test_tui_adapter_uses_canonical_result_details_for_patch() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="edit",
            result=AgentToolResult(
                content=[TextContent(text="Successfully replaced 1 block.")],
                details={"patch": "--- a.py\n+++ a.py\n@@\n-old\n+new"},
            ),
            is_error=False,
        )
    )

    assert "Patch:\n--- a.py\n+++ a.py" in (state.items[0].tool_result_text or "")
