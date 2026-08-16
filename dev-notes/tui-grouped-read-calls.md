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

`Ctrl+O` expands the group into every exact read invocation without repeating
previews of the file contents. The model already receives each complete result;
the expanded TUI stays focused on which files were read. A single read keeps its
existing row and result behavior. Reads separated by another tool, text block,
or assistant response are not grouped. Skill-file reads retain their special
skill presentation.

Adjacent built-in `edit` and `write` calls use the same presentation: `Editing N
files` becomes `Edited N files`, while `Writing N files` becomes `Written N files`.
Every affected path is listed below. Expanding edit and write groups preserves
each invocation and result, unlike read groups whose file-content results stay
suppressed. Consecutive edit-only or write-only model continuations also join the
same group; this covers providers that emit one mutation, wait for its result,
then emit the next. Assistant text or thinking still ends the group.

## Architecture

Grouping is display-only in `src/tau_coding/tui/`. `TuiEventAdapter` assigns a
presentation batch identifier to tool calls from one completed assistant message.
`TuiState` keeps each grouped call's ID, arguments, progress, result, and timing,
while exposing one aggregate file row. That row can stand alone or live inside a
larger mixed-tool batch. Every call ID maps back to its top-level item, so live
updates continue to use O(1) lookup and refresh the existing Textual widget in
place. Results still determine aggregate progress and error styling. Read-result
contents stay suppressed, while edit and write results remain available on
expansion.

Restored canonical messages rebuild groups deterministically. Read groups retain
assistant-message boundaries. Edit and write groups may additionally span
consecutive same-tool model continuations when each preceding mutation completed;
assistant text, thinking, or another tool ends the group. Eligibility comes from
the canonical assistant block sequence rather than transient display adjacency,
so live and restored sessions make the same grouping decision. Agent events, tool
execution, provider payloads, and session JSONL remain unchanged. Existing custom
call renderers are applied to each invocation when a group is expanded.

Only built-in `read`, `edit`, and `write` calls use file grouping. Shell commands
and extension tools retain their own presentation semantics.

## Tests

- `tests/test_tui_adapter.py` covers restored read/edit/write groups, serialized
  edit/write continuations, text boundaries, path lists, call-ID lookup, and
  expansion.
- `tests/test_tui_app.py` covers live grouping, in-place progress updates,
  completion, and `Ctrl+O` expansion in Textual.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```
