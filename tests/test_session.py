from pathlib import Path

import pytest

from tau_agent import AssistantMessage, UserMessage
from tau_agent.session import (
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


def test_path_to_entry_rejects_missing_parent() -> None:
    entry = MessageEntry(id="child", parent_id="missing", message=UserMessage(content="Hi"))

    with pytest.raises(SessionTreeError, match="Missing session entry"):
        path_to_entry([entry], "child")
