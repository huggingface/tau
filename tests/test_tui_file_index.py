import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from tau_coding.tui.autocomplete import FileReferenceEntry
from tau_coding.tui.file_index import FileReferenceIndex


def _entry(relative: str, *, is_dir: bool = False) -> FileReferenceEntry:
    return FileReferenceEntry(relative=relative, search=relative.lower(), is_dir=is_dir)


def _run_inline(target: Callable[[], None]) -> None:
    target()


def test_file_index_bootstraps_synchronously(tmp_path: Path) -> None:
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        return (_entry("src/app.py"),)

    index = FileReferenceIndex(tmp_path, refresh=refresh, start_thread=_run_inline)

    assert index.snapshot() == (_entry("src/app.py"),)
    assert calls == 1
    assert index.is_ready
    index.close()


def test_file_index_refreshes_after_ttl(tmp_path: Path) -> None:
    now = [0.0]
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        return (_entry(f"file{calls}.py"),)

    index = FileReferenceIndex(
        tmp_path,
        ttl=2.0,
        refresh=refresh,
        start_thread=_run_inline,
        clock=lambda: now[0],
    )

    assert index.snapshot() == (_entry("file1.py"),)
    assert index.snapshot() == (_entry("file1.py"),)
    assert calls == 1

    now[0] = 2.0
    assert index.snapshot() == (_entry("file2.py"),)
    assert calls == 2
    index.close()


def test_file_index_invalidate_forces_refresh(tmp_path: Path) -> None:
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        return (_entry(f"file{calls}.py"),)

    index = FileReferenceIndex(tmp_path, refresh=refresh, start_thread=_run_inline)
    assert index.snapshot() == (_entry("file1.py"),)

    index.invalidate()

    assert index.snapshot() == (_entry("file2.py"),)
    assert calls == 2
    index.close()


def test_file_index_survives_refresh_errors(tmp_path: Path) -> None:
    def refresh() -> tuple[FileReferenceEntry, ...]:
        raise OSError("permission denied")

    index = FileReferenceIndex(tmp_path, refresh=refresh, start_thread=_run_inline)

    assert index.snapshot() == ()
    assert index.is_ready
    index.close()


def test_file_index_does_not_block_on_stale_refresh(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    refreshed = threading.Event()
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (_entry("first.py"),)
        started.set()
        release.wait(2)
        return (_entry("second.py"),)

    now = [0.0]
    index = FileReferenceIndex(
        tmp_path,
        ttl=1.0,
        refresh=refresh,
        on_refresh=refreshed.set,
        clock=lambda: now[0],
    )

    assert index.snapshot() == (_entry("first.py"),)
    now[0] = 2.0
    assert index.snapshot() == (_entry("first.py"),)
    assert started.wait(2)

    release.set()
    assert refreshed.wait(2)
    assert index.snapshot() == (_entry("second.py"),)
    index.close()


def test_file_index_refresh_forces_scan(tmp_path: Path) -> None:
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        return (_entry(f"file{calls}.py"),)

    index = FileReferenceIndex(
        tmp_path,
        ttl=100.0,
        refresh=refresh,
        start_thread=_run_inline,
    )
    assert index.snapshot() == (_entry("file1.py"),)

    index.refresh()

    assert index.snapshot() == (_entry("file2.py"),)
    assert calls == 2
    index.close()


def test_file_index_preserves_last_snapshot_on_refresh_failure(tmp_path: Path) -> None:
    now = [0.0]
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (_entry("good.py"),)
        raise OSError("transient")

    index = FileReferenceIndex(
        tmp_path,
        ttl=1.0,
        refresh=refresh,
        start_thread=_run_inline,
        clock=lambda: now[0],
    )

    assert index.snapshot() == (_entry("good.py"),)
    now[0] = 2.0
    assert index.snapshot() == (_entry("good.py"),)
    assert calls == 3

    # The failed refresh leaves the cache stale so the next request retries.
    assert index.snapshot() == (_entry("good.py"),)
    assert calls == 5
    index.close()


def test_file_index_preserves_snapshot_on_incomplete_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    calls = 0

    def fake_scan(root: Path) -> tuple[tuple[FileReferenceEntry, ...], bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (_entry("src/app.py"),), True
        return (_entry("other.py"),), False

    monkeypatch.setattr("tau_coding.tui.file_index.scan_file_reference_entries", fake_scan)
    index = FileReferenceIndex(
        tmp_path,
        ttl=1.0,
        start_thread=_run_inline,
        clock=lambda: now[0],
    )

    assert index.snapshot() == (_entry("src/app.py"),)
    now[0] = 2.0
    assert index.snapshot() == (_entry("src/app.py"),)
    index.close()


def test_file_index_retries_scan_invalidated_mid_flight(tmp_path: Path) -> None:
    now = [0.0]
    calls = 0

    def refresh() -> tuple[FileReferenceEntry, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (_entry("first.py"),)
        if calls == 2:
            index.invalidate()
            return (_entry("discarded.py"),)
        return (_entry("final.py"),)

    index = FileReferenceIndex(
        tmp_path,
        ttl=1.0,
        refresh=refresh,
        start_thread=_run_inline,
        clock=lambda: now[0],
    )

    assert index.snapshot() == (_entry("first.py"),)
    now[0] = 2.0
    assert index.snapshot() == (_entry("final.py"),)
    assert calls == 3
    index.close()


def test_file_index_close_suppresses_callbacks(tmp_path: Path) -> None:
    refreshed = threading.Event()

    def refresh() -> tuple[FileReferenceEntry, ...]:
        return (_entry("file.py"),)

    index = FileReferenceIndex(
        tmp_path,
        refresh=refresh,
        on_refresh=refreshed.set,
        start_thread=_run_inline,
    )
    index.close()

    assert index.snapshot() == ()
    assert not refreshed.is_set()
