# Phase 23: Advanced TUI and Product Polish

Phase 23 improves the Textual frontend while keeping the reusable agent harness
independent of UI concerns.

The boundary remains:

```text
CodingSession emits AgentEvent values
        ↓
TuiEventAdapter updates TuiState
        ↓
Textual widgets render the transcript and controls
```

## Current polish slices

Live tool results now render successful output in the transcript, matching
restored session history. This keeps tool-call blocks useful during an active
run instead of hiding successful command output until the session is reloaded.

The TUI also has a command-palette entry point. Pressing `Ctrl+K` focuses the
prompt, inserts `/`, and shows all slash-command completions using the existing
completion engine. Selection still uses the same `Tab`, `Up`, and `Down`
bindings as ordinary slash-command autocomplete.

## Boundaries

These changes live in `tau_coding.tui`. The command registry still owns command
metadata, and `tau_agent` remains unaware of Textual, keybindings, slash
commands, and rendering.

## Still deferred

The larger Phase 23 roadmap still includes richer model/session pickers, a diff
viewer, configurable keybindings, and deeper theme polish. Those should remain
separate atomic slices.

## Tests

Coverage lives in:

```text
tests/test_tui_adapter.py
tests/test_tui_app.py
```
