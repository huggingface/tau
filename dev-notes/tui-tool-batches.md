# Batched tool calls in the TUI

Assistant responses frequently contain several adjacent tool calls. Even after
read calls gained their own compact grouping, rendering each remaining call as a
separate transcript message repeated the same border, padding, and vertical
spacing for one logical burst of work.

## What changed

Adjacent built-in tool calls from one assistant response now share one transcript
message. Each logical action remains one line:

```text
Doing thing one     · $ command one
Doing thing two     · $ command two
Read 5 files        · a.py, b.py, c.py, +2
Doing something else · $ command three
```

Each line keeps its own running, success, or failure color on the semantic
description. Commands, arguments, and paths remain neutral. Adjacent reads still
collapse into one read row inside the larger batch.

`Ctrl+O` expands every row using its tool-specific behavior. Bash rows recover
the exact command and show their result. Grouped reads expand to individual read
invocations without repeating file-content previews. Patch results retain their
existing diff rendering.

Batches never cross assistant text, thinking blocks, model continuations, skill
loads, or separate assistant responses. Calls with custom call-card rendering
remain separate so an extension's layout is not flattened into a text row.

## Architecture

This remains a TUI-only projection. `TuiEventAdapter` assigns one presentation
batch identifier to each contiguous tool-call run in an assistant message.
`TuiState` stores one parent `ChatItem` with structured child rows, while every
underlying tool-call ID continues to map to the parent for O(1) live updates. A
child row may itself own a grouped-read call list.

The transcript widget renders child rows inside one message and computes status
color per child rather than assigning one color to the container. Expansion and
selection text are also derived from the structured children. Provider payloads,
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
