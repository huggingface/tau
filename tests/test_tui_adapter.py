from pathlib import Path

from tau_agent import (
    AgentEndEvent,
    AgentStartEvent,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    RetryEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    UserMessage,
)
from tau_coding.skills import Skill, format_skill_invocation
from tau_coding.tui import TuiEventAdapter, TuiState
from tau_coding.tui.state import format_tool_call_block, format_tool_result_block


def test_tui_adapter_tracks_running_state() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(AgentStartEvent())
    assert state.running is True

    adapter.apply(AgentEndEvent())
    assert state.running is False


def test_tui_adapter_builds_assistant_items_from_streamed_messages() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageStartEvent())
    adapter.apply(MessageDeltaEvent(delta="Hel"))
    adapter.apply(MessageDeltaEvent(delta="lo"))
    assert state.assistant_buffer == "Hello"
    assert state.items == []

    adapter.apply(MessageEndEvent(message=AssistantMessage(content="Hello")))

    assert state.assistant_buffer == ""
    assert [(item.role, item.text) for item in state.items] == [("assistant", "Hello")]


def test_tui_adapter_builds_user_items_from_streamed_messages() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageStartEvent(message_role="user"))
    adapter.apply(MessageEndEvent(message=UserMessage(content="Hello Tau")))

    assert state.assistant_buffer == ""
    assert [(item.role, item.text) for item in state.items] == [("user", "Hello Tau")]


def test_tui_adapter_compacts_streamed_skill_invocations() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review\nFull noisy instructions.",
        description="Review code",
    )

    adapter.apply(
        MessageEndEvent(
            message=UserMessage(content=format_skill_invocation(skill, "check the auth flow"))
        )
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("skill", "Using skill: review"),
        ("user", "check the auth flow"),
    ]


def test_tui_adapter_groups_thinking_deltas_separately() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(ThinkingDeltaEvent(delta="hidden "))
    adapter.apply(ThinkingDeltaEvent(delta="reasoning"))

    assert [(item.role, item.text) for item in state.items] == [("thinking", "hidden reasoning")]
    assert state.show_thinking is False


def test_tui_adapter_flushes_assistant_buffer_before_tool_events() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageDeltaEvent(delta="Before tool"))
    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
        )
    )

    assert state.assistant_buffer == ""
    assert state.items[0].role == "assistant"
    assert state.items[0].text == "Before tool"
    assert state.items[1].role == "tool"
    assert "→ read" in state.items[1].text


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
            tool_call=ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "/workspace/.tau/skills/review.md"},
            )
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-1",
                name="read",
                ok=True,
                content="# Review\nFull instructions.",
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("skill", "Loading skill: review", "✓ read\n# Review\nFull instructions.")
    ]


def test_tui_adapter_leaves_ordinary_reads_as_tool_items() -> None:
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
            tool_call=ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "/workspace/README.md"},
            )
        )
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("tool", "→ read /workspace/README.md")
    ]


def test_tool_call_blocks_use_human_readable_invocations() -> None:
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
    assert (
        format_tool_call_block(
            ToolCall(id="call-2", name="edit", arguments={"path": "src/tau_coding/tui/app.py"})
        )
        == "→ edit src/tau_coding/tui/app.py"
    )
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-3",
                name="bash",
                arguments={
                    "command": "git log --oneline --decorate --graph --max-count=8",
                    "timeout": 30,
                },
            )
        )
        == "$ git log --oneline --decorate --graph --max-count=8 (timeout 30s)"
    )


def test_tui_adapter_records_tool_updates_and_results() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(ToolExecutionUpdateEvent(tool_call_id="call-1", message="reading"))
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(tool_call_id="call-1", name="read", ok=True, content="done")
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-2",
                name="bash",
                ok=False,
                content="failed",
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("tool", "… reading", None),
        ("tool", "✓ read", "✓ read\ndone"),
        ("tool", "✗ bash", "✗ bash\nfailed"),
    ]


def test_tui_adapter_records_retry_status() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        RetryEvent(
            attempt=2,
            max_attempts=3,
            delay_seconds=0,
            message="Retrying provider request 2/3 after HTTP 503.",
        )
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("status", "… Retrying provider request 2/3 after HTTP 503.")
    ]


def test_tui_adapter_records_queue_updates() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(QueueUpdateEvent(steering=("adjust",), follow_up=("after",)))

    assert state.queued_steering == ("adjust",)
    assert state.queued_follow_up == ("after",)
    assert state.queued_message_count == 2


def test_tool_result_blocks_preview_long_content() -> None:
    content = "\n".join(f"line {index}" for index in range(1, 12))

    block = format_tool_result_block(name="read", ok=True, content=content)

    assert "line 1" in block
    assert "line 8" in block
    assert "line 9" not in block
    assert "3 more lines" in block


def test_tui_adapter_renders_live_edit_patch() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-1",
                name="edit",
                ok=True,
                content="Successfully replaced 1 block.",
                data={"patch": "--- a.py\n+++ a.py\n@@\n-old\n+new"},
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        (
            "tool",
            "✓ edit",
            "✓ edit\nSuccessfully replaced 1 block.\n\nPatch:\n--- a.py\n+++ a.py\n@@\n-old\n+new",
        )
    ]


def test_tui_adapter_records_errors_and_stops_on_non_recoverable_error() -> None:
    state = TuiState(running=True, assistant_buffer="partial")
    adapter = TuiEventAdapter(state)

    adapter.apply(ErrorEvent(message="provider failed", recoverable=False))

    assert state.running is False
    assert state.error == "provider failed"
    assert [(item.role, item.text) for item in state.items] == [
        ("assistant", "partial"),
        ("error", "Error: provider failed"),
    ]


def test_tui_adapter_renders_cancellation_as_status() -> None:
    state = TuiState(running=True, assistant_buffer="partial")
    adapter = TuiEventAdapter(state)

    adapter.apply(ErrorEvent(message="Agent run cancelled", recoverable=True))

    assert state.running is True
    assert state.error is None
    assert [(item.role, item.text) for item in state.items] == [
        ("assistant", "partial"),
        ("status", "Agent run cancelled."),
    ]
