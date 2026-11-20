"""Cached file-reference index for prompt ``@`` completion."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from tau_coding.tui.autocomplete import FileReferenceEntry, iter_file_reference_entries

DEFAULT_FILE_INDEX_TTL_SECONDS = 2.0

RefreshEntries = Callable[[], tuple[FileReferenceEntry, ...]]
StartThread = Callable[[Callable[[], None]], None]


def _start_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="tau-file-index", daemon=True).start()


class FileReferenceIndex:
    """Stale-while-revalidate snapshot of workspace file-reference entries.

    The first ``snapshot`` builds synchronously so the first ``@`` still shows
    suggestions. Later snapshots return the cached entries immediately and
    rebuild in a daemon thread when the TTL expires or ``invalidate`` is called.
    ``on_refresh`` runs after a background rebuild so the TUI can redraw
    completions.
    """

    def __init__(
        self,
        cwd: Path,
        *,
        ttl: float = DEFAULT_FILE_INDEX_TTL_SECONDS,
        on_refresh: Callable[[], None] | None = None,
        refresh: RefreshEntries | None = None,
        start_thread: StartThread | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cwd = cwd.resolve()
        self._ttl = ttl
        self._on_refresh = on_refresh
        self._refresh_entries = refresh or self._default_refresh
        self._start_thread = start_thread or _start_daemon_thread
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: tuple[FileReferenceEntry, ...] = ()
        self._built_at: float | None = None
        self._built_once = False
        self._refreshing = False
        self._closed = False
        self._generation = 0
        self._refresh_finished = threading.Event()
        self._refresh_finished.set()

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._built_once

    def snapshot(self) -> tuple[FileReferenceEntry, ...]:
        """Return cached entries without waiting for a background refresh."""
        bootstrap = False
        with self._lock:
            if self._closed:
                return self._entries
            if not self._built_once:
                if self._refreshing:
                    return self._entries
                self._refreshing = True
                self._refresh_finished.clear()
                bootstrap = True
            else:
                stale = self._built_at is None or (self._clock() - self._built_at) >= self._ttl
                if not stale or self._refreshing:
                    return self._entries
                self._refreshing = True
                self._refresh_finished.clear()

        if bootstrap:
            self._refresh(notify=False)
        else:
            self._start_thread(self._refresh)
        with self._lock:
            return self._entries

    def refresh(self) -> None:
        """Schedule a background refresh if one is not already running."""
        with self._lock:
            if self._closed or self._refreshing:
                return
            self._refreshing = True
            self._refresh_finished.clear()
        self._start_thread(self._refresh)

    def invalidate(self) -> None:
        """Mark the cached snapshot stale so the next request rebuilds it."""
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            self._built_at = None

    def close(self) -> None:
        """Stop delivering refresh callbacks after the app shuts down."""
        with self._lock:
            self._closed = True
            self._on_refresh = None
            self._refresh_finished.set()

    def _default_refresh(self) -> tuple[FileReferenceEntry, ...]:
        return iter_file_reference_entries(self._cwd)

    def _refresh(self, *, notify: bool = True) -> None:
        callback: Callable[[], None] | None = None
        for _attempt in range(2):
            with self._lock:
                generation = self._generation
                if self._closed:
                    self._refreshing = False
                    self._refresh_finished.set()
                    return

            entries: tuple[FileReferenceEntry, ...] | None = None
            for retry in range(2):
                try:
                    entries = self._refresh_entries()
                except Exception:  # noqa: BLE001 - retry transient filesystem errors
                    if retry == 0:
                        time.sleep(0.05)
                        continue
                break

            with self._lock:
                if self._closed:
                    self._refreshing = False
                    self._refresh_finished.set()
                    return
                if generation != self._generation:
                    # The workspace changed while scanning; retry with the
                    # new generation instead of publishing stale results.
                    continue
                if entries is None:
                    # Keep the last good snapshot and stay stale so the next
                    # request retries instead of publishing an empty result.
                    self._built_once = True
                    self._built_at = None
                    self._refreshing = False
                    self._refresh_finished.set()
                    return
                self._entries = entries
                self._built_at = self._clock()
                self._built_once = True
                self._refreshing = False
                callback = self._on_refresh if notify else None
                self._refresh_finished.set()
                break
        else:
            # Repeated invalidation kept discarding scans. Release the slot so
            # a later request can retry, and let the UI rebuild.
            with self._lock:
                self._refreshing = False
                callback = self._on_refresh if notify else None
                self._refresh_finished.set()

        if callback is not None:
            with suppress(Exception):
                callback()
