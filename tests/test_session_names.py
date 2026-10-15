"""Session names survive transcript copies independently of the discovery index."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from tau_agent import UserMessage
from tau_agent.session import (
    InMemorySessionStorage,
    JsonlSessionStorage,
    MessageEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionState,
    entry_from_json_line,
    entry_to_json_line,
)
from tau_ai import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig, SessionManager, TauPaths
from tau_coding.rpc import _entry_wire
from tau_coding.session_export import export_session_jsonl, render_session_html


def session_config(tmp_path: Path) -> CodingSessionConfig:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(cwd=tmp_path, model="fake")
    return CodingSessionConfig(
        provider=FakeProvider([]),
        model="fake",
        system="You are Tau.",
        storage=JsonlSessionStorage(record.path),
        cwd=tmp_path,
        session_id=record.id,
        session_manager=manager,
    )


@pytest.mark.anyio
async def test_renames_append_without_rewriting_history_and_survive_copy(tmp_path: Path) -> None:
    config = session_config(tmp_path)
    session = await CodingSession.load(config)
    await session.set_session_name("First name")
    assert isinstance(config.storage, JsonlSessionStorage)
    original = config.storage.path.read_bytes()

    await session.set_session_name("Latest name")
    renamed = config.storage.path.read_bytes()
    assert renamed.startswith(original)
    assert len(renamed) > len(original)
    await session.set_session_name("Latest name")
    assert config.storage.path.read_bytes() == renamed

    copied = tmp_path / "copied.jsonl"
    copied.write_bytes(renamed)
    restored = await CodingSession.load(
        replace(config, storage=JsonlSessionStorage(copied), session_id=None, session_manager=None)
    )
    assert restored.session_name == "Latest name"
    assert restored.messages == session.messages


@pytest.mark.anyio
async def test_legacy_root_title_is_readable_without_index(tmp_path: Path) -> None:
    config = session_config(tmp_path)
    await config.storage.append(SessionInfoEntry(cwd=str(tmp_path), title="Legacy title"))
    session = await CodingSession.load(replace(config, session_id=None, session_manager=None))
    assert session.session_name == "Legacy title"


def test_name_is_global_in_file_order_and_does_not_replace_root_metadata() -> None:
    root = SessionInfoEntry(id="root", cwd="/project", created_at=1, title="Legacy")
    message = MessageEntry(id="message", parent_id=root.id, message=UserMessage(content="Hello"))
    first = SessionInfoEntry(id="first", parent_id=message.id, name="First", timestamp=20)
    latest = SessionInfoEntry(id="latest", parent_id=root.id, name="Latest", timestamp=10)
    entries = [root, message, first, latest]

    for leaf_id in (None, message.id, first.id, latest.id):
        state = SessionState.from_entries(entries, leaf_id=leaf_id)
        assert state.session_name == "Latest"
    state = SessionState.from_entries(entries, leaf_id=first.id)
    assert state.session_info == root
    assert state.messages == (message.message,)
    assert entry_from_json_line(entry_to_json_line(latest)) == latest
    assert _entry_wire(latest, "fake")["name"] == "Latest"
    assert _entry_wire(root, "fake")["name"] == "Legacy"


@pytest.mark.anyio
async def test_transcript_name_repairs_disagreeing_index_on_load(tmp_path: Path) -> None:
    config = session_config(tmp_path)
    assert config.session_manager is not None and config.session_id is not None
    await config.storage.append(SessionInfoEntry(name="Durable name"))
    config.session_manager.touch_session(config.session_id, title="Stale cache")
    before = await config.storage.read_all()

    session = await CodingSession.load(config)

    assert session.session_name == "Durable name"
    assert config.session_manager.list_sessions(tmp_path)[0].title == "Durable name"
    assert await config.storage.read_all() == before


@pytest.mark.anyio
async def test_legacy_index_rename_wins_over_root_until_explicitly_persisted(
    tmp_path: Path,
) -> None:
    config = session_config(tmp_path)
    assert config.session_manager is not None and config.session_id is not None
    root = SessionInfoEntry(title="Old root title")
    await config.storage.append(root)
    config.session_manager.touch_session(config.session_id, title="Index-only rename")

    session = await CodingSession.load(config)
    assert session.session_name == "Index-only rename"
    assert await config.storage.read_all() == [root]

    await session.set_session_name("Index-only rename")
    entries = await config.storage.read_all()
    assert entries[0] == root
    assert isinstance(entries[-1], SessionInfoEntry)
    assert entries[-1].name == "Index-only rename"
    restored = await CodingSession.load(replace(config, session_id=None, session_manager=None))
    assert restored.session_name == "Index-only rename"


@pytest.mark.anyio
async def test_prepared_session_does_not_repair_index_before_commit(tmp_path: Path) -> None:
    config = session_config(tmp_path)
    assert config.session_manager is not None and config.session_id is not None
    await config.storage.append(SessionInfoEntry(name="Durable name"))
    config.session_manager.touch_session(config.session_id, title="Stale cache")

    session = await CodingSession.load(replace(config, defer_authoritative_writes=True))
    assert session.session_name == "Durable name"
    assert config.session_manager.list_sessions(tmp_path)[0].title == "Stale cache"
    await session._commit_prepared_entries()
    assert config.session_manager.list_sessions(tmp_path)[0].title == "Durable name"


@pytest.mark.anyio
async def test_failed_name_append_keeps_old_name_and_index(tmp_path: Path) -> None:
    class FailingStorage(InMemorySessionStorage):
        async def append(self, entry: SessionEntry) -> None:
            raise OSError("Transcript unavailable")

    config = session_config(tmp_path)
    assert config.session_manager is not None and config.session_id is not None
    config.session_manager.touch_session(config.session_id, title="Old name")
    storage = FailingStorage([SessionInfoEntry(name="Old name")])
    session = await CodingSession.load(replace(config, storage=storage))

    with pytest.raises(OSError, match="Transcript unavailable"):
        await session.set_session_name("Unsaved name")

    assert session.session_name == "Old name"
    assert config.session_manager.list_sessions(tmp_path)[0].title == "Old name"
    assert len(await storage.read_all()) == 1


@pytest.mark.anyio
async def test_cache_write_failure_does_not_lose_durable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = session_config(tmp_path)
    assert config.session_manager is not None
    session = await CodingSession.load(config)

    def fail_cache_write(*args: object, **kwargs: object) -> None:
        raise OSError("Index unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(config.session_manager, "touch_session", fail_cache_write)
        assert await session.set_session_name("Durable name") == "Durable name"
        assert session.session_name == "Durable name"

    restored = await CodingSession.load(config)
    assert restored.session_name == "Durable name"
    assert config.session_manager.list_sessions(tmp_path)[0].title == "Durable name"


@pytest.mark.anyio
async def test_concurrent_standalone_renames_extend_the_same_branch(tmp_path: Path) -> None:
    storage = InMemorySessionStorage()
    config = replace(
        session_config(tmp_path), storage=storage, session_id=None, session_manager=None
    )
    session = await CodingSession.load(config)
    await asyncio.gather(session.set_session_name("First"), session.set_session_name("Second"))
    entries = await storage.read_all()
    names = [entry for entry in entries if isinstance(entry, SessionInfoEntry) and entry.name]
    assert [entry.name for entry in names] == ["First", "Second"]
    assert names[1].parent_id == names[0].id
    assert (await CodingSession.load(config)).session_name == "Second"


@pytest.mark.anyio
async def test_exports_preserve_the_name_without_an_index(tmp_path: Path) -> None:
    config = session_config(tmp_path)
    session = await CodingSession.load(config)
    await session.set_session_name("Fix <parser> & tests")
    entries = await session.session_entries()
    exported = export_session_jsonl(entries, tmp_path / "export.jsonl")
    restored = await CodingSession.load(
        replace(
            config, storage=JsonlSessionStorage(exported), session_id=None, session_manager=None
        )
    )
    assert restored.session_name == "Fix <parser> & tests"
    html = render_session_html(entries)
    assert "<title>Fix &lt;parser&gt; &amp; tests</title>" in html
    assert "<title>Explicit title</title>" in render_session_html(entries, title="Explicit title")
