---
title: "Fix: Ctrl+T thinking toggle during streaming broke smooth scroll-follow"
---

# Fix: Ctrl+T thinking toggle during streaming broke smooth scroll-follow

This is a bug fix (re-regression of #175) for the interactive TUI. It does not
touch the core agent harness.

## Symptom

While an assistant message was streaming, pressing **Ctrl+T** to toggle
thinking-token display stopped the smooth bottom-following scroll that #175 / PR
#177 had restored. After the toggle the transcript either stopped auto-following
or jumped/snapped instead of gliding, and only recovered after the turn ended and
the transcript was rebuilt.

## Root cause

The scroll-follow work in #177 relies on the transcript being updated
**incrementally** during streaming: per-delta `append_assistant_delta` /
`append_thinking_delta`, with `call_after_refresh(scroll_end(...))` on the
existing `StreamingTranscriptMessageWidget` and no remount.

`action_toggle_thinking` ignored that and always called `_refresh()` ->
`TranscriptView.update_from_state()` -> `_redraw()`, which does a full
`remove_children()` + remount of every `TranscriptMessageWidget` /
`StreamingTranscriptMessageWidget`. During an active turn that:

1. Tears down `_active_assistant_widget` / `_active_thinking_widget` (reset to
   `None`), so the next streamed delta mounts a fresh assistant widget (visible
   jump / lost incremental markdown stream).
2. Reflows the whole transcript. The transient content-height change moves
   `scroll_y`, and `TranscriptView.watch_scroll_y` interprets that as a user
   scrollback and flips `_follow_output = False`, snapping the viewport.

This is exactly the "scroll state reset during token deltas" failure mode #175
warned about. The earlier "Reduce transcript toggle latency" change
(`ae42fd0f`) optimized the toggle for the *idle* transcript and did not account
for the toggle firing mid-stream.

## Fix

Keep the full `_refresh()` redraw for the idle case (toggle pressed while no turn
is streaming), where it is safe. While the agent is running (`state.running`),
toggle thinking **incrementally** without a full remount:

- `TranscriptView.apply_thinking_visibility(state, theme)` reconciles only the
  thinking-related widgets against the `state.items` thinking blocks.
- The active assistant streaming widget is never thinking-role, so it is left
  intact and keeps receiving deltas through the same widget instance.
- **Showing**: remove all thinking widgets (both finalized `TranscriptMessageWidget`
  blocks and live `StreamingTranscriptMessageWidget` blocks, including the
  hidden-thinking placeholder), then mount a fresh widget for each `thinking`
  item in `state.items`, ordered ahead of the active assistant widget.
  `StreamingTranscriptMessageWidget(item)` re-renders the full `item.text` on
  mount, and the next thinking delta continues appending to the freshly assigned
  active widget.
- **Hiding**: remove all thinking widgets and, if a thinking item exists, mount a
  single hidden-thinking placeholder ahead of the assistant widget.

To keep the scroll-follow state stable across the reflow, `apply_thinking_visibility`
snapshots `_follow_output`, sets a `_suppress_follow_update` guard so
`watch_scroll_y` ignores layout-induced scroll movement during the reconcile, and
restores `_follow_output` + re-requests the bottom-follow scroll from a
`call_after_refresh` callback that runs after layout settles. This means a
mid-stream toggle does not change follow mode: it stays following when the user
was at the bottom, and stays put when the user had scrolled up.

`action_toggle_thinking` in `src/tau_coding/tui/app.py` chooses the path:

```python
self.state.toggle_thinking()
if self.state.running and self.screen_stack:
    transcript.apply_thinking_visibility(self.state, theme=...)
    self._refresh_chrome()
    return
self._refresh()
```

The reconcile is synchronous (fire-and-forget `mount` / `remove_children`,
matching `_redraw`) so it stays atomic with respect to the streaming worker and
does not introduce worker concurrency.

## How to test

Regression tests in `tests/test_tui_app.py`:

- `test_tui_thinking_toggle_during_stream_preserves_follow_scroll` — pinned to
  the bottom, toggle on/off mid-stream keeps following smoothly and keeps the
  assistant streaming widget.
- `test_tui_thinking_toggle_during_stream_preserves_user_scrollback` — after
  scrolling up, the toggle does not yank the viewport back to the bottom.
- `test_tui_thinking_toggle_during_stream_keeps_all_thinking_blocks` — multiple
  thinking blocks across tool calls all re-render on show and collapse to one
  placeholder on hide.
- `test_tui_thinking_toggle_via_event_stream_preserves_assistant_widget` —
  end-to-end through `_apply_streaming_transcript_event`, the assistant widget
  survives the toggle and keeps receiving deltas.

Run them with:

```bash
uv run pytest tests/test_tui_app.py -k "thinking_toggle_during_stream or thinking_toggle_via_event_stream"
```

## Related

- Issue #175 — "Fix TUI scrollback regression while assistant messages stream" (fixed by PR #177).
- Key commits: `ae42fd0f` "Reduce transcript toggle latency",
  `cb488805`/`a5dff600`/`19e4c498` (#177 scrollback fix).