"""Tests for pluggable session storage resolution in tau_coding."""

from pathlib import Path

import pytest

from tau_agent.session import JsonlSessionStorage, SessionEntry
from tau_coding.session_manager import CodingSessionRecord
from tau_coding.session_storage import (
    SESSION_STORAGE_ENV_VAR,
    SessionStorageResolutionError,
    create_session_storage,
    load_session_storage_factory,
    resolve_session_storage_factory,
)


class MemorySessionStorage:
    """Record-aware in-memory storage used as a custom-factory target."""

    def __init__(self, record: CodingSessionRecord) -> None:
        self.record = record
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


def memory_factory(record: CodingSessionRecord) -> MemorySessionStorage:
    """Module-level factory target for `module:attribute` resolution tests."""
    return MemorySessionStorage(record)


not_callable = "this is not a factory"


def _record(tmp_path: Path) -> CodingSessionRecord:
    return CodingSessionRecord(
        id="session-one",
        path=tmp_path / "session-one.jsonl",
        cwd=tmp_path,
        model="test-model",
        title=None,
        created_at=0.0,
        updated_at=0.0,
    )


def test_default_storage_is_jsonl_at_record_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SESSION_STORAGE_ENV_VAR, raising=False)
    record = _record(tmp_path)

    storage = create_session_storage(record)

    assert isinstance(storage, JsonlSessionStorage)
    assert storage.path == record.path


def test_explicit_factory_wins(tmp_path: Path) -> None:
    record = _record(tmp_path)

    storage = create_session_storage(record, memory_factory)

    assert isinstance(storage, MemorySessionStorage)
    assert storage.record is record


def test_env_var_selects_custom_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SESSION_STORAGE_ENV_VAR, f"{__name__}:memory_factory")
    record = _record(tmp_path)

    storage = create_session_storage(record)

    assert isinstance(storage, MemorySessionStorage)
    assert storage.record is record


def test_blank_env_var_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SESSION_STORAGE_ENV_VAR, "   ")

    assert resolve_session_storage_factory() is None


def test_unset_env_var_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SESSION_STORAGE_ENV_VAR, raising=False)

    assert resolve_session_storage_factory() is None


def test_env_mapping_override_is_supported() -> None:
    factory = resolve_session_storage_factory(
        {SESSION_STORAGE_ENV_VAR: f"{__name__}:memory_factory"}
    )

    assert factory is memory_factory


@pytest.mark.parametrize(
    "spec",
    [
        "missing-colon",
        ":only_attribute",
        "only.module:",
        "definitely_missing_module_xyz:factory",
        f"{__name__}:missing_attribute",
        f"{__name__}:not_callable",
    ],
)
def test_bad_specs_raise_pointed_errors(spec: str) -> None:
    with pytest.raises(SessionStorageResolutionError):
        load_session_storage_factory(spec)


def test_dotted_attribute_paths_resolve() -> None:
    factory = load_session_storage_factory(f"{__name__}:MemorySessionStorage")

    assert factory is MemorySessionStorage
