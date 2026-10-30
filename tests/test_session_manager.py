import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from tau_coding.paths import TauPaths
from tau_coding.session_manager import CodingSessionRecord, SessionManager, SessionRecordModel


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
    assert record.inference_provider_mode == "fixed"
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


def test_session_manager_treats_legacy_pinned_routes_as_fixed(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    index_path = manager.project_index_path(cwd)
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "id": "legacy-hf",
                "path": str(index_path.parent / "legacy-hf.jsonl"),
                "cwd": str(cwd.resolve()),
                "model": "moonshotai/Kimi-K3",
                "provider_name": "huggingface",
                "inference_provider": "deepinfra",
                "created_at": 1.0,
                "updated_at": 2.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [record] = manager.list_sessions(cwd)

    assert record.inference_provider == "deepinfra"
    assert record.inference_provider_mode == "fixed"


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


def test_session_manager_reuses_parsed_index_until_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    record = manager.create_session(cwd=cwd, model="fake", title="Cached")
    parses = 0
    original_validate = SessionRecordModel.model_validate_json

    def counting_validate(data: str | bytes) -> SessionRecordModel:
        nonlocal parses
        parses += 1
        return original_validate(data)

    monkeypatch.setattr(SessionRecordModel, "model_validate_json", counting_validate)

    for _ in range(3):
        cached = manager.get_session(record.id)
        assert cached is not None
        assert cached.title == "Cached"
    assert parses == 0

    index_path = manager.project_index_path(cwd)
    renamed = json.loads(index_path.read_text(encoding="utf-8"))
    renamed["title"] = "Renamed elsewhere"
    index_path.write_text(json.dumps(renamed) + "\n", encoding="utf-8")

    reread = manager.get_session(record.id)
    assert reread is not None
    assert reread.title == "Renamed elsewhere"
    assert parses == 1


def test_session_manager_writes_are_visible_to_other_managers(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    writer = SessionManager(paths)
    reader = SessionManager(paths)
    cwd = tmp_path / "project"
    cwd.mkdir()
    record = writer.create_session(cwd=cwd, model="fake")
    assert reader.get_session(record.id) == record

    # Rewrites in quick succession can share a timestamp and size; writers seed
    # the shared cache so readers never see the superseded records.
    for title in ("First", "Second", "Third"):
        writer.touch_session(record.id, title=title)
        current = reader.get_session(record.id)
        assert current is not None
        assert current.title == title


def test_session_manager_cwd_hint_skips_other_project_indexes(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    record = manager.create_session(cwd=project, model="fake", title="Hinted")
    manager.create_session(cwd=other, model="fake")
    manager.project_index_path(other).write_text("not json\n", encoding="utf-8")

    hinted = manager.get_session(record.id, cwd=project)

    assert hinted is not None
    assert hinted.title == "Hinted"
    with pytest.raises(ValueError):
        manager.get_session(record.id)
    with pytest.raises(ValueError):
        # A wrong hint falls back to scanning every index.
        manager.get_session(record.id, cwd=tmp_path)


def test_session_manager_archives_session_without_deleting_transcript(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    cwd.mkdir()
    record = manager.create_session(cwd=cwd, model="fake", session_id="archived-session")
    record.path.write_text("conversation\n", encoding="utf-8")

    assert manager.archive_session(record.id) is True
    assert manager.get_session(record.id) is None
    assert manager.list_sessions() == []
    assert manager.list_archived_sessions() == [record]
    assert record.path.read_text(encoding="utf-8") == "conversation\n"
    assert cwd.exists()
    assert manager.archive_session(record.id) is False
    assert manager.unarchive_session(record.id) is True
    assert manager.list_sessions() == [record]
    assert manager.list_archived_sessions() == []
    assert manager.unarchive_session(record.id) is False


def test_session_manager_archives_project_without_deleting_transcripts(tmp_path: Path) -> None:
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    cwd = tmp_path / "project"
    other_cwd = tmp_path / "other"
    cwd.mkdir()
    other_cwd.mkdir()
    first = manager.create_session(cwd=cwd, model="fake", session_id="first")
    second = manager.create_session(cwd=cwd, model="fake", session_id="second")
    other = manager.create_session(cwd=other_cwd, model="fake", session_id="other")
    for record in (first, second, other):
        record.path.write_text(record.id, encoding="utf-8")

    assert manager.archive_project(cwd) is True
    assert manager.list_sessions() == [other]
    assert {record.id for record in manager.list_archived_sessions()} == {"first", "second"}
    assert first.path.read_text(encoding="utf-8") == "first"
    assert second.path.read_text(encoding="utf-8") == "second"
    assert cwd.exists()
    assert manager.archive_project(cwd) is False
    assert manager.unarchive_project(cwd) is True
    assert {record.id for record in manager.list_sessions()} == {"first", "second", "other"}
    assert manager.list_archived_sessions() == []
    assert manager.unarchive_project(cwd) is False


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
