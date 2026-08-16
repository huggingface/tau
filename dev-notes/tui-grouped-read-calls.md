# Grouped read calls in the TUI

Models often request several files in one assistant response. Rendering every
batched `read` as a separate collapsed row made exploration-heavy turns noisy,
even though the calls formed one logical batch.

## What changed

The TUI now combines adjacent `read` calls from the same assistant message into
one presentation row:

```text
→ Reading 4 files · tools.py, state.py, widgets.py, +1
```

As results arrive, the row reports aggregate progress such as `2/4 complete`.
Once all calls finish it changes to `Read 4 files`; if any call failed, the row
also reports the failure count and uses the existing error styling. The aggregate
description and progress carry the running/success/failure color, while file paths
stay in the neutral tool-body color. At most three paths are previewed, and long
paths retain their filename-bearing suffix.

`Ctrl+O` expands the group into every exact read invocation without repeating
previews of the file contents. The model already receives each complete result;
the expanded TUI stays focused on which files were read. A single read keeps its
existing row and result behavior. Reads separated by another tool, text block,
or assistant response are not grouped. Skill-file reads retain their special
skill presentation.

## Architecture

Grouping is display-only in `src/tau_coding/tui/`. `TuiEventAdapter` assigns a
presentation batch identifier to tool calls from one completed assistant message.
`TuiState` keeps each grouped call's ID, arguments, progress, result, and timing,
while exposing one aggregate `ChatItem`. Every call ID maps back to that item, so
live updates continue to use O(1) lookup and refresh the existing Textual widget
in place. Results still determine aggregate progress and error styling, but the
widget suppresses their content when rendering a grouped row.

Restored canonical messages use the same assistant-message boundary to rebuild
the group deterministically. Agent events, tool execution, provider payloads,
and session JSONL remain unchanged. Existing custom call renderers are applied
to each invocation when a group is expanded.

The first version intentionally groups only built-in `read` calls. Mutating tools
and shell commands remain separate so consequential actions are never hidden in
an aggregate row. Other read-only tools can adopt the same presentation model
later once their argument previews and extension behavior are defined.

## Tests

- `tests/test_tui_adapter.py` covers restored groups, call-ID lookup, expanded
  invocations/results, and assistant-message boundaries.
- `tests/test_tui_app.py` covers live grouping, in-place progress updates,
  completion, and `Ctrl+O` expansion in Textual.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```
