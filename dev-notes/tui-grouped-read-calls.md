# Grouped read calls in the TUI

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

`Ctrl+O` expands the group into every exact read invocation without repeating
previews of the file contents. The model already receives each complete result;
the expanded TUI stays focused on which files were read. A single read keeps its
existing row and result behavior. Reads separated by another tool, text block,
or assistant response are not grouped. Skill-file reads retain their special
skill presentation.

Adjacent built-in `edit` calls use the same presentation: `Editing N files`
becomes `Edited N files`, with every edited path listed below. Expanding an edit
group preserves each invocation and result, unlike read groups whose file-content
results stay suppressed.

## Architecture

Grouping is display-only in `src/tau_coding/tui/`. `TuiEventAdapter` assigns a
presentation batch identifier to tool calls from one completed assistant message.
`TuiState` keeps each grouped call's ID, arguments, progress, result, and timing,
while exposing one aggregate read row. That row can stand alone or live inside a
larger mixed-tool batch. Every call ID maps back to its top-level item, so live
updates continue to use O(1) lookup and refresh the existing Textual widget in
place. Results still determine aggregate progress and error styling, but the
widget suppresses their content when rendering a grouped row.

Restored canonical messages use the same assistant-message boundary to rebuild
the group deterministically. Agent events, tool execution, provider payloads,
and session JSONL remain unchanged. Existing custom call renderers are applied
to each invocation when a group is expanded.

Only built-in `read` and `edit` calls use file grouping. Shell commands and
extension tools retain their own presentation semantics.

## Tests

- `tests/test_tui_adapter.py` covers restored read/edit groups, path lists,
  call-ID lookup, expanded invocations/results, and assistant-message boundaries.
- `tests/test_tui_app.py` covers live grouping, in-place progress updates,
  completion, and `Ctrl+O` expansion in Textual.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```
