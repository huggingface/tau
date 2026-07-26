import json
from pathlib import Path

import pytest

from tau_agent import (
    AssistantMessage,
    CustomMessage,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionJsonlError,
    SessionState,
    SessionTreeError,
    entry_from_json_line,
    entry_to_json_line,
    path_to_entry,
)


def test_session_entry_round_trips_canonical_jsonl() -> None:
    entry = MessageEntry(
        id="entry-1",
        timestamp=1,
        message=UserMessage(content="Hello", timestamp=2),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"] == {
        "role": "user",
        "content": "Hello",
        "timestamp": 2,
    }


def test_custom_message_round_trips_with_pi_role_and_metadata() -> None:
    entry = MessageEntry(
        id="entry-1",
        message=CustomMessage(
            content="<task-notification/>",
            custom_type="subagent-notification",
            details={"id": "run-1"},
        ),
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    payload = json.loads(line)["message"]
    assert payload["role"] == "custom"
    assert payload["customType"] == "subagent-notification"
    assert parsed == entry


def test_assistant_and_tool_result_round_trip_canonical_blocks() -> None:
    assistant = MessageEntry(id="a", message=AssistantMessage(content="Hi"))
    result = MessageEntry(
        id="r",
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="edit",
            content="Successfully replaced 1 block.",
            details={"patch": "--- a.py\n+++ a.py"},
        ),
    )

    assistant_payload = json.loads(entry_to_json_line(assistant))["message"]
    result_payload = json.loads(entry_to_json_line(result))["message"]

    assert assistant_payload["content"][0]["text"] == "Hi"
    assert assistant_payload["usage"]["totalTokens"] == 0
    assert result_payload["role"] == "toolResult"
    assert result_payload["toolName"] == "edit"
    assert entry_from_json_line(entry_to_json_line(assistant)) == assistant
    assert entry_from_json_line(entry_to_json_line(result)) == result


def test_structured_thinking_message_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="a",
        message=AssistantMessage(
            content=[
                ThinkingContent(thinking="plan", thinking_signature="reasoning"),
                TextContent(text="done"),
            ]
        ),
    )

    parsed = entry_from_json_line(entry_to_json_line(entry))

    assert parsed == entry
    payload = json.loads(entry_to_json_line(entry))["message"]
    assert [block["type"] for block in payload["content"]] == ["thinking", "text"]
    assert payload["content"][0]["thinkingSignature"] == "reasoning"


def test_legacy_assistant_message_migrates_to_ordered_blocks() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Reading.",
                "tool_calls": [
                    {"id": "call-1", "name": "read", "arguments": {"path": "README.md"}}
                ],
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.text == "Reading."
    assert entry.message.tool_calls[0].name == "read"
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert "tool_calls" not in rewritten
    assert [block["type"] for block in rewritten["content"]] == ["text", "toolCall"]


def test_assistant_message_with_legacy_null_usage_cost_migrates() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Done.",
                "usage": {
                    "input": 10,
                    "output": 2,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total_tokens": 12,
                    "cost": None,
                },
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.usage.total_tokens == 12
    assert entry.message.usage.cost.total == 0.0
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["usage"]["cost"]["total"] == 0.0


def test_legacy_tool_message_migrates_and_preserves_data() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "tool",
            "timestamp": 1,
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "edit",
                "content": "changed",
                "ok": False,
                "error": "failed",
                "data": {"patch": "diff"},
                "details": {"line": 12},
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, ToolResultMessage)
    assert entry.message.role == "toolResult"
    assert entry.message.tool_name == "edit"
    assert entry.message.is_error is True
    assert entry.message.text == "changed"
    assert entry.message.details == {"patch": "diff", "line": 12}
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["role"] == "toolResult"
    assert not {"name", "ok", "error", "data", "tool_call_id"} & rewritten.keys()


def test_legacy_custom_user_message_migrates_to_custom_message() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "custom",
            "timestamp": 1,
            "message": {
                "role": "user",
                "content": "<task-notification/>",
                "custom_type": "subagent-notification",
                "details": {"id": "run-1"},
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, CustomMessage)
    assert entry.message.custom_type == "subagent-notification"
    assert json.loads(entry_to_json_line(entry))["message"]["role"] == "custom"


def test_invalid_jsonl_line_raises_useful_error() -> None:
    with pytest.raises(SessionJsonlError, match="Invalid session entry on line 3"):
        entry_from_json_line('{"type":"unknown"}', line_number=3)


@pytest.mark.anyio
async def test_jsonl_storage_appends_and_reads_entries(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "sessions" / "one.jsonl")
    first = MessageEntry(id="one", message=UserMessage(content="Hi"))
    second = LabelEntry(id="two", label="Greeting")

    await storage.append(first)
    await storage.append(second)

    assert await storage.read_all() == [first, second]


@pytest.mark.anyio
@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
async def test_jsonl_storage_round_trips_unicode_line_separators(
    tmp_path: Path, separator: str
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    entry = MessageEntry(id="one", message=UserMessage(content=f"before{separator}after"))

    await storage.append(entry)

    assert await storage.read_all() == [entry]


@pytest.mark.anyio
async def test_jsonl_storage_reads_existing_file_with_unicode_line_separator(
    tmp_path: Path,
) -> None:
    entry = MessageEntry(id="one", message=UserMessage(content="a\u2028b"))
    path = tmp_path / "session.jsonl"
    path.write_text(entry_to_json_line(entry), encoding="utf-8")
    storage = JsonlSessionStorage(path)

    assert await storage.read_all() == [entry]


def test_session_state_replays_linear_entries() -> None:
    user = UserMessage(content="Hi", timestamp=1)
    assistant = AssistantMessage(content="Hello", timestamp=2)
    entries = [
        MessageEntry(id="user", message=user),
        ModelChangeEntry(id="model", model="fake-model"),
        MessageEntry(id="assistant", message=assistant),
        LabelEntry(id="label", label="Greeting"),
        CustomEntry(id="custom", namespace="test", data={"ok": True}),
        LeafEntry(id="leaf", entry_id="assistant"),
    ]

    state = SessionState.from_entries(entries)

    assert state.messages == (user, assistant)
    assert state.model == "fake-model"
    assert state.label == "Greeting"
    assert state.active_leaf_id == "assistant"


def test_session_state_applies_compaction_and_branch_summary() -> None:
    entries = [
        MessageEntry(id="user", message=UserMessage(content="Explain sessions.")),
        MessageEntry(id="assistant", message=AssistantMessage(content="They are trees.")),
        CompactionEntry(
            id="compact",
            summary="The user asked about sessions.",
            replaces_entry_ids=["user", "assistant"],
        ),
        BranchSummaryEntry(id="branch", summary="A side branch explored storage."),
    ]

    state = SessionState.from_entries(entries)

    assert [message.role for message in state.messages] == ["user", "user"]
    assert "The user asked about sessions." in state.messages[0].text
    assert "A side branch explored storage." in state.messages[1].text


def test_path_to_entry_follows_parent_chain() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))
    child = MessageEntry(id="child", parent_id="root", message=AssistantMessage(content="Hello"))
    leaf = LeafEntry(id="leaf", parent_id="child", entry_id="child")

    assert [entry.id for entry in path_to_entry([root, child, leaf], "child")] == [
        "root",
        "child",
    ]


def test_path_to_entry_rejects_missing_or_cyclic_parent() -> None:
    with pytest.raises(SessionTreeError):
        path_to_entry([], "missing")

    first = CustomEntry(id="first", parent_id="second", namespace="x")
    second = CustomEntry(id="second", parent_id="first", namespace="x")
    with pytest.raises(SessionTreeError):
        path_to_entry([first, second], "first")
