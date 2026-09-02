# Push-based session persistence

## What changed

`CodingSession` no longer persists messages from inside the loops that consume
harness events. It subscribes a persistence listener to the harness, and every
`message_end` notification writes that message to the session tree before the
event reaches the frontend. The count watermarks (`persisted_count = len(...)`
slicing in `prompt()`, `continue_()`, the overflow retry, and
`run_terminal_command`) are gone. All synthetic "Tool call interrupted by
user" repairs now flow through `message_start`/`message_end` events: the
run-start repair moved from `prompt()`/`continue_()` into `_run`, and the
cancelled-cleanup repair pushes to subscribers directly.

## Why it exists

Pressing Esc mid-tool-call makes the TUI cancel the worker that consumes
`session.prompt()`. With pull-side persistence, everything after the last
consumed event was silently dropped: the harness appended the synthetic
interrupted tool result in its `finally` block, but no consumer remained to
persist it, and the later count watermarks classified the orphan as already
persisted. Session files were left with `assistant(tool_use)` followed directly
by a user message. The fault stayed hidden — the in-memory repair protected the
live session and the `load()` repair protected restarts — until a replay that
skipped repair (`/tree`) sent the transcript to a provider, which rejected it
with a 400 (`tool_use` ids without `tool_result` blocks immediately after).

This is the shape Pi uses. In Pi, an aborted tool call's error result is
created inside the same loop iteration as the call
(`packages/agent/src/agent-loop.ts`), so adjacency is structural, never
repaired after the fact (though Pi breaks the batch on abort, so later calls
in a multi-tool message get no result — Tau's repair sweep covers that case).
Persistence lives in the harness's event handler (`handleAgentEvent` in
`packages/agent/src/harness/agent-harness.ts`), which
persists on every `message_end` through an ordered append queue — a
subscriber, not a consumer, so UI teardown cannot lose writes, and no count
watermark exists anywhere. Tau already emitted Pi-compatible events; this
change moves persistence to the same side of the event stream.

## Architecture

- `tau_agent.harness` stays portable: dangling-call repairs now run inside
  `_run` and flow through events — at run start as normal
  `message_start`/`message_end` after canonical `agent_start`/`turn_start`, and
  during cancelled cleanup as notify-only
  pushes wrapped in `suppress(Exception)` so a listener failure cannot mask
  the in-flight `CancelledError`.
- `tau_coding.session` owns persistence as a harness subscriber. The listener
  is attached first — before the extension event fan-out — in every path:
  construction, load (re-attached after the load-time repair rebuilds the
  harness), and `_adopt_replacement` on resume/`/new` (the replacement's own
  listener is detached so writes advance the outer session's parent pointers).
- `message_end` remains the durable-message boundary. A message whose
  `message_end` never fired is not persisted; an abandoned first prompt still
  leaves no durable trace and does not index the session.
- A reconcile backstop in the `finally` of `prompt()`/`continue_()` closes the
  run generator and retries only messages whose `message_end` fired but whose
  write failed. It is keyed on message identity, never counts: the loop emits
  an assistant's `message_end` before appending it to the transcript, so
  count-based sweeps can double-write. Each pending write retains stable
  message and leaf entry ids; a retry reads durable ids and appends only the
  missing pieces, so a failure between the two appends cannot duplicate the
  message. Only retries pay that read — a first attempt mints ids that cannot
  already be on disk, so the streaming path keeps one storage read per message. Repeated failures are logged without masking cancellation, retained,
  and flushed before the next prompt, continuation, compaction, or contextual
  terminal command.

Older files are repaired by the compatibility layer described in
`dev-notes/tool-history-recovery.md`. It validates active history on load and
`/tree`, writes a provider-safe append-only branch with a durable diagnostic,
and leaves the original entries intact. The agent loop applies the same repair
in memory as a final provider-request backstop.

## How to test

```bash
uv run pytest tests/test_agent_harness.py tests/test_coding_session.py tests/test_extensions.py
```

Key regression tests:

- `test_cancelled_prompt_teardown_persists_interrupted_tool_result` replays the
  TUI interrupt exactly (`session.cancel()` then worker cancel, no await
  between) and asserts the session file holds the synthetic result adjacent to
  its tool call.
- `test_cancelled_run_notifies_listeners_of_interrupted_tool_repair` and
  `test_listener_error_during_teardown_does_not_mask_cancellation` pin the
  harness contract; `test_entry_path_repair_is_pushed_to_listeners` also pins
  canonical run event ordering.
- `test_message_persistence_retry_is_idempotent` injects failures before and
  after the message and leaf appends, plus during refresh, and verifies retry
  leaves exactly one message and one leaf. The next-prompt test verifies a write
  that fails twice is retained and flushed before provider context is built.
- `test_session_resumes_indexed_session` asserts each message persists exactly
  once after resume (guards the listener detach in `_adopt_replacement`).
