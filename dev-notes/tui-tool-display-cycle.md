# Cycled tool display in the TUI (summary / calls / expanded)

Long agent turns can produce dozens of tool rows. Even with batching and file
grouping, a scrolled-back transcript is mostly tool plumbing when all you want
is the narrative: what the model said and what it accomplished. This phase adds
a third, most-compact display mode and turns `Ctrl+O` into a three-way cycle.

## What changed

`Ctrl+O` now cycles through three modes, announced in a toast:

1. **Summary** — while the agent works, tool rows render as live compact call
   lines so progress stays visible; when the turn settles, each contiguous burst
   representing two or more calls collapses into one line: `Worked for 1m 23s ·
   5 tool calls`. A single tool call keeps its own row. A burst left pending
   (e.g. after a cancel) shows `Running… 2/5 tool calls` instead, and failures
   are counted (`· 1 failed`). Assistant text, thinking, user messages, terminal
   `!` commands, and extension-rendered tool cards are never collapsed. Starting
   a new turn expands the rows again.
2. **Calls** — the previous default: compact per-call lines and batch groups
   without result contents.
3. **Expanded** — call lines plus exact commands and result previews.

Cycling is global; every burst in the visible transcript collapses together.
Burst boundaries are any non-tool item, so assistant prose between tool
activity stays in place.

## How it works

- `TuiState.tool_display` (`summary | calls | expanded`) replaces the old
  `show_tool_results` bool. The bool survives as a property (`expanded` iff
  mode is `expanded`) so the roughly twenty per-row rendering call sites in
  `app.py`/`widgets.py` keep working unchanged. `cycle_tool_display()` advances
  the mode.
- Tool rows record `finished_at` when their result arrives (`started_at` is no
  longer cleared on completion; `_refresh_tool_batch` now detects pending rows
  via `tool_result_text is None`). Restored sessions set `_restoring` while
  projecting history, so restored rows carry no invented timing and their
  summaries show the call count only.
- `format_tool_run_summary()` flattens batch heads, grouped file calls, and
  standalone rows into "leaves" and renders one line. Sub-second running
  elapsed is suppressed (same 1s threshold as the per-row timer).
- `TranscriptView` owns collapse rendering. `_display_rows()` projects the
  windowed items, replacing runs of collapsible tool items with one synthetic
  `ChatItem` (the hidden-thinking-placeholder pattern) — but only when the turn
  has settled (`_collapsing()` requires `not state.running`) and only when the
  run represents two or more calls (`tool_run_leaves()` counts batch/group
  members); a single tool call keeps its own row. `_item_widgets` maps every
  member to the summary widget, so live `update_item` calls (tool results, the
  1s elapsed timer, extension updates) recompute the summary line in place with
  no remount. `append_item` folds new tool rows into the previous row's summary
  — converting a lone row into a summary when a second call arrives — starting
  a new one only after a non-tool item.
- Turn boundaries drive the collapse/expand transitions in summary mode:
  `AgentStartEvent` rebuilds the window expanded, and `AgentEndEvent` /
  `AgentSettledEvent` rebuild it collapsed; `_refresh()` paths pick the right
  shape automatically from `state.running`.
- Entering or leaving summary mode rebuilds the mounted window
  (`set_tool_display`); `calls` ↔ `expanded` still update rows in place via
  `update_tool_results_visibility`, preserving widget identity for tests and
  avoiding flicker.
- Collapsibility is decided by `_is_collapsible_tool`: `role == "tool"`, not
  `always_show_tool_result`, and no extension `render_call` for the tool (or
  any of its batch/group members). Custom-rendered tools keep their dedicated
  widgets in every mode.

## Testing

- Unit tests cover `cycle_tool_display`, the `show_tool_results` property,
  custom-render detection, and `format_tool_run_summary` (durations, failures,
  running progress, batch leaves, restored rows without timing).
- Pilot tests cover the keybinding cycle, restored-history collapse, and live
  streaming through `Running…` to `Worked for …` with exactly one summary
  widget per run.

## Docs

`website/content/guides/tui.md` documents the three modes and the
never-collapsed exceptions; `website/content/reference/keybindings.md` updates
the `Ctrl+O` entry.
