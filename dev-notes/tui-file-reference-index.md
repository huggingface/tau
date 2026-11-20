# Cached file-reference index for `@` completion

## What changed

`@` prompt completion used to walk the whole workspace on every prompt text
change. The walk collected every path before filtering, so typing `@` in a large
repository could block the Textual event loop for seconds per keystroke. The
walk also created a `Path` and called `relative_to()` for each entry.

The new flow has two layers:

1. `tau_coding.tui.autocomplete.iter_file_reference_entries()` walks with
   `os.scandir`, tracks relative paths as strings, pre-lowers each path for
   matching, and detects directory inode cycles. It returns
   `FileReferenceEntry` records instead of `Path` objects.
2. `tau_coding.tui.file_index.FileReferenceIndex` caches the snapshot per
   workspace. The first request builds synchronously so the first `@` still
   shows suggestions. Later requests return the cached snapshot immediately
   and rebuild in a daemon thread when the TTL expires or a tool invalidates
   the index. A refresh callback asks the TUI to redraw visible completions.

`build_completion_state()` accepts an optional `file_entries` sequence. The TUI
passes the cached snapshot; tests and other callers keep the pure on-demand
walk. Completion semantics are unchanged: substring matching, the 50-item cap,
directory ordering, ignored directories, and external `../` completion all
behave as before.

## Why it exists

The agent loop and the TUI must stay responsive while the user types. A full
filesystem walk is I/O work that belongs off the event loop and should not be
repeated when the prompt text changes by one character.

## Benchmarks

Measured on a synthetic tree with 100,100 entries:

| Operation | Time |
| --- | --- |
| Old `Path.iterdir` walk | ~1.77 s |
| New `os.scandir` walk | ~0.096 s |
| Cached in-memory filter | ~2 ms |

## Testing

- `tests/test_tui_autocomplete.py` covers walker ordering, ignored directories,
  symlink loops, and cached-entry filtering.
- `tests/test_tui_file_index.py` covers bootstrap, TTL refresh, invalidation,
  refresh errors, non-blocking stale reads, and close behavior.
- Existing TUI app tests continue to exercise the real `@` completion path.
