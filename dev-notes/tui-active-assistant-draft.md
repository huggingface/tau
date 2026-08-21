# TUI: durable transcript history vs. the active assistant draft

This note explains how the interactive TUI represents an in-flight assistant
response, why that representation is separate from durable transcript history,
and how it maps to Pi's design.

## The problem this design solves

Older Tau versions split one live assistant turn across three kinds of state:

- streamed answer text in `TuiState.assistant_buffer`;
- streamed thinking inserted directly into `TuiState.items`;
- "which widget is live" pointers on `TranscriptView`.

Any transcript rebuild during a response (Ctrl+T, slash commands, terminal
resize, theme changes — anything calling `TauTuiApp._refresh()`) reconstructed
the provisional thinking rows as ordinary history and dropped the live widget
pointers. The next streamed delta then mounted a *second* live widget, and
message completion removed only the copy it still knew about, permanently
duplicating thinking or answer text on screen. The session file was always
correct; this was purely a TUI projection/ownership bug.

## The state model

`TuiState` now has exactly two representations of assistant output:

- **Durable history** — `TuiState.items`, a list of finalized `ChatItem`s.
  Nothing provisional ever goes in here.
- **The active draft** — `TuiState.active_assistant`, an
  `ActiveAssistantDraft(stream_id, message)`. This is the *only* provisional
  source of truth for a streaming response.

The draft holds the latest *cumulative* `AssistantMessage` snapshot, not an
accumulation of deltas. Tau's event protocol already guarantees that
`MessageUpdateEvent.message` is the complete partial message so far (providers
deep-copy it per event; see `src/tau_ai/stream.py::_snapshot`). Deltas are only
a rendering optimization; the cumulative message is semantic truth. This
matters because a redraw can land *between* the adapter update and the widget
update in either order — with a cumulative snapshot both orders converge, while
delta concatenation would double-append.

State transitions are lifecycle methods, not field pokes:

- `begin_assistant(message)` — new draft, new monotonically increasing
  `stream_id`. Defensively interrupts any unfinished previous draft first.
- `update_assistant(message)` — adopt the newest cumulative snapshot.
- `finish_assistant(message)` — clear the draft and project the canonical
  final blocks into `items` exactly once.
- `discard_assistant()` — clear without projecting (used for error/aborted
  ends, where the terminal message itself is projected via
  `add_assistant_error`, and for deferred overflow errors).
- `interrupt_assistant()` — project the *entire* partial turn (thinking and
  text alike) into `items`, then clear. Used by cancellation and by flushes
  when a terminal event never arrived. One policy for the whole turn: partial
  output is retained, matching the aborted message the session file records.

## The view: widgets are a projection, never the only copy

`TranscriptView` renders two things, in order:

1. the bounded window of durable `state.items`;
2. if the window is at the latest end, the *active tail* — the draft projected
   into ordered blocks by the pure helper
   `project_active_assistant_blocks(message, show_thinking, stream_id)`.

The projection preserves provider content order (`thinking → text → thinking`
stays interleaved), omits empty blocks, ignores tool calls (they render as tool
execution rows), and collapses each contiguous run of thinking blocks into one
placeholder when thinking is hidden — the same rules Pi applies in
`AssistantMessageComponent.updateContent`.

Every mounted tail widget is owned by one `_ActiveAssistantRender`, keyed by
`(kind, stream_id, content_index)`. Because a full `_redraw()` rebuilds this
owner from `state.active_assistant`, **a redraw reconstructs live ownership**
instead of orphaning live rows — the core fix.

Incremental updates go through one semantic operation,
`sync_active_assistant(draft, changed_content_index, …)`:

- same block topology → write only the missing text suffix through Textual's
  `MarkdownStream` (no full reparse per delta);
- a redraw already rendered the suffix → no-op (text is equal);
- the provider corrected content → replace the widget text outright;
- additive topology growth (a new block started) → mount only the new blocks;
- anything else (stream replacement, visibility flip, corrections that remove
  blocks) → rebuild the whole tail from the projection.

Completion (`finish_active_assistant`) claims the render before its first
`await`, then either finalizes and rebinds the owned widgets in place onto the
canonical `ChatItem`s (the common case, preserving widget identity and
selection for unrelated history) or removes only the owned widgets and mounts
canonical rows idempotently (`append_item` and the mount path both refuse to
mount an item that already has a mounted widget).

## Stale async work

Synchronous rebuilds (`_redraw`, Ctrl+T, tail rebuilds) each bump
`_render_generation`. Async operations capture the generation before awaiting
and stop after any await if it changed — the synchronous rebuild that bumped it
already rendered current state, so the stale continuation must not mount,
remove, or re-register anything. Combined with the per-stream `stream_id`,
this means work started for an older render or an older assistant stream can
never resurrect removed output. Writes that land on an already-unmounted
widget are harmless because the widget is detached from the DOM.

## Mapping to Pi

Pi keeps one `streamingMessage` (cumulative partial `AssistantMessage`) plus
one `streamingComponent` for the whole turn: `message_start` creates the
component, every `message_update` re-renders it from the cumulative snapshot,
`message_end` updates it from the authoritative final message, and a chat
rebuild re-adds the component after durable history
(`interactive-mode.ts`, `assistant-message.ts`). Tau follows the same
ownership model — `ActiveAssistantDraft` ↔ `streamingMessage`,
`_ActiveAssistantRender` ↔ `streamingComponent` — while keeping Tau-specific
behavior: a bounded transcript window, individually selectable per-block
widgets, and incremental `MarkdownStream` suffix writes instead of full
re-renders per update.

## Testing

- `tests/test_tui_adapter.py` covers the draft lifecycle (begin/update/finish,
  corrections, interruption, replacement streams, overflow deferral).
- `tests/test_tui_app.py` covers redraw/Ctrl+T/slash-command/theme/resize
  during streaming, interleaved block ordering, hidden-run placeholders,
  cancellation and abort boundaries, windowed scrollback, and deterministic
  race tests that gate `MarkdownStream.write`/`stop` with `asyncio.Event` to
  interleave redraws at exact await points.
