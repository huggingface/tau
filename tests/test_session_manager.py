import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from tau_coding.paths import TauPaths
from tau_coding.session_manager import CodingSessionRecord, SessionManager


def test_session_manager_creates_and_lists_sessions(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()

    record = manager.create_session(
        cwd=cwd,
        model="fake",
        provider_name="huggingface",
        inference_provider="deepinfra",
        title="Test session",
    )

    assert record.provider_name == "huggingface"
    assert record.inference_provider == "deepinfra"
    assert record.path.parent.parent == tmp_path / ".tau" / "sessions"
    assert "project-" in record.path.parent.name
    assert len(record.path.parent.name.rsplit("-", maxsplit=1)[-1]) == 6
    assert (record.path.parent / "index.jsonl").exists()
    assert not (tmp_path / ".tau" / "sessions" / "index.jsonl").exists()
    assert record.path.name == f"{record.id}.jsonl"
    assert manager.get_session(record.id) == record
    assert manager.list_sessions() == [record]
    assert manager.list_sessions(cwd) == [record]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_session_manager_round_trips_unicode_line_separator_in_title(
    tmp_path: Path, separator: str
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()

    record = manager.create_session(cwd=cwd, model="fake", title=f"line one{separator}line two")

    assert manager.get_session(record.id) == record
    assert manager.list_sessions(cwd) == [record]


@pytest.mark.parametrize("session_id", ["", "-bad", "bad-", "bad id", "../escape"])
def test_session_manager_rejects_invalid_custom_session_id(tmp_path: Path, session_id: str) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))

    with pytest.raises(ValueError, match="Session id must be non-empty"):
        manager.prepare_session(cwd=tmp_path, model="fake", session_id=session_id)


@pytest.mark.parametrize("session_id", ["default", "Default", "index", "INDEX"])
def test_session_manager_rejects_reserved_transcript_names(tmp_path: Path, session_id: str) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))

    with pytest.raises(ValueError, match=f"Session id is reserved: {session_id}"):
        manager.prepare_session(cwd=tmp_path, model="fake", session_id=session_id)


def test_session_manager_rejects_dynamic_default_session_id(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    default_id = f"default-{manager.paths.project_session_dir(tmp_path).name}"

    with pytest.raises(ValueError, match=f"Session id is reserved: {default_id}"):
        manager.prepare_session(cwd=tmp_path, model="fake", session_id=default_id)


@pytest.mark.parametrize("session_id", ["CON", "nul.txt", "LPT9.worker"])
def test_session_manager_rejects_nonportable_file_names(tmp_path: Path, session_id: str) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))

    with pytest.raises(ValueError, match=f"Session id is not a portable file name: {session_id}"):
        manager.prepare_session(cwd=tmp_path, model="fake", session_id=session_id)


def test_session_manager_rejects_overlong_session_id(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))

    with pytest.raises(ValueError, match="Session id must be at most 128 bytes"):
        manager.prepare_session(cwd=tmp_path, model="fake", session_id="a" * 129)


def test_session_manager_exclusive_creation_rejects_orphaned_transcript(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    orphan = manager.prepare_session(cwd=tmp_path, model="fake", session_id="orphan")
    orphan.path.write_text("existing transcript\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Session already exists with id 'orphan'"):
        manager.create_session_exclusive(cwd=tmp_path, model="fake", session_id="orphan")

    assert orphan.path.read_text(encoding="utf-8") == "existing transcript\n"
    assert manager.get_session("orphan") is None


def test_session_manager_exclusive_creation_is_atomic(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    barrier = Barrier(2)

    def create() -> str:
        manager = SessionManager(paths)
        barrier.wait()
        return manager.create_session_exclusive(
            cwd=tmp_path,
            model="fake",
            session_id="shared-worker",
        ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _ in range(2)]
    outcomes: list[str] = []
    errors: list[Exception] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Exception as exc:
            errors.append(exc)

    assert outcomes == ["shared-worker"]
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "Session already exists with id 'shared-worker'"
    assert SessionManager(paths).get_session("shared-worker") is not None


def test_session_manager_exclusive_creation_rolls_back_reservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.prepare_session(cwd=tmp_path, model="fake", session_id="rollback-worker")

    index_session = manager.index_session

    def fail_after_index(record_to_index: CodingSessionRecord) -> None:
        index_session(record_to_index)
        raise OSError("index failed")

    monkeypatch.setattr(manager, "index_session", fail_after_index)

    with pytest.raises(OSError, match="index failed"):
        manager.create_session_exclusive(
            cwd=tmp_path,
            model="fake",
            session_id="rollback-worker",
        )

    assert not record.path.exists()
    assert manager.get_session(record.id) is None


def test_session_manager_prepares_unindexed_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()

    record = manager.prepare_session(cwd=cwd, model="fake", provider_name="fake-provider")

    assert record.provider_name == "fake-provider"
    assert record.path.name == f"{record.id}.jsonl"
    assert manager.get_session(record.id) is None
    assert manager.list_sessions(cwd) == []

    indexed = manager.index_session(record)

    assert indexed == record
    assert manager.get_session(record.id) == record
    assert manager.list_sessions(cwd) == [record]


def test_session_manager_filters_sessions_by_project_cwd(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()

    first = manager.create_session(cwd=first_cwd, model="fake", title="First")
    second = manager.create_session(cwd=second_cwd, model="fake", title="Second")

    assert manager.list_sessions(first_cwd) == [first]
    assert manager.list_sessions(second_cwd) == [second]
    assert {record.id for record in manager.list_sessions()} == {first.id, second.id}


def test_session_manager_returns_latest_session_for_cwd(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    older = manager.create_session(cwd=cwd, model="older", session_id="older")
    newer = manager.create_session(cwd=cwd, model="newer", session_id="newer")
    manager.touch_session(older.id)

    latest = manager.latest_session_for_cwd(cwd)

    assert latest is not None
    assert latest.id == older.id
    assert latest.model == "older"
    assert newer in manager.list_sessions(cwd)


def test_session_manager_ignores_extra_index_metadata(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    index_path = manager.project_index_path(cwd)
    session_path = index_path.parent / "session-1.jsonl"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "id": "session-1",
                "path": str(session_path),
                "cwd": str(cwd.resolve()),
                "model": "gpt-5",
                "title": "Session",
                "created_at": 1.0,
                "updated_at": 2.0,
                "provider_name": "openai-codex",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [record] = manager.list_sessions(cwd)

    assert record.id == "session-1"
    assert record.path == session_path
    assert record.model == "gpt-5"


def test_session_manager_gets_or_creates_default_session(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()

    first = manager.get_or_create_default_session(
        cwd=cwd, model="fake", provider_name="fake-provider"
    )
    second = manager.get_or_create_default_session(cwd=cwd, model="other")

    assert first == second
    assert first.provider_name == "fake-provider"
    assert first.id.startswith("default-")
    assert first.path.name == "default.jsonl"
    assert first.path.parent.exists()


def test_session_manager_touch_updates_metadata(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    record = manager.create_session(cwd=cwd, model="fake")

    updated = manager.touch_session(
        record.id,
        model="new-model",
        provider_name="new-provider",
        title="Updated",
    )

    assert updated is not None
    assert updated.id == record.id
    assert updated.model == "new-model"
    assert updated.provider_name == "new-provider"
    assert updated.title == "Updated"
    assert updated.updated_at >= record.updated_at
    assert manager.get_session(record.id) == updated


def test_session_manager_sorts_newest_updated_first(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    older = manager.create_session(cwd=cwd, model="fake", session_id="older")
    newer = manager.create_session(cwd=cwd, model="fake", session_id="newer")
    manager.touch_session(older.id)

    sessions = manager.list_sessions()

    assert [session.id for session in sessions] == ["older", "newer"]
    assert newer in sessions
