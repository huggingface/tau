"""Locked, append-only session storage implementations."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO, Protocol

from tau_agent.session.entries import SessionEntry
from tau_agent.session.jsonl import entries_from_json_lines, entry_to_json_line


class SessionStorage(Protocol):
    """Append-only session storage interface.

    ``append_batch`` is the durable transaction boundary used by startup,
    replacement, and model selection. Implementations must make the complete
    batch visible or leave the previous transcript untouched.
    """

    async def append(self, entry: SessionEntry) -> None:
        """Append one entry to storage."""
        ...

    async def append_batch(self, entries: Sequence[SessionEntry]) -> None:
        """Atomically append a complete batch of entries."""
        ...

    async def read_all(self) -> list[SessionEntry]:
        """Read all entries in storage order."""
        ...


class JsonlSessionStorage:
    """Local JSONL storage with a per-session cross-process lock.

    The lock is deliberately separate from the transcript. Readers use a
    shared lock when the platform provides one; every write uses an exclusive
    lock and re-reads the current file before changing it. Batch writes use a
    same-directory temporary file, fsync, replace, and directory fsync.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.temp_path = self.path.with_name(f".{self.path.name}.tmp")

    async def append(self, entry: SessionEntry) -> None:
        """Append one entry under the session's exclusive cross-process lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked(exclusive=True):
            self._remove_incomplete_temp()
            with self.path.open("ab") as file:
                file.write(entry_to_json_line(entry).encode("utf-8"))
                file.flush()
                os.fsync(file.fileno())

    async def append_batch(self, entries: Sequence[SessionEntry]) -> None:
        """Atomically append all entries, preserving the old file on failure."""
        batch = tuple(entries)
        if not batch:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = b"".join(entry_to_json_line(entry).encode("utf-8") for entry in batch)
        with self._locked(exclusive=True):
            self._remove_incomplete_temp()
            previous = self.path.read_bytes() if self.path.exists() else b""
            data = previous + encoded
            self._atomic_replace(data)

    async def read_all(self) -> list[SessionEntry]:
        """Read all entries in file order; missing files are empty sessions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A fixed temp name makes crash recovery deterministic. If it exists,
        # take an exclusive lock and discard it; it was never the commit file.
        if self.temp_path.exists():
            with self._locked(exclusive=True):
                self._remove_incomplete_temp()
                return self._read_unlocked()
        with self._locked(exclusive=False):
            return self._read_unlocked()

    def _read_unlocked(self) -> list[SessionEntry]:
        if not self.path.exists():
            return []
        return entries_from_json_lines(self.path.read_text(encoding="utf-8").split("\n"))

    def _atomic_replace(self, data: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        # Keep the stable recovery marker in addition to the unique temp. A
        # crash between writes leaves a safely removable artifact.
        try:
            os.close(descriptor)
            temporary_path = Path(temporary)
            with temporary_path.open("wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self.path)
            _fsync_directory(self.path.parent)
        except BaseException:
            with _suppress_os_error():
                Path(temporary).unlink()
            raise

    def _remove_incomplete_temp(self) -> None:
        with _suppress_os_error():
            self.temp_path.unlink()
        # Also clean unique temp files left by a process killed during a
        # replacement. They are never authoritative because replace is atomic.
        prefix = f".{self.path.name}."
        for candidate in self.path.parent.glob(f"{prefix}*.tmp"):
            with _suppress_os_error():
                candidate.unlink()

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_file:
            os.chmod(self.lock_path, 0o600)
            _lock_file(lock_file, exclusive=exclusive)
            try:
                yield
            finally:
                _unlock_file(lock_file)


class InMemorySessionStorage:
    """Deterministic storage useful for tests and embedded frontends."""

    def __init__(self, entries: Sequence[SessionEntry] = ()) -> None:
        self.entries = list(entries)
        self._lock = asyncio.Lock()

    async def append(self, entry: SessionEntry) -> None:
        async with self._lock:
            self.entries.append(entry)

    async def append_batch(self, entries: Sequence[SessionEntry]) -> None:
        async with self._lock:
            self.entries.extend(entries)

    async def read_all(self) -> list[SessionEntry]:
        async with self._lock:
            return list(self.entries)


@contextmanager
def _suppress_os_error() -> Iterator[None]:
    with suppress(OSError):
        yield


def _lock_file(file: BinaryIO, *, exclusive: bool) -> None:
    """Apply an advisory lock, with a clear unsupported-platform fallback."""
    if os.name == "nt":
        import msvcrt

        # msvcrt has no shared lock; an exclusive lock is the safe behavior for
        # reads on Windows and still provides cross-process serialization.
        del exclusive
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fcntl.flock(file.fileno(), mode)


def _unlock_file(file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    """Persist the directory entry where the OS supports directory fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["InMemorySessionStorage", "JsonlSessionStorage", "SessionStorage"]
