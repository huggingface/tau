# Per-entry session labels

## What changed

Tau now follows Pi's label semantics: a `label` entry is an append-only bookmark
change for one existing session entry, not a second session name.

```json
{"type":"label","id":"...","parent_id":"...","target_id":"message-id","label":"checkpoint","timestamp":1740000000}
```

`target_id` identifies the bookmarked entry. `label` is optional; `null`, an
empty string, or whitespace clears the bookmark. `SessionState.labels_by_id` and
`label_timestamps_by_id` resolve changes in storage order, so relabel, clear, and
relabel sequences have deterministic last-change-wins behavior. A label's
resolved timestamp is the timestamp of the latest label entry, not its target.
Resolution scans the complete append-only tree rather than only the active path,
which keeps bookmarks available while navigating branches.

`CodingSession.set_label()` validates that the target exists before appending.
The extension API exposes the same operation as `await tau.set_label(...)`.
Labels never enter model context. Session naming remains exclusively
`SessionInfoEntry.title` and `/name`.

## TUI and export

In `/tree`, labeled branchable entries render a distinct `[label]` prefix:

- `L` creates or edits the selected label; submitting empty text clears it.
- `Ctrl+F` toggles labeled-only filtering.
- `Ctrl+L` toggles the latest label-change timestamps.
- Existing branching, summary, and tool-row controls continue to work.

The HTML export resolves labels over the full entry stream and prefixes the
corresponding tree nodes. Individual label-change rows show their target and
whether they set or cleared the bookmark.

## Backward-compatible migration

Old Tau label entries have no `target_id` because they named the whole session.
The issue suggested either rewriting that value into the session title or
attaching it to an early entry. Rewriting the title was rejected: it would mix
two concepts, could override a real `SessionInfoEntry.title`, and would require a
synthetic metadata mutation during a read.

Instead, JSONL loading deterministically targets every legacy label at the
file's earliest branchable entry (`message`, `compaction`, or
`branch_summary`). If there is no branchable entry, it uses the earliest
non-label/non-legacy-leaf entry; isolated single-line parsing falls back to the
legacy label's parent and then its own ID. New writes are always strict and
require `target_id`. This migration is read-only: the source file is not
rewritten, and exporting/download preserves a canonical in-memory projection.

Tool-history repair no longer snapshots a scalar session label onto its new
branch. Per-entry labels resolve globally and retain their stable target IDs, so
re-emitting them would create redundant change records and alter their
"bookmarked at" timestamps.

## Validation

Targeted coverage includes schema/JSONL migration, file-order resolution,
clear/relabel behavior and timestamps, unknown-target rejection, extension
routing, tree rendering/filter/edit/clear controls, active-row selection after a
label append, and HTML export rendering. Run the complete project checks with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
