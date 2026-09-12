"""Tests for the SessionStorage conformance checker."""

from pathlib import Path

import pytest

from tau_agent.session import (
    JsonlSessionStorage,
    LabelEntry,
    SessionEntry,
)
from tau_agent.session.conformance import (
    SessionStorageConformanceError,
    conformance_entries,
    verify_session_storage,
)


class MemorySessionStorage:
    """Minimal conforming in-memory storage (the docs example)."""

    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


class _DropsEntriesStorage(MemorySessionStorage):
    """Broken: silently drops every second append."""

    def __init__(self) -> None:
        super().__init__()
        self._seen = 0

    async def append(self, entry: SessionEntry) -> None:
        self._seen += 1
        if self._seen % 2 == 1:
            self.entries.append(entry)


class _ReordersStorage(MemorySessionStorage):
    """Broken: returns entries out of append order."""

    async def read_all(self) -> list[SessionEntry]:
        return list(reversed(self.entries))


class _TruncatesTimestampsStorage(MemorySessionStorage):
    """Broken: loses float precision, like a careless column type."""

    async def read_all(self) -> list[SessionEntry]:
        return [
            entry.model_copy(update={"timestamp": float(int(entry.timestamp))})
            for entry in self.entries
        ]


class _StaleStorage(MemorySessionStorage):
    """Broken: a \"fresh\" storage that already contains entries."""

    def __init__(self) -> None:
        super().__init__()
        self.entries.append(LabelEntry(label="stale"))


class _ForgetfulStorage(MemorySessionStorage):
    """Broken across reopens: nothing is durable (state dies with the instance)."""


def test_conformance_entries_cover_every_entry_type() -> None:
    types = {entry.type for entry in conformance_entries()}
    assert types == {
        "session_info",
        "model_change",
        "thinking_level_change",
        "message",
        "compaction",
        "branch_summary",
        "label",
        "leaf",
        "custom",
    }


def test_conformance_entries_form_a_parent_chain() -> None:
    entries = conformance_entries()
    for previous, entry in zip(entries, entries[1:], strict=False):
        assert entry.parent_id == previous.id


@pytest.mark.anyio
async def test_jsonl_storage_passes_conformance(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "conformance.jsonl"
    await verify_session_storage(
        lambda: JsonlSessionStorage(path),
        reopen=lambda: JsonlSessionStorage(path),
    )


@pytest.mark.anyio
async def test_memory_storage_passes_without_reopen() -> None:
    await verify_session_storage(MemorySessionStorage)


@pytest.mark.anyio
async def test_async_factories_are_supported(tmp_path: Path) -> None:
    async def make_storage() -> JsonlSessionStorage:
        return JsonlSessionStorage(tmp_path / "async.jsonl")

    await verify_session_storage(make_storage)


@pytest.mark.anyio
async def test_dropping_entries_fails() -> None:
    with pytest.raises(SessionStorageConformanceError, match="expected"):
        await verify_session_storage(_DropsEntriesStorage)


@pytest.mark.anyio
async def test_reordering_entries_fails() -> None:
    with pytest.raises(SessionStorageConformanceError, match="round-trip"):
        await verify_session_storage(_ReordersStorage)


@pytest.mark.anyio
async def test_losing_timestamp_precision_fails() -> None:
    with pytest.raises(SessionStorageConformanceError, match="round-trip"):
        await verify_session_storage(_TruncatesTimestampsStorage)


@pytest.mark.anyio
async def test_non_empty_fresh_storage_fails() -> None:
    with pytest.raises(SessionStorageConformanceError, match="empty session"):
        await verify_session_storage(_StaleStorage)


@pytest.mark.anyio
async def test_non_durable_storage_fails_reopen_check() -> None:
    with pytest.raises(SessionStorageConformanceError, match="durable"):
        await verify_session_storage(_ForgetfulStorage, reopen=_ForgetfulStorage)
