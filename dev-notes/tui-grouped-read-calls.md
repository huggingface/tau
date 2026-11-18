# Grouped file calls in the TUI

Models often request several files in one assistant response. Rendering every
batched `read` as a separate collapsed row made exploration-heavy turns noisy,
even though the calls formed one logical batch.

## What changed

The TUI now combines adjacent `read` calls from the same assistant message into
one presentation group with every path listed below its headline:

```text
→ Reading 4 files
  - tools.py
  - state.py
  - widgets.py
  - adapter.py
```

As results arrive, the row reports aggregate progress such as `2/4 complete`.
Once all calls finish it changes to `Read 4 files`; if any call failed, the row
also reports the failure count and uses the existing error styling. The aggregate
description and progress carry the running/success/failure color, while every
file path stays in the neutral tool-body color.

`Ctrl+O` expands the group into every exact read invocation, and each member now
shows its previewed result (eight lines, with the usual `Preview only` note when
the file is longer). The model already receives each complete result; the expanded
TUI shows the same short preview a single read row shows. A single read keeps its
existing row and result behavior. Reads separated by another tool, visible
assistant text or thinking, or a failed call are not grouped. Skill-file reads
retain their special skill presentation.

Adjacent built-in `edit` and `write` calls use the same presentation: `Editing N
files` becomes `Edited N files`, while `Writing N files` becomes `Written N files`.
Every affected path is listed below. Expanding a group preserves each invocation
and result for read, edit, and write alike. Consecutive same-tool responses also
join the same group as long as neither carries visible prose or thinking; this
covers providers that emit one call, wait for its result, then emit the next. Any
visible assistant text or thinking, a different tool, or a failed call ends the
group, and a group stops growing after eight rows.

## Architecture

Grouping is display-only in `src/tau_coding/tui/`. `TuiEventAdapter` assigns a
presentation batch identifier to tool calls from one completed assistant message.
`TuiState` keeps each grouped call's ID, arguments, progress, result, and timing,
while exposing one aggregate file row. That row can stand alone or live inside a
larger mixed-tool batch. Every call ID maps back to its top-level item, so live
updates continue to use O(1) lookup and refresh the existing Textual widget in
place. Results still determine aggregate progress and error styling, and all file
results remain available on expansion.

Restored canonical messages rebuild groups deterministically. Groups may span
consecutive same-tool narration-free responses when each preceding call completed
successfully; assistant text, thinking, another tool, or a failure ends the group.
Eligibility comes from the canonical assistant block sequence rather than
transient display adjacency, so live and restored sessions make the same grouping
decision. Agent events, tool execution, provider payloads, and session JSONL
remain unchanged. Existing custom call renderers are applied to each invocation
when a group is expanded.

Only built-in `read`, `edit`, and `write` calls use file grouping. Shell commands
and extension tools retain their own presentation semantics.

## Tests

- `tests/test_tui_adapter.py` covers restored read/edit/write groups, serialized
  same-tool continuations, text boundaries, path lists, call-ID lookup, and
  expansion.
- `tests/test_tui_app.py` covers live grouping, in-place progress updates,
  completion, `Ctrl+O` expansion, run boundaries (prose, thinking, failure), the
  row cap, and whitespace-only block handling in Textual.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```
