from pathlib import Path

import pytest

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
    ToolResultMessage,
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


def test_tui_state_groups_adjacent_reads_from_one_assistant_message() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(id="call-1", name="read", arguments={"path": "a.py"}),
                    ToolCall(id="call-2", name="read", arguments={"path": "b.py"}),
                ]
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content="one",
            ),
            ToolResultMessage(
                tool_call_id="call-2",
                tool_name="read",
                content="two",
            ),
        ]
    )

    assert len(state.items) == 1
    item = state.items[0]
    assert item.text == "→ Read 2 files\n  - a.py\n  - b.py"
    assert item.grouped_tool_calls is not None
    assert [member.tool_call_id for member in item.grouped_tool_calls] == ["call-1", "call-2"]
    assert state.find_tool_item("call-1") is item
    assert state.find_tool_item("call-2") is item
    assert state.resolve_tool_invocation(item, expanded=True) == "→ read a.py\n→ read b.py"
    assert item.tool_result_text == "✓ read group"


def test_tui_state_clusters_edits_and_lists_every_path() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(id="edit-1", name="edit", arguments={"path": "a.py", "edits": []}),
                    ToolCall(id="edit-2", name="edit", arguments={"path": "b.py", "edits": []}),
                ]
            ),
            ToolResultMessage(tool_call_id="edit-1", tool_name="edit", content="changed a"),
            ToolResultMessage(tool_call_id="edit-2", tool_name="edit", content="changed b"),
        ]
    )

    assert len(state.items) == 1
    item = state.items[0]
    assert item.text == "→ Edited 2 files\n  - a.py\n  - b.py"
    assert item.grouped_tool_calls is not None
    assert item.tool_result_text == "✓ edit group"
    expanded = state.resolve_tool_invocation(item, expanded=True)
    assert expanded is not None
    assert "→ edit a.py\n\n✓ edit\nchanged a" in expanded
    assert "→ edit b.py\n\n✓ edit\nchanged b" in expanded


def test_tui_state_clusters_writes_and_lists_every_path() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(
                        id="write-1",
                        name="write",
                        arguments={"path": "a.py", "content": "one"},
                    ),
                    ToolCall(
                        id="write-2",
                        name="write",
                        arguments={"path": "b.py", "content": "two"},
                    ),
                ]
            ),
            ToolResultMessage(tool_call_id="write-1", tool_name="write", content="wrote a"),
            ToolResultMessage(tool_call_id="write-2", tool_name="write", content="wrote b"),
        ]
    )

    assert len(state.items) == 1
    item = state.items[0]
    assert item.text == "→ Written 2 files\n  - a.py\n  - b.py"
    assert item.grouped_tool_calls is not None
    assert item.tool_result_text == "✓ write group"
    expanded = state.resolve_tool_invocation(item, expanded=True)
    assert expanded is not None
    assert "→ write a.py\n\n✓ write\nwrote a" in expanded
    assert "→ write b.py\n\n✓ write\nwrote b" in expanded


def test_tui_state_restores_write_only_model_continuations_as_one_group() -> None:
    messages = []
    for index in range(1, 6):
        messages.extend(
            [
                AssistantMessage(
                    content=[
                        ToolCall(
                            id=f"write-{index}",
                            name="write",
                            arguments={"path": f"file-{index}.md", "content": str(index)},
                        )
                    ]
                ),
                ToolResultMessage(
                    tool_call_id=f"write-{index}",
                    tool_name="write",
                    content=f"wrote {index}",
                ),
            ]
        )
    state = TuiState()
    state.load_messages(messages)

    assert len(state.items) == 1
    item = state.items[0]
    assert item.text == (
        "→ Written 5 files\n"
        "  - file-1.md\n"
        "  - file-2.md\n"
        "  - file-3.md\n"
        "  - file-4.md\n"
        "  - file-5.md"
    )
    assert item.grouped_tool_calls is not None
    assert [member.tool_call_id for member in item.grouped_tool_calls] == [
        "write-1",
        "write-2",
        "write-3",
        "write-4",
        "write-5",
    ]
    assert all(state.find_tool_item(f"write-{index}") is item for index in range(1, 6))


def test_tui_state_restores_edit_only_model_continuations_as_one_group() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(
                        id="edit-1",
                        name="edit",
                        arguments={"path": "a.py", "edits": []},
                    )
                ]
            ),
            ToolResultMessage(tool_call_id="edit-1", tool_name="edit", content="edited a"),
            AssistantMessage(
                content=[
                    ToolCall(
                        id="edit-2",
                        name="edit",
                        arguments={"path": "b.py", "edits": []},
                    )
                ]
            ),
            ToolResultMessage(tool_call_id="edit-2", tool_name="edit", content="edited b"),
            AssistantMessage(
                content=[
                    ToolCall(
                        id="edit-3",
                        name="edit",
                        arguments={"path": "c.py", "edits": []},
                    )
                ]
            ),
            ToolResultMessage(tool_call_id="edit-3", tool_name="edit", content="edited c"),
        ]
    )

    assert len(state.items) == 1
    item = state.items[0]
    assert item.text == "→ Edited 3 files\n  - a.py\n  - b.py\n  - c.py"
    assert item.grouped_tool_calls is not None
    assert [member.tool_call_id for member in item.grouped_tool_calls] == [
        "edit-1",
        "edit-2",
        "edit-3",
    ]
    assert all(state.find_tool_item(f"edit-{index}") is item for index in range(1, 4))


def test_tui_state_keeps_write_continuations_separate_across_assistant_text() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(
                        id="write-1",
                        name="write",
                        arguments={"path": "a.md", "content": "one"},
                    )
                ]
            ),
            ToolResultMessage(tool_call_id="write-1", tool_name="write", content="wrote a"),
            AssistantMessage(
                content=[
                    TextContent(text="Now writing the next file."),
                    ToolCall(
                        id="write-2",
                        name="write",
                        arguments={"path": "b.md", "content": "two"},
                    ),
                ]
            ),
            ToolResultMessage(tool_call_id="write-2", tool_name="write", content="wrote b"),
        ]
    )

    assert len(state.items) == 3
    assert state.items[0].text == "→ write a.md"
    assert state.items[1].role == "assistant"
    assert state.items[2].text == "→ write b.md"
    assert all(item.grouped_tool_calls is None for item in (state.items[0], state.items[2]))


@pytest.mark.parametrize(
    ("boundary", "boundary_before_call"),
    [
        (TextContent(text="boundary text"), True),
        (TextContent(text="boundary text"), False),
        (ThinkingContent(thinking="boundary thinking"), True),
        (ThinkingContent(thinking="boundary thinking"), False),
    ],
)
def test_file_mutation_continuation_boundaries_match_live_and_restored(
    boundary: TextContent | ThinkingContent,
    boundary_before_call: bool,
) -> None:
    first_call = ToolCall(
        id="write-1",
        name="write",
        arguments={"path": "a.md", "content": "one"},
    )
    second_call = ToolCall(
        id="write-2",
        name="write",
        arguments={"path": "b.md", "content": "two"},
    )
    first_content = [boundary, first_call] if boundary_before_call else [first_call, boundary]
    messages = [
        AssistantMessage(content=first_content),
        ToolResultMessage(tool_call_id="write-1", tool_name="write", content="wrote a"),
        AssistantMessage(content=[second_call]),
        ToolResultMessage(tool_call_id="write-2", tool_name="write", content="wrote b"),
    ]

    restored = TuiState()
    restored.load_messages(messages)

    live = TuiState()
    adapter = TuiEventAdapter(live)
    adapter.apply(MessageEndEvent(message=messages[0]))
    adapter.apply(
        ToolExecutionStartEvent(
            tool_call_id="write-1",
            tool_name="write",
            args={"path": "a.md", "content": "one"},
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            tool_call_id="write-1",
            tool_name="write",
            result=AgentToolResult(content="wrote a"),
            is_error=False,
        )
    )
    adapter.apply(MessageEndEvent(message=messages[2]))
    adapter.apply(
        ToolExecutionStartEvent(
            tool_call_id="write-2",
            tool_name="write",
            args={"path": "b.md", "content": "two"},
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            tool_call_id="write-2",
            tool_name="write",
            result=AgentToolResult(content="wrote b"),
            is_error=False,
        )
    )

    for state in (restored, live):
        first_item = state.find_tool_item("write-1")
        second_item = state.find_tool_item("write-2")
        assert first_item is not None
        assert second_item is not None
        assert first_item is not second_item
        assert first_item.grouped_tool_calls is None
        assert second_item.grouped_tool_calls is None


def test_tui_state_batches_mixed_tools_and_clusters_adjacent_reads() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(
                        id="bash-1",
                        name="bash",
                        arguments={"command": "echo one", "description": "Doing thing one"},
                    ),
                    ToolCall(
                        id="bash-2",
                        name="bash",
                        arguments={"command": "echo two", "description": "Doing thing two"},
                    ),
                    ToolCall(id="read-1", name="read", arguments={"path": "a.py"}),
                    ToolCall(id="read-2", name="read", arguments={"path": "b.py"}),
                    ToolCall(
                        id="bash-3",
                        name="bash",
                        arguments={"command": "echo three", "description": "Doing thing three"},
                    ),
                ]
            ),
            ToolResultMessage(tool_call_id="bash-1", tool_name="bash", content="one"),
            ToolResultMessage(tool_call_id="bash-2", tool_name="bash", content="two"),
            ToolResultMessage(tool_call_id="read-1", tool_name="read", content="a"),
            ToolResultMessage(tool_call_id="read-2", tool_name="read", content="b"),
            ToolResultMessage(tool_call_id="bash-3", tool_name="bash", content="three"),
        ]
    )

    assert len(state.items) == 1
    item = state.items[0]
    assert item.tool_batch_items is not None
    assert len(item.tool_batch_items) == 4
    assert item.tool_batch_items[2].grouped_tool_calls is not None
    assert item.text.splitlines() == [
        "→ Doing thing one",
        "→ Doing thing two",
        "→ Read 2 files",
        "  - a.py",
        "  - b.py",
        "→ Doing thing three",
    ]
    assert all(
        state.find_tool_item(call_id) is item
        for call_id in (
            "bash-1",
            "bash-2",
            "read-1",
            "read-2",
            "bash-3",
        )
    )


def test_tui_state_keeps_custom_rendered_calls_out_of_tool_batches() -> None:
    state = TuiState(
        tool_call_renderer=lambda name, _arguments: (
            "[bold]agent card[/bold]" if name == "agent" else None
        )
    )
    batch_id = state.new_tool_batch_id()

    state.add_tool_call(
        ToolCall(id="custom", name="agent", arguments={"prompt": "explore"}),
        batch_id=batch_id,
    )
    state.add_tool_call(
        ToolCall(
            id="bash",
            name="bash",
            arguments={"command": "pwd", "description": "Checking location"},
        ),
        batch_id=batch_id,
    )

    assert len(state.items) == 2
    assert all(item.tool_batch_items is None for item in state.items)


def test_tui_state_keeps_result_only_extension_tools_out_of_batches() -> None:
    state = TuiState(
        tool_result_renderer=lambda name, _result, _expanded: f"[bold]{name} card[/bold]"
    )
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(id="custom-1", name="extension-tool", arguments={}),
                    ToolCall(id="custom-2", name="extension-tool", arguments={}),
                ]
            ),
            ToolResultMessage(tool_call_id="custom-1", tool_name="extension-tool", content="one"),
            ToolResultMessage(tool_call_id="custom-2", tool_name="extension-tool", content="two"),
        ]
    )

    assert len(state.items) == 2
    assert all(item.tool_batch_items is None for item in state.items)
    assert [state.resolve_tool_result(item, expanded=False) for item in state.items] == [
        "[bold]extension-tool card[/bold]",
        "[bold]extension-tool card[/bold]",
    ]


def test_tui_state_marks_group_failed_when_any_read_fails() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[
                    ToolCall(id="call-1", name="read", arguments={"path": "a.py"}),
                    ToolCall(id="call-2", name="read", arguments={"path": "missing.py"}),
                ]
            ),
            ToolResultMessage(tool_call_id="call-1", tool_name="read", content="one"),
            ToolResultMessage(
                tool_call_id="call-2",
                tool_name="read",
                content="not found",
                is_error=True,
            ),
        ]
    )

    item = state.items[0]
    assert item.text == "→ Read 2 files · 1 failed\n  - a.py\n  - missing.py"
    assert item.tool_result_text is not None
    assert item.tool_result_text.startswith("✗ read group")


def test_tui_adapter_does_not_group_reads_separated_by_assistant_text() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    adapter.apply(
        MessageEndEvent(
            message=AssistantMessage(
                content=[
                    ToolCall(id="call-1", name="read", arguments={"path": "a.py"}),
                    TextContent(text="then"),
                    ToolCall(id="call-2", name="read", arguments={"path": "b.py"}),
                ]
            )
        )
    )
    adapter.apply(
        ToolExecutionStartEvent(tool_call_id="call-1", tool_name="read", args={"path": "a.py"})
    )
    adapter.apply(
        ToolExecutionStartEvent(tool_call_id="call-2", tool_name="read", args={"path": "b.py"})
    )

    assert [item.text for item in state.items] == ["then", "→ read a.py", "→ read b.py"]


def test_tui_state_does_not_group_reads_from_different_assistant_messages() -> None:
    state = TuiState()
    state.load_messages(
        [
            AssistantMessage(
                content=[ToolCall(id="call-1", name="read", arguments={"path": "a.py"})]
            ),
            ToolResultMessage(tool_call_id="call-1", tool_name="read", content="one"),
            AssistantMessage(
                content=[ToolCall(id="call-2", name="read", arguments={"path": "b.py"})]
            ),
            ToolResultMessage(tool_call_id="call-2", tool_name="read", content="two"),
        ]
    )

    assert [item.text for item in state.items] == ["→ read a.py", "→ read b.py"]


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


def test_bash_tool_formatter_hides_short_command_until_expanded() -> None:
    command = 'rg -n "ToolCall" src'
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"command": command, "timeout": 10},
    )

    assert format_tool_call_block(call) == "→ Running shell command (timeout 10s)"
    assert format_tool_call_invocation(call, expanded=True) == f"$ {command} (timeout 10s)"


def test_bash_tool_formatter_hides_described_short_command_until_expanded() -> None:
    command = "rm -rf /tmp/x"
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"command": command, "description": "Listing files"},
    )

    assert format_tool_call_block(call) == "→ Listing files"
    assert format_tool_call_invocation(call, expanded=True) == f"$ {command}"


def test_bash_tool_formatter_hides_long_command_behind_description() -> None:
    command = "git diff --check && git commit -m 'Finish work' && " + "echo done " * 12
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={
            "command": command,
            "description": "  Validating and\ncommitting changes  ",
            "timeout": 120,
        },
    )

    collapsed = format_tool_call_block(call)
    assert collapsed == "→ Validating and committing changes (timeout 120s)"
    assert "$" not in collapsed
    assert "\n" not in collapsed
    assert format_tool_call_block(call, compact=False) == f"$ {command} (timeout 120s)"
    assert format_tool_call_invocation(call, expanded=True) == f"$ {command} (timeout 120s)"


def test_bash_tool_formatter_shows_complete_description_without_command_hint() -> None:
    command = "python - <<'PY'\nprint('hello')\nPY"
    description = "Describing a deliberately overlong inline script operation " * 2
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"command": command, "description": description},
    )

    collapsed = format_tool_call_block(call)
    assert collapsed == f"→ {description.strip()}"
    assert "$" not in collapsed
    assert "\n" not in collapsed


def test_bash_tool_formatter_never_previews_undescribed_commands() -> None:
    commands = [
        "python - <<'PY'\nprint('one')\nPY",
        "git status &&\ngit diff",
        "echo " + "x" * 200,
        'uv run python -c "print(1)"',
        "\n\n",
    ]

    for index, command in enumerate(commands):
        call = ToolCall(id=f"call-{index}", name="bash", arguments={"command": command})
        assert format_tool_call_block(call) == "→ Running shell command"
        assert format_tool_call_invocation(call, expanded=True) == f"$ {command}"


def test_tui_state_recovers_full_bash_command_when_tool_output_expands() -> None:
    command = "python - <<'PY'\nprint('hello')\nPY"
    state = TuiState(tool_call_renderer=lambda _name, _arguments: None)
    state.add_tool_call(ToolCall(id="call-1", name="bash", arguments={"command": command}))
    item = state.items[0]
    item.started_at = None

    assert item.text == "→ Running shell command"
    assert state.resolve_tool_invocation(item) is None
    assert state.resolve_tool_invocation(item, expanded=True) == (
        f"→ Running shell command\n$ {command}"
    )


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
