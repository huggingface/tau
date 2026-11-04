"""Conformance checks for third-party SessionStorage implementations.

`SessionStorage` is a small protocol (`append` / `read_all`), which makes it a
natural extension point: sessions can live in a database, an object store, or
any other durable backend instead of local JSONL files. This module gives
those implementations a way to verify they honor the same contract as
`JsonlSessionStorage`, so the rest of Tau (replay, branching, compaction,
export) keeps working unchanged.

Typical usage from an external package's test suite:

    from tau_agent.session import verify_session_storage

    async def test_my_storage_conforms(tmp_path):
        await verify_session_storage(
            lambda: MyStorage(tmp_path / "db", session_id="one"),
            reopen=lambda: MyStorage(tmp_path / "db", session_id="one"),
        )

The checks intentionally use only public session primitives and add no
dependencies.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import NoReturn

from tau_agent.messages import UserMessage
from tau_agent.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
)
from tau_agent.session.jsonl import entry_to_json_line
from tau_agent.session.storage import SessionStorage

type StorageFactory = Callable[[], SessionStorage | Awaitable[SessionStorage]]
"""A callable returning a SessionStorage (optionally awaitable for async setup)."""


class SessionStorageConformanceError(AssertionError):
    """Raised when a SessionStorage implementation violates the storage contract."""


def conformance_entries() -> list[SessionEntry]:
    """Return one linked session entry of every entry type.

    The entries form a valid parent chain and exercise the shapes storage
    backends most often get wrong: float timestamps, `None` fields, nested
    JSON data, and list-valued fields.
    """
    info = SessionInfoEntry(cwd="/tmp/conformance", title="Conformance session")
    model = ModelChangeEntry(parent_id=info.id, model="conformance-model")
    thinking = ThinkingLevelChangeEntry(parent_id=model.id, thinking_level=None)
    message = MessageEntry(
        parent_id=thinking.id,
        message=UserMessage(content='Hello, storage \u2014 with unicode and "quotes".'),
    )
    compaction = CompactionEntry(
        parent_id=message.id,
        summary="Summarized one greeting.",
        replaces_entry_ids=[message.id],
    )
    branch = BranchSummaryEntry(
        parent_id=compaction.id,
        summary="Abandoned branch summary.",
        branch_root_id=info.id,
    )
    label = LabelEntry(parent_id=branch.id, label="conformance")
    custom = CustomEntry(
        parent_id=label.id,
        namespace="tau_agent.conformance",
        data={"nested": {"ok": True, "values": [1, 2.5, None]}, "count": 3},
    )
    leaf = LeafEntry(parent_id=custom.id, entry_id=custom.id)
    return [info, model, thinking, message, compaction, branch, label, custom, leaf]


async def verify_session_storage(
    make_storage: StorageFactory,
    *,
    reopen: StorageFactory | None = None,
) -> None:
    """Verify a SessionStorage implementation against the storage contract.

    Checks, in order:

    1. A fresh storage reads as an empty session (missing == empty, no error).
    2. Appended entries come back from `read_all` in append order.
    3. Every entry type round-trips with full serialization fidelity
       (ids, parent ids, float timestamps, nested JSON, `None` fields).
    4. `read_all` is repeatable and does not mutate stored entries.
    5. With `reopen`: entries survive re-constructing the storage, and
       appending after a reopen preserves earlier entries (append-only).

    `make_storage` must return a **fresh, empty** storage. `reopen` should
    return a new storage instance backed by the same underlying data; pass it
    whenever the backend is durable so persistence is covered too.

    Raises `SessionStorageConformanceError` on the first violation.
    """
    storage = await _built(make_storage)

    initial = await storage.read_all()
    if initial != []:
        _fail(
            "a fresh storage must read as an empty session (a missing session "
            f"is an empty list, not an error); got {len(initial)} entries"
        )

    entries = conformance_entries()
    for entry in entries:
        await storage.append(entry)

    _check_entries_match(await storage.read_all(), entries, context="after appending")
    _check_entries_match(
        await storage.read_all(),
        entries,
        context="on a repeated read_all (reads must not mutate storage)",
    )

    if reopen is None:
        return

    reopened = await _built(reopen)
    _check_entries_match(
        await reopened.read_all(),
        entries,
        context="after reopening the storage (entries must be durable)",
    )

    extra = LabelEntry(parent_id=entries[-1].id, label="appended-after-reopen")
    await reopened.append(extra)
    _check_entries_match(
        await reopened.read_all(),
        [*entries, extra],
        context="after appending post-reopen (storage must stay append-only)",
    )


async def _built(factory: StorageFactory) -> SessionStorage:
    storage = factory()
    if inspect.isawaitable(storage):
        return await storage  # ty:ignore[invalid-return-type]
    return storage


def _fail(message: str) -> NoReturn:
    raise SessionStorageConformanceError(f"SessionStorage conformance failure: {message}")


def _canonical_line(entry: SessionEntry, *, context: str) -> str:
    try:
        return entry_to_json_line(entry)
    except Exception as error:  # noqa: BLE001 - surfaced as a conformance failure
        _fail(f"{context}: read_all returned a value that is not a valid SessionEntry: {error!r}")


def _check_entries_match(
    got: list[SessionEntry],
    expected: list[SessionEntry],
    *,
    context: str,
) -> None:
    if len(got) != len(expected):
        _fail(f"{context}: expected {len(expected)} entries, read_all returned {len(got)}")
    for index, (got_entry, expected_entry) in enumerate(zip(got, expected, strict=True)):
        got_line = _canonical_line(got_entry, context=context)
        expected_line = entry_to_json_line(expected_entry)
        if got_line != expected_line:
            _fail(
                f"{context}: entry {index} does not round-trip.\n"
                f"  expected: {expected_line.strip()}\n"
                f"  got:      {got_line.strip()}"
            )
