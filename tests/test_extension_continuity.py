"""Real session lifecycle tests for awaited extension continuity work."""

import asyncio
from pathlib import Path

import pytest

from pi_event_helpers import assistant_done
from tau_agent.messages import AssistantMessage, UserMessage
from tau_agent.session import JsonlSessionStorage, MessageEntry
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, TauResourcePaths
from tau_coding.events import CompactionEndEvent, CompactionStartEvent
from tau_coding.extensions import ExtensionContext

pytestmark = pytest.mark.anyio


async def session_with_history(tmp_path: Path) -> CodingSession:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    old = MessageEntry(message=UserMessage(content="old " * 25000))
    recent = MessageEntry(parent_id=old.id, message=UserMessage(content="recent " * 15000))
    await storage.append(old)
    await storage.append(recent)
    return await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([[assistant_done(AssistantMessage(content="summary"))]]),
            model="fake",
            system="test",
            storage=storage,
            cwd=tmp_path,
            resource_paths=TauResourcePaths(root=tmp_path / "tau"),
            extensions_enabled=False,
            auto_compact_token_threshold=1,
        )
    )


@pytest.mark.parametrize("kind", ["manual", "detailed", "threshold", "overflow"])
async def test_compaction_awaits_before_replacement(tmp_path: Path, kind: str) -> None:
    session = await session_with_history(tmp_path)
    original = session.messages
    events: list[object] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def observe(event: object) -> None:
        events.append(event)
        if isinstance(event, CompactionStartEvent):
            assert session.messages == original
            entered.set()
            await release.wait()
            await session.append_custom_entry("checkpoint", {"saved": True})
        elif isinstance(event, CompactionEndEvent):
            assert not event.aborted
            assert session.messages != original

    session.extension_runtime.emit_event = observe
    if kind == "manual":
        pending = asyncio.create_task(session.compact())
    elif kind == "detailed":
        pending = asyncio.create_task(session.compact_detailed())
    elif kind == "threshold":
        pending = asyncio.create_task(session._maybe_auto_compact())
    else:
        pending = asyncio.create_task(
            session._try_overflow_compact(context=session._diagnostic_context())
        )
    await asyncio.wait_for(entered.wait(), 2)
    assert session.messages == original
    assert not pending.done()
    release.set()
    await pending
    assert [e.type for e in events] == ["compaction_start", "compaction_end"]
    assert any(e.type == "custom" for e in session.active_branch_entries)
    await session.aclose()


async def test_noop_and_cancelled_compaction(tmp_path: Path) -> None:
    session = await session_with_history(tmp_path)
    events: list[object] = []

    async def observe(event: object) -> None:
        events.append(event)
        if isinstance(event, CompactionStartEvent):
            raise asyncio.CancelledError

    session.extension_runtime.emit_event = observe
    session.set_auto_compaction_enabled(False)
    assert not await session._maybe_auto_compact()
    assert not events
    original = session.messages
    with pytest.raises(asyncio.CancelledError):
        await session.compact()
    assert session.messages == original
    assert isinstance(events[-1], CompactionEndEvent)
    assert events[-1].aborted
    await session.aclose()


async def test_context_summary_does_not_mutate_transcript(tmp_path: Path) -> None:
    session = await session_with_history(tmp_path)
    context = ExtensionContext(session.extension_runtime)
    before = session.messages
    assert (
        await context.summarize([UserMessage(content="public")], instructions="summarize")
        == "summary"
    )
    assert session.messages == before
    entries = context.branch_entries
    assert isinstance(entries[0], MessageEntry)
    entries[0].message = UserMessage(content="changed")
    assert session.active_branch_entries[0] != entries[0]
    with pytest.raises(ValueError, match="timeout"):
        await context.summarize([], instructions="", timeout=0)
    await session.aclose()
