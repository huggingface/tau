# Drop persisted leaf session entries (#700)

Tau now follows Pi's file-order rule for session tips: the active tip is the
last non-`leaf` entry in JSONL order.

## Changes

- `CodingSession` no longer writes `LeafEntry` after messages, model or thinking
  changes, custom entries, history repairs, compactions, or tree navigation.
- Message persistence retries retain one stable `MessageEntry`. If append writes
  and then raises, retry reads durable ids and does not duplicate it.
- Replay ignores historical leaf pointers when selecting the tip. `LeafEntry`
  remains in the discriminated entry union so old files deserialize safely.
- Plain `/tree` navigation changes only the in-memory tip. A later write parents
  from that selection and makes the branch durable. Navigation with a branch
  summary writes the summary immediately, making it the file-order tip.
- HTML export derives the active path from the last non-leaf entry and can render
  historical leaf records without a special visibility filter.

## Compatibility and restart behavior

Old leaf records are data, not active-pointer commands. Even if a trailing leaf
points to an older branch, resume uses the last non-leaf entry in file order.
This intentionally changes one edge case: selecting an older entry with `/tree`
and quitting before any subsequent write does not preserve that selection.

## Validation

```bash
uv run pytest tests/test_session.py tests/test_session_export.py tests/test_coding_session.py -q
uv run pytest
```

Coverage includes linear and branched resume, stale historical leaf records,
model/thinking tips, in-memory navigation followed by branch writes, single-entry
retry after before/after-append failures, and export rendering.
