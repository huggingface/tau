# Batched tool calls in the TUI

Assistant responses frequently contain several adjacent tool calls. Even after
read calls gained their own compact grouping, rendering each remaining call as a
separate transcript message repeated the same border, padding, and vertical
spacing for one logical burst of work.

## What changed

Adjacent built-in tool calls from one assistant response now share one transcript
message. Each logical action remains one line:

```text
Doing thing one
Doing thing two
Read 2 files
  - a.py
  - b.py
Doing something else
```

Each line keeps its own running, success, or failure color on the semantic
description. Commands, arguments, and paths remain neutral. Adjacent reads still
collapse into file-list rows inside the larger batch, as do adjacent edits and
writes.

`Ctrl+O` expands every row using its tool-specific behavior. Bash rows retain
their description and show the exact command and result beneath it. Grouped reads
expand to individual read invocations without repeating file-content previews;
grouped edits and writes retain each invocation and result. Batch invocations and
results remain one selectable plain-text surface.

Batches never cross assistant text, thinking blocks, skill loads, or unrelated
assistant responses. A narrow exception joins consecutive completed `edit` or
`write` calls from same-tool model continuations, matching providers that
serialize file mutations one at a time. Only the known `bash`, `read`, `edit`,
and `write` tools are eligible; extension tools remain separate so custom call or
result cards are never
flattened into generic text rows.

## Architecture

This remains a TUI-only projection. `TuiEventAdapter` assigns one presentation
batch identifier to each contiguous tool-call run in an assistant message.
`TuiState` stores one parent `ChatItem` with structured child rows, while every
underlying tool-call ID continues to map to the parent for O(1) live updates. A
child row may itself own a grouped-read call list.

The transcript widget renders child rows as one Rich `Text` value with status
spans per child rather than a `Group` of separate renderables. This preserves
Textual's drag selection across lines while retaining independent colors.
Expansion and selection text are derived from the same structured children.
Provider payloads,
agent events, execution order, canonical messages, and session JSONL are
unchanged.

## Tests

- `tests/test_tui_adapter.py` covers restored mixed-tool batches, nested read
  groups, and call-ID lookup.
- `tests/test_tui_app.py` covers one-widget rendering, expansion, bash results,
  and suppressed grouped-read content.

Run:

```bash
uv run pytest tests/test_tui_adapter.py tests/test_tui_app.py
```
