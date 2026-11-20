# Cached file-reference index for `@` completion

## What changed

`@` prompt completion used to walk the whole workspace on every prompt text
change. The walk collected every path before filtering, so typing `@` in a large
repository could block the Textual event loop for seconds per keystroke. The
walk also created a `Path` and called `relative_to()` for each entry.

The new flow has two layers:

1. `tau_coding.tui.autocomplete.scan_file_reference_entries()` walks with
   `os.scandir`, tracks relative paths as strings, pre-lowers each path for
   matching, and tracks ancestor inodes per traversal branch. Alias symlinks
   keep producing their own relative paths; cycles stop when a branch revisits
   an ancestor. It returns `FileReferenceEntry` records plus a completeness
   flag, so a missing root or unreadable nested directory does not masquerade
   as an authoritative empty result.
2. `tau_coding.tui.file_index.FileReferenceIndex` caches the snapshot per
   workspace. The first request builds synchronously so the first `@` still
   shows suggestions. Later requests return the cached snapshot immediately
   and rebuild in a daemon thread when the TTL expires or a tool invalidates
   the index. Failed or incomplete rebuilds keep the last good snapshot and
   stay stale for a retry; invalidations during a scan retry with the new
   generation. A refresh callback asks the TUI to redraw visible completions
   without losing the selected item. The callback follows the active `@`
   search rather than the previous result count, so an initially empty search
   appears when a tool creates the first matching file.

`build_completion_state()` accepts an optional `file_entries` sequence or a
callable that produces one. The TUI passes the index's `snapshot` method, so a
workspace scan only happens when an active workspace `@` token actually needs
entries; shell paths, `/model` arguments, and external `../` or `~/` references
never resolve it. Tests and other callers keep the pure on-demand walk.
Completion semantics are unchanged: substring matching, the 50-item cap,
directory ordering, ignored directories, external `../` completion, and
home-relative `~/` completion all behave as before. Home prefixes are
normalized before parent-path handling, and paths containing spaces are
inserted with backslash escapes so completion can continue inside them.

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
  symlink loops, alias preservation, incomplete scans, cached-entry filtering,
  escaped spaced paths, and home-relative normalization.
- `tests/test_tui_file_index.py` covers bootstrap, TTL refresh, forced refresh,
  invalidation, mid-flight invalidation, incomplete/failed scans, non-blocking
  stale reads, and close behavior.
- `tests/test_tui_app.py` covers refreshing an initially empty `@` search.
