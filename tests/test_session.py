from pathlib import Path

import pytest

from tau_agent import AssistantMessage, ToolResultMessage, UserMessage
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


def test_session_entry_round_trips_jsonl() -> None:
    entry = MessageEntry(id="entry-1", message=UserMessage(content="Hello"))

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    assert parsed == entry


def test_plain_user_message_jsonl_line_omits_custom_metadata_keys() -> None:
    # Forward compat: a session that never uses custom messages must stay
    # byte-identical to the pre-metadata wire format, so old binaries
    # (extra="forbid") can still read new session files.
    entry = MessageEntry(id="entry-1", message=UserMessage(content="Hello"))

    line = entry_to_json_line(entry)

    assert '"custom_type"' not in line
    assert '"details"' not in line
    assert entry_from_json_line(line) == entry


def test_custom_user_message_jsonl_line_keeps_custom_metadata_keys() -> None:
    entry = MessageEntry(
        id="entry-1",
        message=UserMessage(
            content="<task-notification/>",
            custom_type="subagent-notification",
            details={"id": "run-1"},
        ),
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    assert '"custom_type":"subagent-notification"' in line
    assert '"details":{"id":"run-1"}' in line
    assert parsed == entry


def test_plain_assistant_message_jsonl_line_omits_usage_key() -> None:
    # Forward compat, same contract as the UserMessage custom-metadata test
    # above: virtually every session contains an assistant message, so a
    # "usage": null key in each one would defeat that guarantee for every
    # session, not just those using extensions.
    entry = MessageEntry(id="entry-1", message=AssistantMessage(content="Hi"))

    line = entry_to_json_line(entry)

    assert '"usage"' not in line
    assert entry_from_json_line(line) == entry


def test_assistant_message_with_usage_round_trips_jsonl() -> None:
    from tau_agent import Usage

    entry = MessageEntry(
        id="entry-1",
        message=AssistantMessage(
            content="Hi",
            usage=Usage(input=10, output=5, total_tokens=15),
        ),
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    assert '"usage"' in line
    assert parsed == entry
    assert isinstance(parsed, MessageEntry)
    assert isinstance(parsed.message, AssistantMessage)
    assert parsed.message.usage is not None
    assert parsed.message.usage.total_tokens == 15


def test_old_binary_assistant_message_model_accepts_new_plain_session_line() -> None:
    # Simulate an old tau binary's AssistantMessage: extra="forbid", no usage
    # field. It must accept a message serialized by the new code.
    import json
    from typing import Literal

    from pydantic import BaseModel, ConfigDict, Field

    from tau_agent import ToolCall

    class LegacyAssistantMessage(BaseModel):
        model_config = ConfigDict(extra="forbid")

        role: Literal["assistant"] = "assistant"
        content: str = ""
        tool_calls: list[ToolCall] = Field(default_factory=list)

    entry = MessageEntry(id="entry-1", message=AssistantMessage(content="Hi"))
    payload = json.loads(entry_to_json_line(entry))

    legacy = LegacyAssistantMessage.model_validate(payload["message"])

    assert legacy.content == "Hi"


def test_old_binary_user_message_model_accepts_new_plain_session_line() -> None:
    # Simulate an old tau binary's UserMessage: extra="forbid", no
    # custom_type/details fields. It must accept a message serialized by the
    # new code as long as no custom metadata was used.
    import json
    from typing import Literal

    from pydantic import BaseModel, ConfigDict

    class LegacyUserMessage(BaseModel):
        model_config = ConfigDict(extra="forbid")

        role: Literal["user"] = "user"
        content: str

    entry = MessageEntry(id="entry-1", message=UserMessage(content="Hello"))
    payload = json.loads(entry_to_json_line(entry))

    legacy = LegacyUserMessage.model_validate(payload["message"])

    assert legacy.content == "Hello"


def test_tool_result_message_metadata_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="entry-1",
        message=ToolResultMessage(
            tool_call_id="call-1",
            name="edit",
            content="Successfully replaced 1 block.",
            ok=True,
            data={"patch": "--- a.py\n+++ a.py\n@@\n-old\n+new"},
            details={"first_changed_line": 12},
        ),
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    assert parsed == entry


def test_compaction_entry_round_trips_jsonl() -> None:
    entry = CompactionEntry(
        id="compact",
        summary="The user asked about session replay.",
        replaces_entry_ids=["user", "assistant"],
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    assert parsed == entry


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
async def test_jsonl_storage_missing_file_is_empty(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "missing.jsonl")

    assert await storage.read_all() == []


def test_session_state_replays_linear_entries() -> None:
    entries = [
        MessageEntry(id="user", message=UserMessage(content="Hi")),
        ModelChangeEntry(id="model", model="fake-model"),
        MessageEntry(id="assistant", message=AssistantMessage(content="Hello")),
        LabelEntry(id="label", label="Greeting"),
        CustomEntry(id="custom", namespace="test", data={"ok": True}),
        LeafEntry(id="leaf", entry_id="assistant"),
    ]

    state = SessionState.from_entries(entries)

    assert state.messages == (UserMessage(content="Hi"), AssistantMessage(content="Hello"))
    assert state.model == "fake-model"
    assert state.label == "Greeting"
    assert state.active_leaf_id == "assistant"
    assert state.custom_entries == (entries[4],)
    assert state.context_entry_ids == ("user", "assistant")


def test_session_state_can_replay_explicit_empty_leaf() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))

    state = SessionState.from_entries([root], leaf_id=None)

    assert state.messages == ()
    assert state.active_leaf_id is None
    assert state.context_entry_ids == ()


def test_session_state_replays_compaction_as_context_summary() -> None:
    user = MessageEntry(id="user", message=UserMessage(content="Explain sessions."))
    assistant = MessageEntry(
        id="assistant",
        parent_id="user",
        message=AssistantMessage(content="Sessions are append-only."),
    )
    compaction = CompactionEntry(
        id="compact",
        parent_id="assistant",
        summary="The user asked about sessions. The assistant explained append-only replay.",
        replaces_entry_ids=["user", "assistant"],
    )
    followup = MessageEntry(
        id="followup",
        parent_id="compact",
        message=UserMessage(content="Continue."),
    )

    state = SessionState.from_entries([user, assistant, compaction, followup])

    assert state.messages == (
        UserMessage(
            content=(
                "Previous conversation summary:\n"
                "The user asked about sessions. The assistant explained append-only replay."
            )
        ),
        UserMessage(content="Continue."),
    )
    assert state.compaction_entries == (compaction,)
    assert state.context_entry_ids == ("compact", "followup")


def test_session_state_inserts_partial_compaction_before_retained_messages() -> None:
    old_user = MessageEntry(id="old-user", message=UserMessage(content="Old request"))
    old_assistant = MessageEntry(
        id="old-assistant",
        parent_id="old-user",
        message=AssistantMessage(content="Old answer"),
    )
    recent_user = MessageEntry(
        id="recent-user",
        parent_id="old-assistant",
        message=UserMessage(content="Recent request"),
    )
    recent_assistant = MessageEntry(
        id="recent-assistant",
        parent_id="recent-user",
        message=AssistantMessage(content="Recent answer"),
    )
    compaction = CompactionEntry(
        id="compact",
        parent_id="recent-assistant",
        summary="Older work was summarized.",
        replaces_entry_ids=["old-user", "old-assistant"],
    )

    state = SessionState.from_entries(
        [old_user, old_assistant, recent_user, recent_assistant, compaction]
    )

    assert state.messages == (
        UserMessage(content="Previous conversation summary:\nOlder work was summarized."),
        UserMessage(content="Recent request"),
        AssistantMessage(content="Recent answer"),
    )
    assert state.context_entry_ids == ("compact", "recent-user", "recent-assistant")


def test_session_state_replays_branch_summary_as_context_summary() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    summary = BranchSummaryEntry(
        id="branch-summary",
        parent_id="root",
        branch_root_id="root",
        summary="The abandoned branch explored an alternate implementation.",
    )

    state = SessionState.from_entries([root, summary], leaf_id="branch-summary")

    assert state.messages == (
        UserMessage(content="Root"),
        UserMessage(
            content=(
                "The following is a summary of a branch that this conversation came back from:\n"
                "<summary>\n"
                "The abandoned branch explored an alternate implementation.\n"
                "</summary>"
            )
        ),
    )
    assert state.context_entry_ids == ("root", "branch-summary")


def test_path_to_entry_returns_root_to_leaf_branch() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(id="right", parent_id="root", message=AssistantMessage(content="Right"))

    assert path_to_entry([root, left, right], "right") == [root, right]


def test_session_state_can_replay_one_branch() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    right = MessageEntry(id="right", parent_id="root", message=AssistantMessage(content="Right"))

    state = SessionState.from_entries([root, left, right], leaf_id="right")

    assert state.messages == (UserMessage(content="Hi"), AssistantMessage(content="Right"))
    assert state.active_leaf_id == "right"
    assert state.entries == (root, right)


def test_session_state_replays_compaction_on_active_branch() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Root"))
    left = MessageEntry(id="left", parent_id="root", message=AssistantMessage(content="Left"))
    compact = CompactionEntry(
        id="compact",
        parent_id="left",
        summary="Root and left branch summary.",
        replaces_entry_ids=["root", "left"],
    )
    right = MessageEntry(id="right", parent_id="root", message=AssistantMessage(content="Right"))

    state = SessionState.from_entries([root, left, compact, right], leaf_id="compact")

    assert state.messages == (
        UserMessage(content="Previous conversation summary:\nRoot and left branch summary."),
    )
    assert state.entries == (root, left, compact)


def test_path_to_entry_rejects_missing_parent() -> None:
    entry = MessageEntry(id="child", parent_id="missing", message=UserMessage(content="Hi"))

    with pytest.raises(SessionTreeError, match="Missing session entry"):
        path_to_entry([entry], "child")
