# Pi-Compatible First-Kept Compaction

## What changed

Tau compactions now persist `first_kept_entry_id`, the id of the first active-context
message retained after the summarized prefix. Fresh JSONL records omit the empty legacy
`replaces_entry_ids` field. The persisted cost is therefore constant instead of growing by
one id for every summarized entry.

`CompactionPlan` carries the boundary and a transient replaced-entry count. The count keeps
manual status text accurate without storing all replaced ids. Manual, threshold, and
overflow compaction all use the same recent-preserving plan, and a compaction is not written
unless it has a real retained boundary.

## Replay semantics

Replay follows the active root-to-leaf path. At a modern compaction it produces:

```text
[summary] + [first kept message ... messages before compaction] + [successor messages]
```

The boundary is inclusive. If a hand-edited or boundary-less entry has no resolvable kept
id, the pre-compaction result is only the summary; normal path replay still adds successors
written after that compaction. This matches Pi's historical `firstKeptEntryId` behavior.

Tau applies compactions while walking the selected path, so branches not on that path never
influence the boundary lookup. A boundary at the first, middle, or final active message is
covered deterministically.

## Legacy compatibility

Older Tau versions persisted `replaces_entry_ids`, including records that also have a
`first_kept_entry_id`. A non-empty legacy list deliberately takes precedence. That preserves
the old set-based behavior, including arbitrary non-prefix replacement sets and summaries
inserted at the first replaced position. The field remains accepted by the strict Pydantic
discriminated union and is serialized only when non-empty.

RPC session inspection no longer emits `replacesEntryIds` or
`details.tauReplacedEntryIds`. Modern entries map directly to Pi's compaction shape. Legacy
entries without the complete Pi boundary/token pair remain `tau.compaction` custom records
with their summary, while local replay continues to honor the private compatibility field.
HTML export displays the first-kept boundary rather than the replaced-id list; raw JSONL
exports retain legacy data.

## Why this belongs at these layers

- `tau_agent` owns entry compatibility and deterministic replay.
- `tau_coding` owns compaction planning, status text, RPC projection, and HTML presentation.

This keeps the portable harness independent of CLI and rendering policy while preserving
Tau's append-only session history.

## Validation

Focused coverage lives in `tests/test_session.py`, `tests/test_coding_session.py`,
`tests/test_rpc.py`, and `tests/test_session_export.py`. The legacy fixture is
`tests/fixtures/legacy_compaction.jsonl`.

Run the CI-equivalent suite with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
