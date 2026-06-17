# Phase 17.5: TUI Transcript Wrapping

Phase 17.5 hardens the Textual transcript surface so it behaves more like a
minimal chat stack.

The implementation lives in:

```text
src/tau_coding/tui/app.py
src/tau_coding/tui/widgets.py
```

## What was added

Transcript items now render as standalone colored blocks instead of prefixed
lines such as `you:` or `assistant:`.

The display state remains simple:

```python
ChatItem(role="user", text="...")
```

Only the Textual renderer decides how to display that item. This preserves the
core boundary:

```text
CodingSession emits events
TuiEventAdapter builds display state
TranscriptView renders blocks
```

## Wrapping behavior

`TranscriptView` still uses Textual's `RichLog`, but it is created with:

```python
min_width=1
wrap=True
```

This removes RichLog's default wide minimum width and lets normal user and
assistant messages reflow to the available terminal width. Chat block bodies use
Rich `Text` with folded overflow so long unbroken strings are wrapped inside the
block instead of forcing horizontal scrolling.

Tool output and code-like text preserve line breaks. Very long unbroken chunks
are folded intentionally rather than clipped.

Later Phase 23 polish keeps the same display-state shape but renders fenced
code blocks and edit patches with Rich syntax renderables inside the block.
Malformed or inline fences fall back to plain text so transcript content is not
lost.

## Visual model

Each role gets a distinct dark block style:

- user
- assistant
- tool
- status
- error

The role is expressed by color, not by an inline label. This keeps the TUI
minimal while still making transcript structure scannable.

## Tests

The phase is covered by:

```text
tests/test_tui_app.py
```

The tests verify:

- chat items render without `you:`, `assistant:`, or `tool:` prefixes
- long unbroken message text folds within a narrow console width
- the mounted transcript uses a narrow `min_width`
