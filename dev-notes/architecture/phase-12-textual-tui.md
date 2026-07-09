---
title: "Phase 12: Textual TUI"
---

Phase 12 adds Tau's first interactive terminal UI using Textual.

The implementation lives in:

```text
src/tau_coding/tui/
```

## What was added

Tau now has a minimal interactive TUI that:

- uses `CodingSession` rather than raw `AgentHarness`
- accepts user prompts through a Textual input widget
- streams `AgentEvent` values into TUI display state
- displays assistant messages, tool events, status messages, and errors
- shows a dark-mode session sidebar with model, cwd, tools, skills, and prompt templates
- restores previous session messages into the visible transcript
- supports existing `/help` and `/exit` command handling
- supports Escape to request cancellation
- stores the early default TUI session at `.tau/sessions/default.jsonl`

The CLI opens the TUI by default:

```bash
tau
```

Global options can be passed before starting the TUI:

```bash
tau --model gpt-4.1-mini --cwd /path/to/project
```

## Why this exists

Pi separates the reusable agent layer from terminal UI concerns:

```text
agent/session emits events
terminal frontend consumes events
TUI components render display state
```

Tau now follows that same boundary with Python-native Textual:

```text
CodingSession.prompt()
  emits AgentEvent
      ↓
TuiEventAdapter
  updates TuiState
      ↓
TauTuiApp
  renders Textual widgets
```

`tau_agent` still has no dependency on Textual, Rich, Typer, or terminal UI behavior.

## TUI state adapter

The adapter layer is intentionally separate from Textual:

```text
src/tau_coding/tui/state.py
src/tau_coding/tui/adapter.py
```

`TuiState` stores display-only state:

- transcript items
- current assistant streaming buffer
- running flag
- latest error

`TuiEventAdapter` applies portable `AgentEvent` values to that state.

This makes event-to-display behavior testable without launching a terminal app.

## Textual app

The Textual app is intentionally minimal:

```text
src/tau_coding/tui/app.py
src/tau_coding/tui/widgets.py
```

It uses:

- `Header`
- `Footer`
- `Static` for status and sidebar content
- `Horizontal` and `Vertical` containers for the sidebar/main layout
- a scrollable transcript made from selectable message widgets
- `Input` for prompt submission

Prompt execution runs in a Textual worker so the UI can continue updating while events stream.

## Session storage

Phase 12 adds a temporary default local session path helper:

```python
def default_session_path(cwd: Path) -> Path:
    return cwd / ".tau" / "sessions" / "default.jsonl"
```

This is intentionally simple. Later phases can replace it with a richer Pi-style session manager and picker.

## Current limitations

The TUI is a foundation, not a full Pi-level interface yet. It does not include:

- session picker
- model picker
- command palette
- tree navigation
- diff viewer
- markdown rendering
- theme customization
- keybinding configuration
- extension UI hooks
- compaction UI

## Tests

The phase is covered by:

```text
tests/test_tui_adapter.py
tests/test_cli.py
```

Tests focus on the pure adapter and CLI wiring. Full terminal interaction testing is deferred until the TUI surface stabilizes.

## Next phase

The next phase can expand the TUI incrementally with one of:

- session selection and resume support
- richer transcript rendering
- command palette and slash-command registry
- extension hooks
- compaction/context management UI
