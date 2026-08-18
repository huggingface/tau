# Automatic compaction always keeps the newest user turn

## What was added

`_first_recent_context_index` in `tau_coding.session` now clamps its compaction
boundary to the last user message, so automatic compaction can never drop the
newest user turn or its assistant reply.

## Why it exists

The function returns the index of the first row to **keep**; rows before it are
summarized and replaced. Its "keep whole user turns" logic moved the boundary
forward to the next user message, but nothing guaranteed that boundary stayed at
or before the **last** user message. That let compaction silently destroy the
most recent context:

- `[user, assistant, user, assistant]` returned `3`, keeping only the final
  `assistant` reply and dropping the newest user prompt.
- `[user, assistant, toolResult, toolResult]` returned `4`, compacting the entire
  context including the newest turn.

The root cause was conflating "keep recent tokens" with "keep whole user turns"
without a guard that the newest user turn is always retained.

## Fix

The function computes a single `boundary` value, then clamps it with
`min(boundary, last_user_index)` where `last_user_index` is the index of the last
`user` message (found by a new `_last_user_message_index` helper). When the
boundary clamps to `0`, the caller (`_recent_preserving_compaction_plan`) returns
`None` and skips compaction rather than compacting everything.

## How to test

```bash
uv run pytest tests/test_coding_session.py -k "first_recent_context_index or auto_compact or overflow"
```

The pure-function tests cover: keeping the newest user turn and reply, never
compacting past the last user message, keeping a pending (unanswered) newest user
prompt, and a normal recent-suffix case.
