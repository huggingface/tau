# Threshold compaction events

## What changed

Automatic threshold compaction used to be invisible. The session compacted
between turns, but emitted no events, so the TUI had nothing to render: the
assistant's answer appeared, and then the spinner kept turning for several
seconds with no explanation. Issue #506.

The session now emits `CompactionStartEvent(reason="threshold")` and
`CompactionEndEvent(reason="threshold", ...)` around every automatic threshold
compaction, and the TUI renders a status row for it.

| Layer | Before | After |
|---|---|---|
| `CodingSession` | `_maybe_auto_compact()` did the check and the work, silently | `_auto_compaction_plan()` is a pure check; `_run_compaction_plan(plan)` does the work; `_try_auto_compact()` is an async generator that emits start/end around it |
| `TuiState` | No compaction flag | `compacting: bool` |
| `TuiAdapter` | Ignored threshold events | Adds `… Compacting context`, removes it on success, rewrites it to `… Automatic compaction failed: <error>` on failure |
| `TuiApp` | `_is_working()` ignored compaction | `_is_working()` includes `state.compacting`; threshold end triggers a full refresh |

## Why it has this shape

**Only emit when a compaction actually runs.** `_auto_compaction_plan()` returns
`None` when the context is below the threshold, or when the whole context fits in
`keep_recent_tokens` (so there is no older prefix worth summarizing). Emitting
unconditionally would produce a start/end pair on every turn, which is noise for
extensions and would make the TUI flash a status row that never did anything.

**Reuse the overflow path's work.** `_run_compaction_plan()` is the shared body
that both `_try_auto_compact()` and `_try_overflow_compact()` call. The overflow
path already emitted events; splitting the check from the work let the threshold
path emit its own events without duplicating the compaction logic.

**Emit `aborted=True` on failure.** Automatic compaction is best-effort: a
summarization failure is logged and the turn continues with the original context.
Without an end event the TUI would leave the status row on screen forever, so the
failure path emits `CompactionEndEvent(aborted=True, error_message=...)` and the
adapter rewrites the row in place. The turn still succeeds.

**No extra turn-finished notification.** The enclosing turn owns
`AgentSettledEvent` (see #504). Threshold compaction happens inside that turn, so
the TUI notifies once. The new `state.compacting` flag only feeds `_is_working()`,
which drives the spinner, footer mode, and window title.

**`reason="auto"` was not used.** `CompactionReason` already had `"threshold"`,
and it is more precise than a generic "auto" for consumers that want to
distinguish a threshold compaction from an overflow recovery.

## What is out of scope

- `reason="manual"` events from `compact_detailed()` / `compact()`. Manual
  `/compact` is driven by the CLI frontend, which already renders its own
  `Compacting session…` status and fires its own turn-finished notification.
  Emitting session events there would duplicate that UI.
- Mid-turn threshold checks between tool calls. The three existing check points
  (before a prompt, after a turn, after a context-overflow error) are unchanged.
- Scaling `DEFAULT_COMPACTION_KEEP_RECENT_TOKENS`.

## How to test

```bash
uv run pytest tests/test_coding_session.py tests/test_tui_adapter.py tests/test_tui_app.py -q
```

The new tests cover:

- `test_session_auto_compaction_emits_threshold_events` — start and end events
  are emitted with `reason="threshold"` when the estimate crosses the threshold.
- `test_session_auto_compaction_emits_no_events_below_threshold` — no events when
  the context fits.
- `test_session_auto_compaction_failure_emits_aborted_end_event` — a summarization
  failure emits `aborted=True` with an error message and does not raise.
- `test_tui_adapter_shows_threshold_compaction_status` — the status row appears
  and is removed on success.
- `test_tui_adapter_keeps_threshold_compaction_failure_visible` — the row is
  rewritten to the failure message.
- `test_tui_app_notifies_once_for_turn_with_threshold_compaction` — one
  turn-finished notification, not two.
- `test_tui_app_surfaces_threshold_compaction_failure` — the failure is visible
  in the transcript.

To see it by hand, run a session with a low threshold and a long conversation:

```bash
tau --auto-compact-threshold 2000
```

The transcript shows `… Compacting context` after the answer, then the row
disappears.
